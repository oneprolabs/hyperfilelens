"""Real Node metadata/dispatch regressions and bounded recovery semantics."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.node.models import Node, NodeTask
from apps.storage.repositories.models import RepositoryOrphanSnapshot, RepositoryTask
from apps.storage.services.internal.orphan_snapshots import run_orphan_reconciliation
from apps.storage.services.internal.repository_operations import (
    create_repository_operation_task, discover_repository_execution_targets,
    finalize_repository_operation,
)
from apps.storage.tests import test_repository_tasks as fixtures
from apps.task.models import Task

CAP = 'orphan_snapshot_reconcile_v1'
MANIFEST = {'id': 'a' * 32, 'labels': {'type': 'snapshot', 'path': '/backup', 'hostname': 'writer', 'username': 'root'}}


@patch.dict('os.environ', {'STORAGE_MAINTENANCE_TIMEZONE': 'UTC'})
class ReconciliationRecoveryTests(TestCase):
    def setUp(self):
        fixtures.RepositoryTaskTests.setUp(self)
        discover_repository_execution_targets()
        self.target = self.repository.execution_targets.get()
        self.node = Node.objects.create(
            organization_id=self.org.id, name='writer', role='agent',
            metadata={'inventory': {'capabilities': [CAP]}},
        )
        self.target.owner_type = 'node'
        self.target.owner_node_id = self.node.id
        self.target.save()

    def operation(self, trigger=Task.TriggerType.MANUAL):
        return create_repository_operation_task(
            target_id=self.target.id,
            operation_type=RepositoryTask.OperationType.SNAPSHOT_RECONCILE,
            trigger_type=trigger,
        )

    def child(self, kwargs, *, status='success', result=None):
        child = NodeTask.objects.create(
            organization_id=self.org.id, requesting_organization_id=self.org.id,
            node=self.node, parent_task=kwargs['parent_task'], kind=kwargs['kind'],
            correlation_type=kwargs['correlation_type'], correlation_id=kwargs['correlation_id'],
            payload=kwargs['payload'], watchdog_deadline_at=timezone.now() + timedelta(hours=1),
            status=status, accepted_at=timezone.now(),
            result=result if result is not None else {'inventory_complete': True, 'manifests': [MANIFEST]},
        )
        return SimpleNamespace(task=child)

    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_inventory_metadata_capability_dispatches_and_completes_steps(self, dispatch, _payload):
        dispatch.side_effect = lambda **kwargs: self.child(kwargs)
        rt = self.operation()
        result = run_orphan_reconciliation(rt.id)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(
            list(rt.task.steps.values_list('status', flat=True)),
            ['success', 'success', 'success', 'success'],
        )
        self.assertNotIn(
            'refresh_repository_usage',
            rt.task.steps.values_list('step_name', flat=True),
        )
        rt.task.refresh_from_db()
        self.assertEqual(rt.task.result_payload['orphan_snapshot_summary']['scanned'], 1)
        self.assertTrue(rt.task.result_payload['orphan_snapshot_summary']['inventory_available'])
        self.assertTrue(rt.task.events.filter(metadata__outcome='discovered').exists())

    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_waiting_resumes_existing_child_without_dispatch_or_duplicate_events(self, dispatch, _payload):
        dispatch.side_effect = lambda **kwargs: self.child(kwargs, status='running', result={})
        rt = self.operation()
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'waiting')
        self.assertEqual(
            list(rt.task.steps.values_list('status', flat=True)),
            ['success', 'success', 'running', 'pending'],
        )
        child = rt.task.node_tasks.get()
        child.status = 'success'
        child.result = {'inventory_complete': True, 'manifests': [MANIFEST]}
        child.save()
        self.node.metadata = {}
        self.node.save()  # Completion must remain observable after capability changes.
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'success')
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(rt.task.events.filter(metadata__outcome='discovered').count(), 1)

    def retry_due(self, rt):
        retry = RepositoryTask.objects.select_related('task').get(task__replaces_task=rt.task)
        retry.due_at = timezone.now() - timedelta(seconds=1)
        retry.save(update_fields=['due_at'])
        return retry

    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_missing_capability_retries_once_then_ends_cycle(self, dispatch):
        self.node.metadata = {'inventory': {'capabilities': []}}
        self.node.save()
        rt = self.operation()
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'retry_scheduled')
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'failed')
        dispatch.assert_not_called()
        rt.task.refresh_from_db()
        self.assertEqual(rt.task.retry_count, 0)
        retry = self.retry_due(rt)
        self.assertEqual(run_orphan_reconciliation(retry.id)['status'], 'failed')
        retry.task.refresh_from_db()
        self.assertEqual(retry.task.recovery_attempt, 1)
        self.assertFalse(Task.objects.filter(replaces_task=retry.task).exists())
        rt.task.refresh_from_db()
        self.assertEqual(rt.task.error_code, 'ORPHAN_CAPABILITY_MISSING')
        self.assertEqual(rt.task.status, Task.Status.FAILED)
        self.assertIn(CAP, rt.task.error_message)
        self.assertEqual(rt.task.current_step, 'run_repository_operation')
        self.assertEqual(
            list(rt.task.steps.values_list('status', flat=True)),
            ['success', 'success', 'failed', 'skipped'],
        )
        summary = rt.task.result_payload['orphan_snapshot_summary']
        self.assertIsNone(summary['scanned'])
        self.assertFalse(summary['inventory_available'])
        state = self.target.maintenance_state
        state.refresh_from_db()
        self.assertGreater(state.next_reconcile_due_at, timezone.now() + timedelta(hours=47))
        self.assertIsNotNone(self.operation())  # A fresh manual scan is never permanently paused.

    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_retry_can_succeed_after_agent_capability_recovers(self, dispatch, _payload):
        self.node.metadata = {}
        self.node.save()
        rt = self.operation()
        run_orphan_reconciliation(rt.id)
        self.node.metadata = {'capabilities': [CAP]}
        self.node.save()
        dispatch.side_effect = lambda **kwargs: self.child(kwargs)
        retry = self.retry_due(rt)
        original_events = list(rt.task.events.values())
        self.assertEqual(run_orphan_reconciliation(retry.id)['status'], 'success')
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(retry.task.node_tasks.get().correlation_id, f'{retry.task.task_uuid}:snapshot.inventory:')
        rt.task.refresh_from_db()
        retry.task.refresh_from_db()
        self.assertEqual(rt.task.status, Task.Status.FAILED)
        self.assertEqual(retry.task.status, Task.Status.SUCCESS)
        self.assertNotEqual(retry.task.task_uuid, rt.task.task_uuid)
        self.assertEqual(list(rt.task.events.values()), original_events)
        self.assertFalse(retry.task.events.filter(level='ERROR').exists())
        self.assertEqual(retry.task.recovery_attempt, 1)
        self.assertEqual(retry.task.trigger_type, Task.TriggerType.RETRY)
        self.assertIsNone(retry.task.error_message)

    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_confirmed_pre_execution_failure_uses_a_new_child_for_the_one_retry(self, dispatch, _payload):
        def execute(**kwargs):
            if dispatch.call_count == 1:
                handle = self.child(kwargs, status='failed', result={})
                handle.task.accepted_at = None
                handle.task.save(update_fields=['accepted_at'])
                return handle
            return self.child(kwargs)

        dispatch.side_effect = execute
        rt = self.operation()
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'retry_scheduled')
        retry = self.retry_due(rt)
        self.assertEqual(run_orphan_reconciliation(retry.id)['status'], 'success')
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(rt.task.node_tasks.count(), 1)
        self.assertEqual(retry.task.node_tasks.count(), 1)

    @patch('apps.storage.services.internal.orphan_snapshots.managed_ids', side_effect=[ConnectionError('lookup failed'), set()])
    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_check_lookup_failure_also_gets_only_one_retry(self, dispatch, _payload, _managed):
        dispatch.side_effect = lambda **kwargs: self.child(kwargs)
        rt = self.operation()
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'retry_scheduled')
        retry = self.retry_due(rt)
        self.assertEqual(run_orphan_reconciliation(retry.id)['status'], 'success')
        self.assertEqual(dispatch.call_count, 2)

    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_unexpected_error_is_actionable_without_echoing_credentials(self, dispatch, _payload):
        dispatch.side_effect = AttributeError('password=secret-value https://user:password@example.test')
        rt = self.operation()
        run_orphan_reconciliation(rt.id)
        retry = self.retry_due(rt)
        run_orphan_reconciliation(retry.id)
        retry.task.refresh_from_db()
        self.assertEqual(retry.task.error_code, 'ORPHAN_INTERNAL_ERROR')
        error = retry.task.result_payload['orphan_snapshot_error']
        self.assertEqual(error['exception_type'], 'AttributeError')
        self.assertEqual(error['stage'], 'run_repository_operation')
        self.assertNotIn('secret-value', str(retry.task.result_payload))
        self.assertNotIn('user:password', str(retry.task.result_payload))

    @patch('apps.storage.tasks.execute_repository_operation.apply_async')
    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_one_retry_uses_new_task_gate_and_sixty_second_delay(self, dispatch, _payload, enqueue):
        from apps.storage.services.internal.orphan_snapshot_fence import assert_no_orphan_cleanup
        from django.core.exceptions import ValidationError

        dispatch.side_effect = ConnectionError('unavailable')
        rt = self.operation()
        state = self.target.maintenance_state
        original_due = state.next_reconcile_due_at
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'retry_scheduled')
        retry = RepositoryTask.objects.select_related('task').get(task__replaces_task=rt.task)
        enqueue.assert_called_once_with(kwargs={'repository_task_id': retry.id}, countdown=60)
        self.target.refresh_from_db()
        self.assertEqual(self.target.active_task_id, retry.task_id)
        self.assertGreater(retry.due_at, timezone.now() + timedelta(seconds=55))
        state.refresh_from_db()
        self.assertEqual(state.next_reconcile_due_at, original_due)
        rt.task.refresh_from_db()
        original_finished = rt.task.finished_at
        original_events = rt.task.events.count()
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'failed')
        self.assertEqual(run_orphan_reconciliation(retry.id)['status'], 'retry_scheduled')
        self.assertEqual(Task.objects.filter(replaces_task=rt.task).count(), 1)
        self.assertIsNone(retry.task.started_at)
        self.assertEqual(dispatch.call_count, 1)  # Periodic redispatch cannot skip the one-minute wait.
        self.retry_due(rt)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(run_orphan_reconciliation(retry.id)['status'], 'failed')
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(enqueue.call_count, 1)
        self.assertFalse(Task.objects.filter(replaces_task=retry.task).exists())
        rt.task.refresh_from_db()
        self.assertEqual(rt.task.finished_at, original_finished)
        self.assertEqual(rt.task.events.count(), original_events)
        state.refresh_from_db()
        self.assertGreater(state.next_reconcile_due_at, timezone.now() + timedelta(hours=47))
        assert_no_orphan_cleanup(self.repository.id)

    def test_finished_scan_does_not_change_maintenance_backoff(self):
        state = self.target.maintenance_state
        state.consecutive_failures = 5
        state.next_retry_at = timezone.now() + timedelta(hours=1)
        state.save()
        retry_at = state.next_retry_at
        rt = self.operation()
        finalize_repository_operation(repository_task_id=rt.id, succeeded=False, error_code='ORPHAN_STORAGE_UNAVAILABLE')
        state.refresh_from_db()
        self.assertEqual(state.consecutive_failures, 5)
        self.assertEqual(state.next_retry_at, retry_at)
        self.assertGreater(state.next_reconcile_due_at, timezone.now() + timedelta(hours=47))

    def test_replacement_creation_failure_rolls_back_attempt_and_gate_transfer(self):
        from apps.storage.services.internal.orphan_snapshots import _finish

        rt = self.operation()
        original_events = list(rt.task.events.values())
        original_due = self.target.maintenance_state.next_reconcile_due_at
        with patch(
            'apps.storage.services.internal.orphan_snapshots.create_task',
            side_effect=RuntimeError('replacement persistence failed'),
        ), self.assertRaises(RuntimeError):
            _finish(rt, False, 'Inventory unavailable.')
        rt.task.refresh_from_db()
        self.target.refresh_from_db()
        state = self.target.maintenance_state
        state.refresh_from_db()
        self.assertEqual(rt.task.status, Task.Status.PENDING)
        self.assertEqual(self.target.active_task_id, rt.task_id)
        self.assertEqual(state.next_reconcile_due_at, original_due)
        self.assertEqual(list(rt.task.events.values()), original_events)
        self.assertFalse(Task.objects.filter(replaces_task=rt.task).exists())

    @patch.dict('os.environ', {'STORAGE_ORPHAN_SNAPSHOT_DELETE_ENABLED': 'true'})
    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_delayed_replacement_cannot_delete_its_original_attempt_discovery(self, dispatch, _payload):
        from apps.storage.services.internal.orphan_snapshots import _record

        dispatch.side_effect = lambda **kwargs: self.child(kwargs)
        rt = self.operation()

        def record_then_interrupt(*args, **kwargs):
            _record(*args, **kwargs)
            raise ConnectionError('interrupted after discovery')

        with patch(
            'apps.storage.services.internal.orphan_snapshots._record',
            side_effect=record_then_interrupt,
        ):
            self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'retry_scheduled')
        RepositoryOrphanSnapshot.objects.update(first_seen_at=timezone.now() - timedelta(days=2))
        retry = self.retry_due(rt)
        self.assertEqual(run_orphan_reconciliation(retry.id)['status'], 'success')
        self.assertEqual(dispatch.call_count, 2)  # Only inventory, never deletion.
        self.assertEqual(RepositoryOrphanSnapshot.objects.get().status, 'candidate')
        self.assertTrue(rt.task.events.filter(metadata__outcome='discovered').exists())
        self.assertFalse(retry.task.events.filter(metadata__outcome='deleted').exists())

    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_wrong_tenant_owner_cannot_dispatch(self, dispatch):
        from apps.iam.models import Organization
        other = Organization.objects.create(key='other-owner', name='Other')
        self.node.organization_id = other.id
        self.node.save()
        rt = self.operation()
        run_orphan_reconciliation(rt.id)
        retry = self.retry_due(rt)
        run_orphan_reconciliation(retry.id)
        dispatch.assert_not_called()
        rt.task.refresh_from_db()
        self.assertEqual(rt.task.error_code, 'ORPHAN_OWNER_MISSING')

    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_mismatched_durable_child_keeps_gate_without_replacement(self, dispatch):
        from django.core.exceptions import ValidationError
        from apps.storage.services.internal.orphan_snapshot_fence import assert_no_orphan_cleanup

        rt = self.operation()
        other_node = Node.objects.create(organization_id=self.org.id, name='other-writer', role='agent')
        NodeTask.objects.create(
            organization_id=self.org.id, requesting_organization_id=self.org.id,
            node=other_node, parent_task=rt.task, kind='repository.operation',
            correlation_type='repository_orphan',
            correlation_id=f'{rt.task.task_uuid}:snapshot.inventory:',
            payload={'operation_type': 'snapshot.inventory'},
            watchdog_deadline_at=timezone.now(), status='running',
        )
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'waiting')
        dispatch.assert_not_called()
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)

    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_node_delete_wait_resume_keeps_gate_and_exact_identity(self, dispatch, _payload):
        from apps.storage.services.internal.orphan_snapshot_fence import assert_no_orphan_cleanup
        from django.core.exceptions import ValidationError

        def execute(**kwargs):
            if kwargs['payload']['operation_type'] == 'snapshot.orphan_delete':
                self.assertEqual(kwargs['payload']['snapshot_ids'], ['a' * 32])
                return self.child(kwargs, status='running', result={})
            return self.child(kwargs)

        dispatch.side_effect = execute
        first = self.operation()
        run_orphan_reconciliation(first.id)
        RepositoryOrphanSnapshot.objects.update(first_seen_at=timezone.now() - timedelta(days=2))
        second = self.operation()
        self.assertEqual(run_orphan_reconciliation(second.id)['status'], 'waiting')
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)
        child = second.task.node_tasks.get(payload__operation_type='snapshot.orphan_delete')
        child.status = 'success'
        child.result = {'snapshot_id': 'a' * 32, 'deleted': True}
        child.save()
        self.assertEqual(run_orphan_reconciliation(second.id)['status'], 'success')
        self.assertEqual(dispatch.call_count, 3)  # One inventory per task, one deletion.
        self.assertEqual(RepositoryOrphanSnapshot.objects.get().status, 'deleted')
        self.assertFalse(second.task.events.filter(metadata__outcome='discovered').exists())
        self.assertTrue(second.task.events.filter(metadata__outcome='deleted').exists())
        assert_no_orphan_cleanup(self.repository.id)

    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_unknown_agent_inventory_never_dispatches_again_or_releases_gate(self, dispatch, _payload):
        from apps.storage.services.internal.orphan_snapshot_fence import assert_no_orphan_cleanup
        from django.core.exceptions import ValidationError

        dispatch.side_effect = lambda **kwargs: self.child(kwargs, status='timeout', result={})
        rt = self.operation()
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'waiting')
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'waiting')
        self.assertEqual(dispatch.call_count, 1)
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)

    @patch('apps.storage.services.internal.repository_access.repository_payload_for_node', return_value={})
    @patch('apps.storage.services.internal.orphan_snapshots.run_agent_task_async')
    def test_transient_storage_failure_is_retryable_but_inventory_unknown(self, dispatch, _payload):
        dispatch.side_effect = ConnectionError('AWS_SECRET_ACCESS_KEY=do-not-echo')
        rt = self.operation()
        self.assertEqual(run_orphan_reconciliation(rt.id)['status'], 'retry_scheduled')
        retry = self.retry_due(rt)
        self.assertEqual(run_orphan_reconciliation(retry.id)['status'], 'failed')
        retry.task.refresh_from_db()
        state = self.target.maintenance_state
        state.refresh_from_db()
        self.assertEqual(retry.task.recovery_attempt, 1)
        self.assertGreater(state.next_reconcile_due_at, timezone.now() + timedelta(hours=47))
        self.assertEqual(retry.task.error_code, 'ORPHAN_STORAGE_UNAVAILABLE')
        self.assertIsNone(retry.task.result_payload['orphan_snapshot_summary']['scanned'])
        self.assertNotIn('do-not-echo', str(retry.task.result_payload))
