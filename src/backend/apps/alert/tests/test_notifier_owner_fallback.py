"""Organization default rules email the Owner when no channels are bound."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.notifier import send_notification, send_resolved_notification
from apps.iam.models import Membership, Organization
from apps.iam.profile_models import Profile
from common.platform_staff import apply_platform_staff
from common.resource_scope import ResourceScope

User = get_user_model()


class OwnerEmailFallbackTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="owner-fallback-org", name="Owner Fallback")
        self.owner = User.objects.create_user(
            username="org-owner",
            email="owner@example.com",
            password="test-pass-123",
        )
        Membership.objects.create(
            user=self.owner,
            organization=self.org,
            role=Membership.Role.OWNER,
            is_active=True,
        )

    def _default_policy(self, **overrides):
        values = {
            "organization": self.org,
            "owner_scope": ResourceScope.ORGANIZATION,
            "default_key": "backup.task_failed",
            "name": "Backup Task Failed",
            "type": "task",
            "severity": "critical",
            "notification_channel_ids": [],
        }
        values.update(overrides)
        return AlertPolicy.objects.create(**values)

    def _alert(self, policy, **overrides):
        values = {
            "organization": self.org,
            "owner_scope": ResourceScope.ORGANIZATION,
            "policy_id": policy.id,
            "type": policy.type,
            "severity": policy.severity,
            "status": "firing",
            "title": policy.name,
            "message": "Backup failed on host-a",
            "resource_name": "host-a",
            "fingerprint": f"{policy.id}|owner-fallback",
        }
        values.update(overrides)
        return AlertRecord.objects.create(**values)

    def test_default_rule_without_channels_emails_organization_owner(self):
        policy = self._default_policy()
        alert = self._alert(policy)

        with (
            patch(
                "apps.alert.services.internal.notifier.email_delivery_configured",
                return_value=True,
            ),
            patch(
                "apps.alert.services.internal.notifier.send_organization_alert_email",
            ) as send_email,
        ):
            send_notification(alert)

        send_email.assert_called_once()
        kwargs = send_email.call_args.kwargs
        self.assertEqual(kwargs["recipient"], self.owner.email)
        model = kwargs["model"]
        self.assertEqual(model.title, "Backup Task Failed")
        self.assertEqual(model.severity, "critical")
        self.assertFalse(model.resolved)
        self.assertEqual(model.organization_key, self.org.key)
        alert.refresh_from_db()
        self.assertEqual(alert.metadata["owner_email_fallback"]["status"], "sent")
        self.assertEqual(alert.metadata["owner_email_fallback"]["recipient"], self.owner.email)
        self.assertTrue(alert.metadata["last_notification_at"])

    def test_custom_rule_without_channels_does_not_email_owner(self):
        policy = self._default_policy(default_key=None, name="Custom Rule")
        alert = self._alert(policy)

        with patch(
            "apps.alert.services.internal.notifier.send_organization_alert_email",
        ) as send_email:
            send_notification(alert)

        send_email.assert_not_called()
        alert.refresh_from_db()
        self.assertNotIn("owner_email_fallback", alert.metadata or {})

    def test_bound_channels_skip_owner_fallback(self):
        policy = self._default_policy(notification_channel_ids=[99999])
        alert = self._alert(policy)

        with patch(
            "apps.alert.services.internal.notifier.send_organization_alert_email",
        ) as send_email:
            send_notification(alert)

        send_email.assert_not_called()

    def test_owner_fallback_skipped_when_email_service_unavailable(self):
        policy = self._default_policy()
        alert = self._alert(policy)

        with (
            patch(
                "apps.alert.services.internal.notifier.email_delivery_configured",
                return_value=False,
            ),
            patch(
                "apps.alert.services.internal.notifier.send_organization_alert_email",
            ) as send_email,
        ):
            send_notification(alert)

        send_email.assert_not_called()

    def test_installation_admin_owner_is_skipped(self):
        apply_platform_staff(self.owner, True)
        self.owner.save(update_fields=["is_staff", "is_superuser"])
        Profile.objects.update_or_create(
            user=self.owner,
            defaults={"is_installation_admin": True},
        )
        policy = self._default_policy()
        alert = self._alert(policy)

        with (
            patch(
                "apps.alert.services.internal.notifier.email_delivery_configured",
                return_value=True,
            ),
            patch(
                "apps.alert.services.internal.notifier.send_organization_alert_email",
            ) as send_email,
        ):
            send_notification(alert)

        send_email.assert_not_called()
        alert.refresh_from_db()
        self.assertNotIn("owner_email_fallback", alert.metadata or {})

    def test_resolved_default_rule_also_emails_owner(self):
        policy = self._default_policy()
        alert = self._alert(policy, status="resolved")

        with (
            patch(
                "apps.alert.services.internal.notifier.email_delivery_configured",
                return_value=True,
            ),
            patch(
                "apps.alert.services.internal.notifier.send_organization_alert_email",
            ) as send_email,
        ):
            send_resolved_notification(alert)

        kwargs = send_email.call_args.kwargs
        self.assertEqual(kwargs["recipient"], self.owner.email)
        self.assertTrue(kwargs["model"].resolved)
        alert.refresh_from_db()
        self.assertEqual(alert.metadata["owner_email_fallback"]["recipient"], self.owner.email)
        self.assertTrue(alert.metadata["owner_email_fallback"]["resolved"])

    def test_failed_owner_email_does_not_block_retry_window(self):
        policy = self._default_policy()
        alert = self._alert(policy)

        with (
            patch(
                "apps.alert.services.internal.notifier.email_delivery_configured",
                return_value=True,
            ),
            patch(
                "apps.alert.services.internal.notifier.send_organization_alert_email",
                side_effect=RuntimeError("smtp down"),
            ),
        ):
            send_notification(alert)

        alert.refresh_from_db()
        self.assertEqual(alert.metadata["owner_email_fallback"]["status"], "failed")
        self.assertNotIn("last_notification_at", alert.metadata or {})
