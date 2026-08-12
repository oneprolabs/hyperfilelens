"""System monitor API tests."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.alert.constants import AlertStatus
from apps.alert.models import AlertRecord
from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.task.models import Task


class SystemMonitorApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="monitor-api@test.local",
            email="monitor-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="monitor-test-org", name="Monitor Test Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)

    def test_system_monitor_requires_auth(self):
        anon = APIClient()
        resp = anon.get("/api/v1/monitors/system/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_system_monitor_returns_payload(self):
        from apps.monitor.services.interface import collect_and_persist_sample

        collect_and_persist_sample()
        resp = self.client.get("/api/v1/monitors/system/", {"hours": "1"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.data
        if isinstance(body, dict) and "data" in body:
            body = body["data"]
        self.assertIn("host", body)
        self.assertIn("series", body)
        self.assertIn("current", body)
        self.assertIn("range", body)
        self.assertGreaterEqual(len(body["series"]), 1)

    def test_invalid_custom_range(self):
        resp = self.client.get("/api/v1/monitors/system/", {"start_at": "2020-01-01T00:00:00Z"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_diverse_attention_preview_includes_each_available_kind(self):
        now = timezone.now()
        for index in range(10):
            Task.objects.create(
                organization_id=self.org.id,
                task_type=Task.Type.BACKUP,
                display_name=f"Failed backup {index}",
                status=Task.Status.FAILED,
                finished_at=now,
            )
        Node.objects.create(
            organization=self.org,
            name="Offline proxy",
            role=Node.Role.PROXY,
            availability=Node.Availability.OFFLINE,
            availability_updated_at=now - timedelta(minutes=1),
        )

        response = self.client.get(
            "/api/v1/monitors/attention/",
            {"page": "1", "page_size": "10", "preview": "diverse"},
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 11)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertIn("node", {row["kind"] for row in response.data["results"]})

    def test_node_attention_excludes_online_active_nodes(self):
        now = timezone.now()
        online = Node.objects.create(
            organization=self.org,
            name="Online agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            availability_updated_at=now,
        )
        offline = Node.objects.create(
            organization=self.org,
            name="Offline agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
            availability_updated_at=now - timedelta(minutes=1),
        )

        response = self.client.get(
            "/api/v1/monitors/attention/",
            {"type": "node"},
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            [row["id"] for row in response.data["results"]],
            [f"node-{offline.id}"],
        )
        self.assertNotIn(
            f"node-{online.id}",
            {row["id"] for row in response.data["results"]},
        )

    def test_operational_health_includes_acknowledged_but_not_resolved_alerts(self):
        acknowledged = AlertRecord.objects.create(
            organization=self.org,
            type="event",
            severity="warning",
            status=AlertStatus.ACKNOWLEDGED,
            title="Acknowledged issue",
            fingerprint="acknowledged-operational-health",
        )
        resolved = AlertRecord.objects.create(
            organization=self.org,
            type="event",
            severity="warning",
            status=AlertStatus.RESOLVED,
            title="Resolved issue",
            fingerprint="resolved-operational-health",
        )

        response = self.client.get(
            "/api/v1/monitors/attention/",
            {"type": "alert"},
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(f"alert-{acknowledged.id}", ids)
        self.assertNotIn(f"alert-{resolved.id}", ids)

    def test_operational_health_limits_failed_tasks_to_last_24_hours(self):
        now = timezone.now()
        recent = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Recent failure",
            status=Task.Status.FAILED,
            finished_at=now - timedelta(hours=23),
        )
        old = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Old failure",
            status=Task.Status.FAILED,
            finished_at=now - timedelta(hours=25),
        )

        response = self.client.get(
            "/api/v1/monitors/attention/",
            {"type": "task"},
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(f"task-{recent.id}", ids)
        self.assertNotIn(f"task-{old.id}", ids)

    def test_operational_health_includes_timed_out_tasks(self):
        timed_out = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Timed out backup",
            status=Task.Status.TIMEOUT,
            finished_at=timezone.now(),
        )

        response = self.client.get(
            "/api/v1/monitors/attention/",
            {"type": "task"},
            HTTP_X_ORG_KEY=self.org.key,
            secure=True,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = {row["id"]: row for row in response.data["results"]}
        self.assertIn(f"task-{timed_out.id}", rows)
        self.assertEqual(
            rows[f"task-{timed_out.id}"]["title"],
            "Timed out task: Timed out backup",
        )
