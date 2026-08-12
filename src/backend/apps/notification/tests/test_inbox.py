from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.alert.models import AlertRecord
from apps.alert.services.internal.lifecycle import resolve_alert
from apps.iam.models import Membership, Organization
from apps.notification.models import UserNotification
from apps.notification.services.internal.in_app import publish_to_org_members
from apps.notification.tasks.cleanup import purge_old_notification_records
from apps.notification.tasks.in_app import publish_user_notifications


class UserNotificationInboxTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="notification-owner",
            password="test-pass-123",
        )
        self.peer = user_model.objects.create_user(
            username="notification-peer",
            password="test-pass-123",
        )
        self.organization = Organization.objects.create(
            key="notification-org",
            name="Notification Org",
        )
        Membership.objects.create(
            user=self.user,
            organization=self.organization,
            is_active=True,
        )
        Membership.objects.create(
            user=self.peer,
            organization=self.organization,
            is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.headers = {"HTTP_X_ORG_KEY": self.organization.key}

    def create_notification(self, *, user=None, source_id="alert-1"):
        return UserNotification.objects.create(
            user=user or self.user,
            organization=self.organization,
            event_type="alert.firing",
            source_type="alert",
            source_id=source_id,
            title="Repository offline",
            severity="critical",
            target_url="/ops/alerts",
        )

    def test_read_state_is_user_scoped(self):
        notification = self.create_notification()
        peer_notification = self.create_notification(user=self.peer)

        response = self.client.get("/api/v1/notifications/inbox/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unread_count"], 1)
        self.assertFalse(response.data["results"][0]["is_read"])

        response = self.client.post(
            f"/api/v1/notifications/inbox/{notification.id}/read/",
            {},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 204)
        notification.refresh_from_db()
        peer_notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)
        self.assertIsNone(peer_notification.read_at)

    def test_canonical_plural_inbox_route_is_available(self):
        self.create_notification()

        response = self.client.get(
            "/api/v1/notifications/inbox/",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_reading_notification_does_not_acknowledge_alert(self):
        alert = AlertRecord.objects.create(
            organization=self.organization,
            type="event",
            severity="critical",
            status="firing",
            title="Backup failed",
            fingerprint="notification-alert",
        )
        notification = self.create_notification(source_id=str(alert.id))

        response = self.client.post(
            f"/api/v1/notifications/inbox/{notification.id}/read/",
            {},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 204)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")
        self.assertIsNone(alert.acknowledged_at)

    def test_mark_all_read_only_updates_current_user(self):
        for index in range(2):
            self.create_notification(source_id=str(index))
        peer_notification = self.create_notification(user=self.peer)

        response = self.client.post(
            "/api/v1/notifications/inbox/mark-all-read/",
            {},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            UserNotification.objects.filter(
                user=self.user,
                organization=self.organization,
                read_at__isnull=True,
            ).exists()
        )
        peer_notification.refresh_from_db()
        self.assertIsNone(peer_notification.read_at)

    def test_republished_event_becomes_unread_without_duplicate_rows(self):
        notification = self.create_notification()
        notification.read_at = notification.updated_at
        notification.save(update_fields=["read_at"])

        publish_to_org_members(
            organization_id=self.organization.id,
            event_type="alert.firing",
            source_type="alert",
            source_id="alert-1",
            title="Repository still offline",
            severity="critical",
            target_url="/ops/alerts",
        )

        notification.refresh_from_db()
        self.assertIsNone(notification.read_at)
        self.assertEqual(notification.title, "Repository still offline")
        self.assertEqual(
            UserNotification.objects.filter(
                user=self.user,
                organization=self.organization,
                event_type="alert.firing",
                source_type="alert",
                source_id="alert-1",
            ).count(),
            1,
        )
        self.assertTrue(
            UserNotification.objects.filter(
                user=self.peer,
                organization=self.organization,
                event_type="alert.firing",
                source_type="alert",
                source_id="alert-1",
            ).exists()
        )

    def test_publish_excludes_inactive_memberships_and_users(self):
        user_model = get_user_model()
        inactive_member = user_model.objects.create_user(
            username="inactive-notification-member",
            password="test-pass-123",
        )
        inactive_user = user_model.objects.create_user(
            username="inactive-notification-user",
            password="test-pass-123",
            is_active=False,
        )
        Membership.objects.create(
            user=inactive_member,
            organization=self.organization,
            is_active=False,
        )
        Membership.objects.create(
            user=inactive_user,
            organization=self.organization,
            is_active=True,
        )

        publish_to_org_members(
            organization_id=self.organization.id,
            event_type="alert.firing",
            source_type="alert",
            source_id="membership-boundary",
            title="Repository offline",
        )

        recipients = set(
            UserNotification.objects.filter(
                organization=self.organization,
                source_id="membership-boundary",
            ).values_list("user_id", flat=True)
        )
        self.assertEqual(recipients, {self.user.id, self.peer.id})

    def test_resolved_alert_notification_is_distinct_and_clear(self):
        alert = AlertRecord.objects.create(
            organization=self.organization,
            type="event",
            severity="warning",
            status="firing",
            title="Backup delayed",
            fingerprint="notification-resolved-alert",
        )

        with patch(
            "apps.notification.tasks.in_app.publish_user_notifications.delay"
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                resolve_alert(alert)

        enqueue.assert_called_once()
        publish_user_notifications(**enqueue.call_args.kwargs)

        notifications = UserNotification.objects.filter(
            organization=self.organization,
            event_type="alert.resolved",
            source_type="alert",
            source_id=str(alert.id),
        )
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(notifications.first().title, "Resolved: Backup delayed")
        self.assertEqual(notifications.first().target_url, "/ops/alerts")

    def test_resolving_an_already_resolved_alert_does_not_republish(self):
        alert = AlertRecord.objects.create(
            organization=self.organization,
            type="event",
            severity="warning",
            status="resolved",
            title="Backup delayed",
            fingerprint="notification-already-resolved-alert",
        )

        with patch(
            "apps.notification.tasks.in_app.publish_user_notifications.delay"
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                resolved = resolve_alert(alert)

        self.assertEqual(resolved.status, "resolved")
        enqueue.assert_not_called()

    def test_resolving_stale_alert_instances_publishes_once(self):
        alert = AlertRecord.objects.create(
            organization=self.organization,
            type="event",
            severity="warning",
            status="firing",
            title="Backup delayed",
            fingerprint="notification-concurrent-resolve-alert",
        )
        stale = AlertRecord.objects.get(id=alert.id)

        with patch(
            "apps.notification.tasks.in_app.publish_user_notifications.delay"
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                resolve_alert(alert)
                resolved_stale = resolve_alert(stale)

        self.assertEqual(resolved_stale.status, "resolved")
        enqueue.assert_called_once()

    def test_notification_enqueue_failure_does_not_break_alert_resolution(self):
        alert = AlertRecord.objects.create(
            organization=self.organization,
            type="event",
            severity="warning",
            status="firing",
            title="Backup delayed",
            fingerprint="notification-enqueue-failure",
        )

        with patch(
            "apps.notification.tasks.in_app.publish_user_notifications.delay",
            side_effect=RuntimeError("broker unavailable"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                resolved = resolve_alert(alert)

        self.assertEqual(resolved.status, "resolved")

    def test_cleanup_deletes_stale_inbox_items_only(self):
        stale = self.create_notification(source_id="stale")
        current = self.create_notification(source_id="current")
        UserNotification.objects.filter(id=stale.id).update(
            updated_at=timezone.now() - timedelta(days=91),
        )

        result = purge_old_notification_records(days_to_keep=90)

        self.assertEqual(result["user_notifications_deleted"], 1)
        self.assertFalse(UserNotification.objects.filter(id=stale.id).exists())
        self.assertTrue(UserNotification.objects.filter(id=current.id).exists())

    def test_cleanup_task_is_registered_with_celery(self):
        from common.celery import app

        app.loader.import_default_modules()

        self.assertIn(purge_old_notification_records.name, app.tasks)
