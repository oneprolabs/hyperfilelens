import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.insight.models import InsightReport
from apps.protection.models import BackupConfig, BackupSourceSnapshot, BackupSourceSnapshotDirectory

User = get_user_model()


def _snapshot_directory(*, organization_id: int) -> BackupSourceSnapshotDirectory:
    config = BackupConfig.objects.create(
        organization_id=organization_id, name="Report configuration",
        source_type="agent", source_ref_id=1, repository_id=1,
    )
    snap = BackupSourceSnapshot.objects.create(
        organization_id=organization_id,
        snapshot_uid=f"snap-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-{uuid.uuid4().hex}",
        source_type="agent",
        source_ref_id=1,
        backup_config_id=config.pk,
        repository_id=1,
        task_id=1,
    )
    return BackupSourceSnapshotDirectory.objects.create(
        source_snapshot=snap,
        organization_id=organization_id,
        backup_config_id=config.pk,
        backup_config_dir_id=1,
        source_path="/data",
        repository_id=1,
    )


@override_settings(HFL_QUOTA_ENFORCEMENT_ENABLED=False)
class InsightOrgScopingTests(APITestCase):
    """Two-tenant isolation fixture, independent of the Community one-org cap."""
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="insight-a@test.local",
            email="insight-a@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org_a, _ = provision_registered_user_tenant(self.user_a)

        self.user_b = User.objects.create_user(
            username="insight-b@test.local",
            email="insight-b@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org_b, _ = provision_registered_user_tenant(self.user_b)

        snapshot_a = _snapshot_directory(organization_id=self.org_a.id)
        snapshot_b = _snapshot_directory(organization_id=self.org_b.id)
        self.report_a = InsightReport.objects.create(
            organization=self.org_a,
            snapshot=snapshot_a,
        )
        self.report_b = InsightReport.objects.create(
            organization=self.org_b,
            snapshot=snapshot_b,
        )

    def test_insight_report_list_scoped_to_org(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(
            reverse("insight-report-list"),
            HTTP_X_ORG_KEY=self.org_a.key,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row["id"] for row in response.data["data"]["list"]]
        self.assertEqual(ids, [self.report_a.id])

    def test_insight_report_foreign_org_returns_404(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(
            reverse("insight-report-detail", kwargs={"pk": self.report_b.id}),
            HTTP_X_ORG_KEY=self.org_a.key,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
