from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.protection.api.views.backup_source_snapshot import _datetime_query_param
from apps.protection.models import BackupSourceSnapshot
from apps.protection.selectors.backup_source_snapshot import (
    backup_source_snapshots_queryset,
    filter_backup_source_snapshots,
)


class BackupSourceSnapshotFilterTests(TestCase):
    organization_id = 701

    def _snapshot(self, uid: str, *, status: str, started_at=None):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.organization_id,
            snapshot_uid=uid,
            idempotency_key=f"idem-{uid}",
            source_type="agent",
            source_ref_id=11,
            backup_config_id=21,
            repository_id=31,
            task_id=41,
            status=status,
        )
        if started_at is not None:
            BackupSourceSnapshot.objects.filter(pk=snapshot.pk).update(
                started_at=started_at
            )
            snapshot.refresh_from_db()
        return snapshot

    def test_filters_snapshot_uid_status_and_started_at_range(self):
        now = timezone.now()
        expected = self._snapshot(
            "bss-filter-target",
            status=BackupSourceSnapshot.Status.AVAILABLE,
            started_at=now - timedelta(hours=2),
        )
        self._snapshot(
            "bss-filter-wrong-status",
            status=BackupSourceSnapshot.Status.PARTIAL,
            started_at=now - timedelta(hours=2),
        )
        self._snapshot(
            "bss-filter-too-old",
            status=BackupSourceSnapshot.Status.AVAILABLE,
            started_at=now - timedelta(days=2),
        )
        self._snapshot(
            "bss-filter-no-start",
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )

        result = filter_backup_source_snapshots(
            backup_source_snapshots_queryset(organization_id=self.organization_id),
            organization_id=self.organization_id,
            snapshot_uid="filter",
            status=BackupSourceSnapshot.Status.AVAILABLE,
            started_from=now - timedelta(days=1),
            started_to=now,
        )

        self.assertEqual(list(result.values_list("id", flat=True)), [expected.id])

    def test_rejects_invalid_started_at_datetime(self):
        with self.assertRaises(ValidationError):
            _datetime_query_param("not-a-date", "started_from")
