from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from apps.storage.repositories.models import RepositoryOrphanSnapshot, RepositoryTask
from apps.storage.services.internal.maintenance_schedule import assign_full_slots, next_full_slot, slot_on
from apps.storage.services.internal.orphan_snapshot_fence import assert_no_orphan_cleanup
from apps.storage.services.internal.orphan_snapshots import run_orphan_reconciliation
from apps.storage.services.internal.repository_operations import create_repository_operation_task, discover_repository_execution_targets, maintenance_settings
from apps.storage.services.internal.snapshot_inventory import parse_snapshot_inventory
from apps.storage.tests import test_repository_tasks as fixtures
from apps.task.models import Task

SID = 'a' * 32
ITEM = {'snapshot_id': SID, 'source': {'host': 'host', 'user': 'user', 'path': '/backup'}, 'operation_id': ''}


class ScheduleTests(SimpleTestCase):
    @patch.dict('os.environ', {'STORAGE_MAINTENANCE_TIMEZONE': 'Asia/Shanghai', 'STORAGE_MAINTENANCE_FULL_INTERVAL_SECONDS': '172800', 'STORAGE_MAINTENANCE_FULL_WINDOW_START': '01:00', 'STORAGE_MAINTENANCE_FULL_WINDOW_END': '05:00'})
    def test_calendar_slot_does_not_drift_after_completion(self):
        settings = maintenance_settings()
        state = SimpleNamespace(full_day_group=0, full_slot_seconds=2 * 3600)
        first = next_full_slot(state, datetime(2026, 10, 9, tzinfo=dt_timezone.utc), settings)
        following = next_full_slot(state, first + timedelta(minutes=49), settings)
        self.assertEqual(following - first, timedelta(days=2))
        self.assertEqual(following.astimezone(settings.timezone).hour, 2)

    def test_dst_gap_moves_forward_within_window(self):
        from datetime import date, time
        result = slot_on(date(2026, 3, 8), 2 * 3600, ZoneInfo('America/New_York'), time(5))
        self.assertEqual(result.astimezone(ZoneInfo('America/New_York')).hour, 3)

    def test_inventory_rejects_malformed_or_truncated_input(self):
        for rows in [None, {}, [{'id': SID, 'labels': {'type': 'policy'}}], [{'id': '--all', 'labels': {'type': 'snapshot'}}]]:
            with self.assertRaises(ValueError):
                parse_snapshot_inventory(rows)
        row = {'id': SID, 'labels': {'type': 'snapshot', 'path': '/backup'}}
        self.assertEqual(parse_snapshot_inventory([row])[0]['snapshot_id'], SID)
        with self.assertRaises(ValueError):
            parse_snapshot_inventory([row, row])


