"""Organization alert email copy and markup."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.utils import timezone

from apps.alert.services.internal.alert_email import (
    build_alert_email_bodies,
    build_alert_email_model,
    build_alert_email_subject,
)


class AlertEmailCopyTests(SimpleTestCase):
    def test_task_failure_email_highlights_reason_and_details(self):
        model = build_alert_email_model(
            title="Backup Task Failed",
            severity="critical",
            resolved=False,
            organization_name="Demo Organization",
            organization_key="demo-organization",
            rule_name="Backup Task Failed",
            resource_type="agent_proxy",
            resource_name="host-a",
            resource_id="42",
            message="Backup failed on host-a",
            metadata={
                "task_type": "backup",
                "task_uuid": "7f2c9a1e-1111-2222-3333-abcdef012345",
                "error_message": "Connection timed out after 30s while reading /data/share.",
            },
            first_triggered_at=timezone.make_aware(
                datetime(2026, 11, 2, 16, 12, 0),
                ZoneInfo("UTC"),
            ),
        )
        subject = build_alert_email_subject(
            title=model.title,
            severity=model.severity,
            resolved=model.resolved,
        )
        plain, html = build_alert_email_bodies(model)

        self.assertEqual(subject, "Critical alert: Backup Task Failed")
        self.assertEqual(
            model.what_happened,
            "Connection timed out after 30s while reading /data/share.",
        )
        self.assertIn("What happened", plain)
        self.assertIn("Resource type", plain)
        self.assertIn("Source Host", plain)
        self.assertIn("Task", plain)
        self.assertIn("backup", plain.lower())
        self.assertIn("7f2c9a1e-1111-2222-3333-abcdef012345", plain)
        self.assertIn("border-left: 4px solid #dc2626", html)
        self.assertIn("What happened", html)
        self.assertIn("Details", html)
        self.assertNotIn("firing", plain.lower())
        self.assertNotIn("notification channels", plain.lower())

    def test_resolved_email_uses_resolution_copy(self):
        model = build_alert_email_model(
            title="Backup Task Failed",
            severity="critical",
            resolved=True,
            organization_name="Demo Organization",
            organization_key="demo-organization",
            rule_name="Backup Task Failed",
            resource_type="agent_proxy",
            resource_name="host-a",
            message="Backup failed on host-a",
        )
        subject = build_alert_email_subject(
            title=model.title,
            severity=model.severity,
            resolved=True,
        )
        plain, html = build_alert_email_bodies(model)
        self.assertEqual(subject, "Resolved: Backup Task Failed")
        self.assertIn("was resolved", plain)
        self.assertIn("Resolution summary", plain)
        self.assertIn("#059669", html)

    def test_metric_email_includes_current_and_threshold(self):
        model = build_alert_email_model(
            title="Control Plane CPU Usage High",
            severity="warning",
            resolved=False,
            organization_name="Demo Organization",
            organization_key="demo-organization",
            rule_name="Control Plane CPU Usage High",
            resource_type="system",
            resource_name="Control Plane",
            message="cpu_usage=93 >= 90%",
            current_value=93,
            threshold_value=90,
            unit="%",
        )
        plain, _html = build_alert_email_bodies(model)
        self.assertIn("Current / threshold", plain)
        self.assertIn("93% / 90%", plain)
