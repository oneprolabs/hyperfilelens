"""Audit REST API tests."""

from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.constants import AuditAction, AuditResult, AuditTargetType
from apps.audit.models import AuditLog
from apps.audit.services.interface import write_audit_log
from apps.iam.models import Membership, Organization


class AuditApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="audit-api@test.local",
            email="audit-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="audit-test-org", name="Audit Test Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.AUDITOR,
        )
        self.client.force_authenticate(user=self.user)
        write_audit_log(
            organization=self.org,
            user=self.user,
            action=AuditAction.TASK_CREATE,
            target_type=AuditTargetType.TASK,
            target_id="1",
            resource_type="task",
            resource_id="1",
            resource_name="backup-task-1",
            result=AuditResult.SUCCESS,
            ip_address="10.0.0.1",
        )
        write_audit_log(
            organization=self.org,
            user=self.user,
            action=AuditAction.ALERT_ACK,
            target_type=AuditTargetType.ALERT,
            target_id="2",
            result=AuditResult.FAILURE,
            error_message="denied",
        )

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def test_list_audit_logs(self):
        resp = self.client.get(
            "/api/v1/audits/",
            {"org": self.org.key},
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data.get("results", resp.data)
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], AuditAction.ALERT_ACK)

    def test_retrieve_audit_log_detail(self):
        log_id = AuditLog.objects.filter(organization=self.org).first().id
        resp = self.client.get(
            f"/api/v1/audits/{log_id}/",
            {"org": self.org.key},
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("action", resp.data)
        self.assertIn("timestamp", resp.data)

    def test_statistics(self):
        resp = self.client.get(
            "/api/v1/audits/statistics/",
            {"org": self.org.key},
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["total_count"], 2)
        self.assertGreaterEqual(resp.data["today_count"], 2)
        self.assertEqual(set(resp.data), {"total_count", "today_count"})

    def test_search_by_ip(self):
        resp = self.client.get(
            "/api/v1/audits/",
            {"org": self.org.key, "search": "10.0.0.1", "search_field": "ip"},
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data.get("results", resp.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ip_address"], "10.0.0.1")

    def test_filter_by_result(self):
        resp = self.client.get(
            "/api/v1/audits/",
            {"org": self.org.key, "result": AuditResult.FAILURE},
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data.get("results", resp.data)
        self.assertTrue(all(r["result"] == AuditResult.FAILURE for r in rows))

    def test_filter_by_datetime_bounds(self):
        old_log = write_audit_log(
            organization=self.org,
            user=self.user,
            action=AuditAction.TASK_CREATE,
            target_type=AuditTargetType.TASK,
            target_id="old",
            resource_type="datetime-test",
        )
        matched_log = write_audit_log(
            organization=self.org,
            user=self.user,
            action=AuditAction.TASK_CREATE,
            target_type=AuditTargetType.TASK,
            target_id="matched",
            resource_type="datetime-test",
        )
        new_log = write_audit_log(
            organization=self.org,
            user=self.user,
            action=AuditAction.TASK_CREATE,
            target_type=AuditTargetType.TASK,
            target_id="new",
            resource_type="datetime-test",
        )
        AuditLog.objects.filter(id=old_log.id).update(
            created_at=timezone.make_aware(datetime(2026, 6, 29, 2, 56))
        )
        AuditLog.objects.filter(id=matched_log.id).update(
            created_at=timezone.make_aware(datetime(2026, 6, 29, 3, 30))
        )
        AuditLog.objects.filter(id=new_log.id).update(
            created_at=timezone.make_aware(datetime(2026, 6, 29, 8, 1))
        )

        resp = self.client.get(
            "/api/v1/audits/",
            {
                "org": self.org.key,
                "resource_type": "datetime-test",
                "start_date": "2026-06-29T03:00:00.000Z",
                "end_date": "2026-06-29T08:00:00.000Z",
            },
            **self._headers(),
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data.get("results", resp.data)
        self.assertEqual([r["target_id"] for r in rows], ["matched"])

    def test_export_json(self):
        resp = self.client.get(
            "/api/v1/audits/export/",
            {"org": self.org.key, "format": "json"},
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("application/json", resp["Content-Type"])

    def test_export_csv(self):
        resp = self.client.get(
            "/api/v1/audits/export/",
            {"org": self.org.key, "export_format": "csv"},
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_create_forbidden(self):
        resp = self.client.post(
            "/api/v1/audits/",
            {"action": "test"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
