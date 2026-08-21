from datetime import datetime, timedelta, timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.monitor.models import RepositoryUsageMetric
from apps.monitor.services.internal.repository_usage_history import (
    cleanup_repository_usage_history,
    record_repository_usage_result,
    repository_usage_history_payload,
)
from apps.storage.repositories.models import Repository


UTC = datetime_timezone.utc


class RepositoryUsageHistoryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="history-org", name="History Org")
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="history-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="history",
            config={},
        )

    def test_upsert_uses_one_logical_slot_and_preserves_zero(self):
        first = datetime(2026, 8, 21, 10, 16, tzinfo=UTC)
        record_repository_usage_result(
            self.repository,
            recorded_at=first,
            usage_bytes=0,
        )
        record_repository_usage_result(
            self.repository,
            recorded_at=first + timedelta(minutes=8),
            usage_bytes=2048,
        )

        row = RepositoryUsageMetric.objects.get()
        self.assertEqual(row.recorded_at, datetime(2026, 8, 21, 10, 15, tzinfo=UTC))
        self.assertEqual(row.usage_bytes, 2048)
        self.assertEqual(row.usage_source, RepositoryUsageMetric.UsageSource.ESTIMATED)
        self.assertIsNone(row.object_count)

    def test_failed_result_is_null_and_retry_can_recover(self):
        recorded_at = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)
        record_repository_usage_result(
            self.repository,
            recorded_at=recorded_at,
            usage_bytes=None,
        )
        row = RepositoryUsageMetric.objects.get()
        self.assertIsNone(row.usage_bytes)
        self.assertIsNone(row.usage_source)

        record_repository_usage_result(
            self.repository,
            recorded_at=recorded_at,
            usage_bytes=4096,
        )
        row.refresh_from_db()
        self.assertEqual(row.usage_bytes, 4096)

    def test_nas_repository_is_recorded(self):
        nas = Repository.objects.create(
            organization_id=self.org.id,
            name="history-nas",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={},
        )
        result = record_repository_usage_result(
            nas,
            recorded_at=datetime(2026, 8, 21, 10, 15, tzinfo=UTC),
            usage_bytes=1024,
        )
        self.assertIsNotNone(result)
        self.assertEqual(RepositoryUsageMetric.objects.get().usage_bytes, 1024)

    def test_negative_values_are_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RepositoryUsageMetric.objects.create(
                repository=self.repository,
                recorded_at=datetime(2026, 8, 21, 10, 15, tzinfo=UTC),
                usage_bytes=-1,
            )

    def test_history_keeps_gaps_and_uses_expected_resolution(self):
        now = datetime(2026, 8, 21, 10, 21, tzinfo=UTC)
        RepositoryUsageMetric.objects.create(
            repository=self.repository,
            recorded_at=datetime(2026, 8, 21, 9, 45, tzinfo=UTC),
            usage_bytes=100,
            usage_source=RepositoryUsageMetric.UsageSource.ESTIMATED,
        )
        RepositoryUsageMetric.objects.create(
            repository=self.repository,
            recorded_at=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
            usage_bytes=None,
        )
        RepositoryUsageMetric.objects.create(
            repository=self.repository,
            recorded_at=datetime(2026, 8, 21, 10, 15, tzinfo=UTC),
            usage_bytes=120,
            usage_source=RepositoryUsageMetric.UsageSource.ESTIMATED,
        )

        payload = repository_usage_history_payload(
            self.repository,
            range_name="24h",
            now=now,
        )

        self.assertEqual(payload["interval"], "15m")
        self.assertEqual(len(payload["points"]), 96)
        self.assertEqual(payload["points"][-3]["usage_bytes"], 100)
        self.assertIsNone(payload["points"][-2]["usage_bytes"])
        self.assertEqual(payload["points"][-2]["coverage"], "missing")
        self.assertEqual(payload["points"][-1]["usage_bytes"], 120)

    def test_thirty_days_returns_at_most_720_hourly_points(self):
        payload = repository_usage_history_payload(
            self.repository,
            range_name="30d",
            now=datetime(2026, 8, 21, 10, 21, tzinfo=UTC),
        )
        self.assertEqual(payload["interval"], "60m")
        self.assertEqual(len(payload["points"]), 720)

    def test_fourteen_days_returns_672_half_hour_points(self):
        payload = repository_usage_history_payload(
            self.repository,
            range_name="14d",
            now=datetime(2026, 8, 21, 10, 21, tzinfo=UTC),
        )
        self.assertEqual(payload["interval"], "30m")
        self.assertEqual(len(payload["points"]), 672)

    def test_cleanup_deletes_only_rows_older_than_cutoff(self):
        now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        cutoff = now - timedelta(days=30)
        for index, recorded_at in enumerate(
            [cutoff - timedelta(seconds=1), cutoff, cutoff + timedelta(minutes=15)]
        ):
            RepositoryUsageMetric.objects.create(
                repository=self.repository,
                recorded_at=recorded_at,
                usage_bytes=index,
            )
        deleted = cleanup_repository_usage_history(
            now=now,
            days_to_keep=30,
            batch_size=1,
        )
        self.assertEqual(deleted, 1)
        self.assertEqual(RepositoryUsageMetric.objects.count(), 2)


class RepositoryUsageHistoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="history-api@test.local",
            email="history-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="history-api-org", name="History API Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(self.user)
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="history-api-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="history-api",
            config={},
        )

    def test_history_endpoint_is_tenant_scoped(self):
        response = self.client.get(
            f"/api/v1/storage/repositories/{self.repository.id}/usage-history/",
            {"range": "7d"},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["interval"], "15m")
        self.assertEqual(len(response.data["points"]), 672)

        other_org = Organization.objects.create(key="other-history-org", name="Other History Org")
        other_repository = Repository.objects.create(
            organization_id=other_org.id,
            name="other-history-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="other-history",
            config={},
        )
        denied = self.client.get(
            f"/api/v1/storage/repositories/{other_repository.id}/usage-history/",
            {"range": "7d"},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(denied.status_code, 404)

    def test_history_endpoint_rejects_unsupported_range(self):
        response = self.client.get(
            f"/api/v1/storage/repositories/{self.repository.id}/usage-history/",
            {"range": "90d"},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, 400)

    def test_history_endpoint_supports_nas_and_local_disk_repositories(self):
        for repo_type in (Repository.Type.NAS, Repository.Type.PROXY_FS):
            repository = Repository.objects.create(
                organization_id=self.org.id,
                name=f"history-api-{repo_type}",
                repo_type=repo_type,
                status=Repository.Status.CREATED,
                health=Repository.Health.ONLINE,
                config={},
            )
            response = self.client.get(
                f"/api/v1/storage/repositories/{repository.id}/usage-history/",
                {"range": "24h"},
                HTTP_X_ORG_KEY=self.org.key,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["interval"], "15m")
