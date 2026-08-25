from __future__ import annotations

from django.test import SimpleTestCase
from django.utils import timezone

from apps.node import conf as node_conf
from apps.node.services.internal.task import _initial_watchdog_deadline
from apps.protection import conf as protection_conf


class BackupTaskWatchdogTests(SimpleTestCase):
    def test_prepared_snapshot_uses_backup_watchdog(self):
        started_at = timezone.now()

        deadline = _initial_watchdog_deadline(
            correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
            kind="backup.snapshot.create",
            from_time=started_at,
        )

        self.assertEqual(
            deadline,
            started_at
            + timezone.timedelta(
                seconds=protection_conf.PROTECTION_BACKUP_ACTIVITY_LEASE_SECONDS
            ),
        )

    def test_policy_prepare_uses_backup_watchdog(self):
        started_at = timezone.now()

        deadline = _initial_watchdog_deadline(
            correlation_type=(
                protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE
            ),
            kind="repository.policy.apply",
            from_time=started_at,
        )

        self.assertEqual(
            deadline,
            started_at
            + timezone.timedelta(
                seconds=protection_conf.PROTECTION_BACKUP_ACTIVITY_LEASE_SECONDS
            ),
        )

    def test_unrelated_task_keeps_default_watchdog(self):
        started_at = timezone.now()

        deadline = _initial_watchdog_deadline(
            correlation_type="unrelated",
            kind="repository.policy.apply",
            from_time=started_at,
        )

        self.assertEqual(
            deadline,
            started_at + timezone.timedelta(seconds=node_conf.TASK_WATCHDOG_SECONDS),
        )

    def test_automatic_probe_uses_remote_probe_watchdog(self):
        started_at = timezone.now()

        deadline = _initial_watchdog_deadline(
            correlation_type="storage.repository_health",
            kind="repo.status",
            from_time=started_at,
        )

        self.assertEqual(
            deadline,
            started_at
            + timezone.timedelta(
                seconds=node_conf.AUTOMATIC_PROBE_WATCHDOG_SECONDS
            ),
        )

    def test_source_nas_probe_uses_bounded_execution_watchdog(self):
        started_at = timezone.now()

        deadline = _initial_watchdog_deadline(
            correlation_type="source.connection_probe",
            kind="nas.test",
            from_time=started_at,
        )

        self.assertEqual(
            deadline,
            started_at
            + timezone.timedelta(
                seconds=node_conf.SOURCE_NAS_PROBE_EXECUTION_TIMEOUT_SECONDS
            ),
        )

    def test_snapshot_delete_uses_long_remote_watchdog(self):
        started_at = timezone.now()

        deadline = _initial_watchdog_deadline(
            correlation_type="protection.snapshot_delete",
            kind="snapshot.delete",
            from_time=started_at,
        )

        self.assertEqual(
            deadline,
            started_at
            + timezone.timedelta(
                seconds=node_conf.SNAPSHOT_DELETE_WATCHDOG_SECONDS
            ),
        )