@patch.dict('os.environ', {
    'STORAGE_MAINTENANCE_TIMEZONE': 'UTC',
    'STORAGE_ORPHAN_SNAPSHOT_DELETE_ENABLED': 'true',
})
class OrphanTests(TestCase):
    def setUp(self):
        fixtures.RepositoryTaskTests.setUp(self)
        discover_repository_execution_targets()
        self.target = self.repository.execution_targets.get()

    def operation(self):
        return create_repository_operation_task(target_id=self.target.id, operation_type=RepositoryTask.OperationType.SNAPSHOT_RECONCILE)

    @patch('apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots')
    @patch('apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory', return_value=[ITEM])
    def test_first_discovery_only_marks_and_fences_backup_admission(self, _inventory, delete):
        rt = self.operation()
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'success')
        delete.assert_not_called()
        candidate = RepositoryOrphanSnapshot.objects.get()
        self.assertEqual(candidate.status, 'candidate')
        self.assertEqual(rt.task.events.get(metadata__event_type='orphan_snapshot_result').metadata['outcome'], 'discovered')
        assert_no_orphan_cleanup(self.repository.id)

    @patch('apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots', return_value={'results': [{'kopia_snapshot_id': SID, 'status': 'success'}]})
    @patch('apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory', return_value=[ITEM])
    def test_later_run_deletes_only_after_grace_and_lists_are_disjoint(self, _inventory, delete):
        first = self.operation()
        run_orphan_reconciliation(first.id)
        RepositoryOrphanSnapshot.objects.update(first_seen_at=timezone.now() - timedelta(hours=25))
        second = self.operation()
        run_orphan_reconciliation(second.id)
        delete.assert_called_once()
        self.assertEqual(RepositoryOrphanSnapshot.objects.get().status, 'deleted')
        self.assertFalse(second.task.events.filter(metadata__outcome='discovered').exists())
        self.assertTrue(second.task.events.filter(metadata__outcome='deleted').exists())
        self.assertTrue(first.task.events.filter(metadata__outcome='discovered').exists())

    @patch('apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots')
    @patch('apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory', return_value=[ITEM])
    def test_manual_rerun_during_grace_never_deletes(self, _inventory, delete):
        run_orphan_reconciliation(self.operation().id)
        run_orphan_reconciliation(self.operation().id)
        delete.assert_not_called()

    @patch.dict("os.environ", {"STORAGE_ORPHAN_SNAPSHOT_DELETE_ENABLED": "false"})
    @patch('apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots')
    @patch('apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory', return_value=[ITEM])
    def test_observation_mode_never_deletes_old_candidates(self, _inventory, delete):
        run_orphan_reconciliation(self.operation().id)
        RepositoryOrphanSnapshot.objects.update(first_seen_at=timezone.now() - timedelta(days=2))
        rt = self.operation()
        run_orphan_reconciliation(rt.id)
        delete.assert_not_called()
        self.assertTrue(rt.task.events.filter(metadata__outcome="deferred").exists())

    @patch('apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots')
    @patch('apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory', return_value=[ITEM])
    def test_restored_management_revokes_candidate(self, _inventory, delete):
        run_orphan_reconciliation(self.operation().id)
        RepositoryOrphanSnapshot.objects.update(first_seen_at=timezone.now() - timedelta(days=2))
        with patch('apps.storage.services.internal.orphan_snapshots.managed_ids', return_value={SID}):
            run_orphan_reconciliation(self.operation().id)
        self.assertEqual(RepositoryOrphanSnapshot.objects.get().status, 'managed')
        delete.assert_not_called()

    @patch('apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory', return_value=[ITEM])
    def test_unknown_delete_retains_gate_and_never_reissues(self, _inventory):
        run_orphan_reconciliation(self.operation().id)
        RepositoryOrphanSnapshot.objects.update(first_seen_at=timezone.now() - timedelta(days=2))
        rt = self.operation()
        with patch('apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots', side_effect=TimeoutError) as delete:
            self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'waiting')
            self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'waiting')
            self.assertEqual(delete.call_count, 1)
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)

    def test_active_backup_blocks_reconciliation_admission(self):
        Task.objects.create(organization_id=self.org.id, task_type=Task.Type.BACKUP, status=Task.Status.RUNNING, request_payload={'repository_id': self.repository.id})
        self.assertIsNone(self.operation())

    def test_timed_out_accepted_agent_is_not_proof_of_termination(self):
        from apps.node.models import Node, NodeTask
        task = Task.objects.create(
            organization_id=self.org.id, task_type=Task.Type.BACKUP,
            status=Task.Status.TIMEOUT, request_payload={"repository_id": self.repository.id},
        )
        node = Node.objects.create(organization_id=self.org.id, name="writer", role="agent")
        NodeTask.objects.create(
            organization_id=self.org.id, requesting_organization_id=self.org.id,
            node=node, parent_task=task, kind="backup.run", correlation_id=str(task.task_uuid),
            status=NodeTask.Status.TIMEOUT, accepted_at=timezone.now(),
            watchdog_deadline_at=timezone.now(), result={"diagnostic_error_code": "RESULT_ACK_TIMEOUT"},
        )
        self.assertIsNone(self.operation())

    @patch('apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory')
    def test_late_result_operation_is_not_classified_as_orphan(self, inventory):
        from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
        task = Task.objects.create(
            organization_id=self.org.id, task_type=Task.Type.BACKUP,
            status=Task.Status.FAILED, request_payload={"repository_id": self.repository.id},
        )
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.org.id, snapshot_uid="late", idempotency_key="late",
            source_type="agent", source_ref_id=1, backup_config_id=1,
            repository_id=self.repository.id, task_id=task.id, task_uuid=task.task_uuid,
            status="failed",
        )
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot, organization_id=self.org.id, backup_config_id=1,
            backup_config_dir_id=1, source_path="/backup", repository_id=self.repository.id,
            status="failed",
        )
        inventory.return_value = [{**ITEM, "operation_id": f"{task.task_uuid}-1"}]
        run_orphan_reconciliation(self.operation().id)
        self.assertFalse(RepositoryOrphanSnapshot.objects.exists())

    @patch('apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots', return_value={'results': [{'kopia_snapshot_id': SID, 'status': 'failed', 'error_message': 'storage unavailable'}]})
    @patch('apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory', return_value=[ITEM])
    def test_confirmed_delete_failure_is_retryable_without_losing_candidate(self, _inventory, delete):
        run_orphan_reconciliation(self.operation().id)
        RepositoryOrphanSnapshot.objects.update(first_seen_at=timezone.now() - timedelta(days=2))
        rt = self.operation()
        self.assertEqual(run_orphan_reconciliation(rt.id)["status"], "failed")
        self.assertEqual(RepositoryOrphanSnapshot.objects.get().status, "candidate")
        assert_no_orphan_cleanup(self.repository.id)
        self.assertTrue(rt.task.events.filter(metadata__outcome="failed").exists())

    @patch('apps.storage.tasks._execute_repository_operation')
    def test_automatic_full_retry_rechecks_window_at_actual_start(self, execute):
        from apps.storage.tasks import execute_repository_operation
        rt = create_repository_operation_task(
            target_id=self.target.id, operation_type=RepositoryTask.OperationType.MAINTENANCE_FULL,
            due_at=timezone.now(), trigger_type=Task.TriggerType.RETRY,
        )
        with patch('apps.storage.services.internal.repository_operations._inside_full_window', return_value=False):
            result = execute_repository_operation.run(repository_task_id=rt.id)
        self.assertEqual(result["status"], "deferred_maintenance_window")
        execute.assert_not_called()

    def test_assignments_balance_two_days_and_remain_stable(self):
        from unittest.mock import MagicMock
        states = [MagicMock(full_day_group=None, full_slot_seconds=None) for _ in range(8)]
        targets = [SimpleNamespace(maintenance_state=state) for state in states]
        with patch.dict('os.environ', {'STORAGE_MAINTENANCE_FULL_INTERVAL_SECONDS': '172800'}):
            settings = maintenance_settings()
            assign_full_slots(targets, settings, timezone.now())
            before = [(state.full_day_group, state.full_slot_seconds) for state in states]
            self.assertEqual(sum(group == 0 for group, _ in before), 4)
            self.assertEqual(len(set(before)), 8)
            assign_full_slots(targets, settings, timezone.now())
            self.assertEqual(before, [(state.full_day_group, state.full_slot_seconds) for state in states])


class OrphanApiTests(TestCase):
    def setUp(self):
        fixtures.RepositoryTasksApiTests.setUp(self)
        from apps.storage.services.internal.repository_operations import finalize_repository_operation
        finalize_repository_operation(repository_task_id=self.repository_task.id, succeeded=True)

    @patch("apps.storage.tasks.execute_repository_operation.delay")
    def test_manual_scan_is_authorized_and_queued_after_commit(self, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/api/v1/storage/repositories/{self.repository.id}/reconcile_snapshots/",
                HTTP_X_ORG_KEY=self.org.key,
            )
        self.assertEqual(response.status_code, 202)
        dispatch.assert_called_once()
        self.assertTrue(RepositoryTask.objects.filter(operation_type="snapshot.reconcile").exists())

    @patch("apps.storage.tasks.execute_repository_operation.delay")
    def test_other_organization_cannot_scan_repository(self, dispatch):
        from apps.iam.models import Organization
        other = Organization.objects.create(key="other-scan", name="Other")
        response = self.client.post(
            f"/api/v1/storage/repositories/{self.repository.id}/reconcile_snapshots/",
            HTTP_X_ORG_KEY=other.key,
        )
        self.assertIn(response.status_code, {403, 404})
        dispatch.assert_not_called()
