"""Platform model account incidents reach a real operator, not tenant users."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.alert.models import AlertRecord
from apps.alert.services.internal.notifier import send_notification
from apps.iam.profile_models import Profile


class PlatformCreditNotificationTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="credit-install-admin", email="admin@example.com", is_staff=True,
        )
        Profile.objects.update_or_create(
            user=self.admin, defaults={"is_installation_admin": True},
        )
        get_user_model().objects.create_user(
            username="unrelated-staff", email="other@example.com", is_staff=True,
        )
        self.alert = AlertRecord.objects.create(
            owner_scope="platform", organization=None, type="event",
            resource_type="ai_model", resource_id="1",
            title="AI Model Credit Issue", message="Insufficient account credit.",
            severity="critical", fingerprint="credit-test",
            metadata={"provider_error_code": "MODEL_INSUFFICIENT_CREDIT"},
        )

    @patch("apps.alert.services.internal.notifier.send_organization_alert_email")
    @patch("apps.alert.services.internal.notifier.email_delivery_configured", return_value=True)
    def test_unbound_platform_credit_rule_emails_installation_admin(self, configured, send):
        send_notification(self.alert)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["recipient"], self.admin.email)
        self.assertIn("credit", send.call_args.kwargs["model"].what_happened)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.metadata["platform_email_fallback"]["status"], "sent")
        self.assertIn("last_notification_at", self.alert.metadata)

    @patch("apps.alert.services.internal.notifier.send_organization_alert_email")
    @patch("apps.alert.services.internal.notifier.email_delivery_configured", return_value=False)
    def test_no_mail_configuration_is_not_reported_as_notification_success(self, configured, send):
        send_notification(self.alert)
        send.assert_not_called()
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.metadata["platform_email_fallback"]["status"], "unavailable")
        self.assertNotIn("last_notification_at", self.alert.metadata)

    @patch("apps.alert.services.internal.notifier.send_organization_alert_email", side_effect=RuntimeError)
    @patch("apps.alert.services.internal.notifier.email_delivery_configured", return_value=True)
    def test_email_failure_preserves_alert_and_delivery_failure(self, configured, send):
        send_notification(self.alert)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.metadata["platform_email_fallback"]["status"], "failed")
        self.assertNotIn("last_notification_at", self.alert.metadata)
