"""Operational event model, projection, and API tests."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.monitor.models import OperationalEvent
from apps.monitor.services.events import (
    cleanup_operational_events,
    record_operational_event,
    schedule_availability_event,
    schedule_repository_health_event,
)
from apps.task.models import Task
from apps.task.services.interface import complete_task
from apps.task.signals import task_failed, task_timed_out


class OperationalEventApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="events@test.local",
            email="events@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="events-org", name="Events Org")
        self.other_org = Organization.objects.create(
            key="other-events-org",
            name="Other Events Org",
        )
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)

    def create_event(self, **overrides) -> OperationalEvent:
        values = {
            "organization_id": self.org.id,
            "event_type": "node.offline",
            "category": OperationalEvent.Category.INFRASTRUCTURE,
            "severity": OperationalEvent.Severity.WARNING,
            "title": "Agent is offline",
            "resource_type": "agent",
            "resource_id": "42",
            "resource_name": "agent-42",
        }
        values.update(overrides)
        return record_operational_event(**values)

    def test_events_require_authentication_and_organization_membership(self):
        anonymous = APIClient()
        response = anonymous.get(
            "/api/v1/monitors/events/",
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.get(
            "/api/v1/monitors/events/",
            HTTP_X_ORG_KEY=self.other_org.key,
            secure=True,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_events_are_org_scoped_and_include_matching_stats(self):
        self.create_event()
        self.create_event(
            event_type="source.online",
            severity=OperationalEvent.Severity.INFORMATION,
            title="Source is online",
            resource_name="source-a",
        )
        self.create_event(
            organization_id=self.other_org.id,
            event_type="node.offline",
            severity=OperationalEvent.Severity.CRITICAL,
            title="Other tenant event",
        )

        response = self.client.get(
            "/api/v1/monitors/events/",
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            response.data["stats"],
            {"total": 2, "critical": 0, "warning": 1, "information": 1},
        )
        self.assertNotIn(
            "Other tenant event",
            {event["title"] for event in response.data["results"]},
        )

    def test_events_support_period_search_category_and_severity_filters(self):
        self.create_event(details="Connection timed out")
        self.create_event(
            event_type="task.failed",
            category=OperationalEvent.Category.PROTECTION,
            severity=OperationalEvent.Severity.CRITICAL,
            title="Backup failed",
            resource_name="daily backup",
        )
        self.create_event(
            event_type="node.offline",
            title="Old event",
            occurred_at=timezone.now() - timedelta(days=8),
        )

        response = self.client.get(
            "/api/v1/monitors/events/",
            {
                "period": "7d",
                "search": "backup",
                "category": "protection",
                "severity": "critical",
            },
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Backup failed")

    def test_duplicate_event_key_is_idempotent_within_org(self):
        first = self.create_event(dedup_key="node:42:offline:1")
        second = self.create_event(
            dedup_key="node:42:offline:1",
            title="Duplicate title",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(OperationalEvent.objects.count(), 1)

    def test_event_headline_is_bounded_to_storage_limit(self):
        event = self.create_event(title="x" * 300)

        self.assertEqual(len(event.title), 255)

    def test_invalid_filter_is_rejected(self):
        response = self.client.get(
            "/api/v1/monitors/events/",
            {"severity": "debug"},
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dashboard_attention_compatibility_endpoint_remains_available(self):
        response = self.client.get(
            "/api/v1/monitors/attention/",
            {"page": "1", "page_size": "10", "preview": "diverse"},
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"count": 0, "results": []})

    def test_cleanup_deletes_only_expired_events(self):
        expired = self.create_event(
            title="Expired event",
            occurred_at=timezone.now() - timedelta(days=91),
        )
        retained = self.create_event(title="Retained event")

        deleted = cleanup_operational_events(days_to_keep=90, batch_size=1)

        self.assertEqual(deleted, 1)
        self.assertFalse(OperationalEvent.objects.filter(id=expired.id).exists())
        self.assertTrue(OperationalEvent.objects.filter(id=retained.id).exists())

    def test_availability_event_is_recorded_after_commit(self):
        occurred_at = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            schedule_availability_event(
                organization_id=self.org.id,
                source="node",
                availability="offline",
                occurred_at=occurred_at,
                resource_type="agent",
                resource_id="42",
                resource_name="agent-42",
                target_path="/node/agents",
            )

        event = OperationalEvent.objects.get()
        self.assertEqual(event.event_type, "node.offline")
        self.assertEqual(event.severity, OperationalEvent.Severity.WARNING)
        self.assertEqual(event.target_path, "/node/agents")

    @mock.patch(
        "apps.monitor.services.events.record_operational_event",
        side_effect=RuntimeError("event storage unavailable"),
    )
    def test_availability_event_failure_does_not_escape_commit_callback(
        self,
        record_event,
    ):
        with (
            self.assertLogs("django.test", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            schedule_availability_event(
                organization_id=self.org.id,
                source="node",
                availability="offline",
                occurred_at=timezone.now(),
                resource_type="agent",
                resource_id="42",
                resource_name="agent-42",
                target_path="/node/agents",
            )

        record_event.assert_called_once()

    def test_repository_offline_event_is_critical(self):
        with self.captureOnCommitCallbacks(execute=True):
            schedule_repository_health_event(
                organization_id=self.org.id,
                repository_id=7,
                repository_name="Primary repository",
                previous_health="online",
                health="offline",
            )

        event = OperationalEvent.objects.get()
        self.assertEqual(event.event_type, "repository.offline")
        self.assertEqual(event.severity, OperationalEvent.Severity.CRITICAL)
        self.assertEqual(event.target_path, "/node/repositories")


class TaskEventProjectionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="task-events", name="Task Events")

    def test_failed_task_signal_records_one_linked_event(self):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Daily backup",
            status=Task.Status.FAILED,
            error_message="Repository unavailable",
            finished_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            task_failed.send(sender=Task, task_uuid=str(task.task_uuid))
            task_failed.send(sender=Task, task_uuid=str(task.task_uuid))

        event = OperationalEvent.objects.get()
        self.assertEqual(event.event_type, "task.failed")
        self.assertEqual(event.severity, OperationalEvent.Severity.WARNING)
        self.assertEqual(event.resource_id, str(task.task_uuid))
        self.assertEqual(event.details, "Repository unavailable")

    def test_timed_out_task_signal_records_critical_event(self):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Restore files",
            status=Task.Status.TIMEOUT,
            finished_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            task_timed_out.send(sender=Task, task_uuid=str(task.task_uuid))

        event = OperationalEvent.objects.get()
        self.assertEqual(event.event_type, "task.timeout")
        self.assertEqual(event.severity, OperationalEvent.Severity.CRITICAL)

    @mock.patch(
        "apps.monitor.signals.record_operational_event",
        side_effect=RuntimeError("event storage unavailable"),
    )
    def test_task_event_failure_does_not_roll_back_terminal_state(
        self,
        record_event,
    ):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Daily backup",
            status=Task.Status.RUNNING,
        )

        with (
            self.assertLogs("django.test", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            complete_task(
                task_uuid=task.task_uuid,
                organization_id=self.org.id,
                status=Task.Status.FAILED,
            )

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        record_event.assert_called_once()
