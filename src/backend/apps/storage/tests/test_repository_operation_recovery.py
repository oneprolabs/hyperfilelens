from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.storage.repositories.models import Repository, RepositoryTask
from apps.storage.services.internal.repository_cleanup import (
    create_repository_cleanup_task,
    run_repository_cleanup_task,
)
from apps.storage.services.internal.repository_create import (
    enqueue_repository_create_task,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryAgentOperationError,
    RepositoryAgentOperationStateUnknown,
    queue_repository_agent_result_followup,
    resolve_or_dispatch_repository_create_agent_task,
)
from apps.storage.services.internal.repository_operations import (
    create_repository_operation_task,
    discover_repository_execution_targets,
    fence_controller_repository_operation,
    finalize_repository_operation,
    set_controller_repository_operation_step,
    set_task_step,
    start_controller_repository_operation,
)
from apps.storage.tasks import (
    execute_repository_operation,
    reconcile_repository_operations,
)
from apps.task.models import Task, TaskStep
from apps.task.services.interface import start_task
from apps.task.services.recovery import CONTROL_PLANE_RESTART_INTERRUPTED


class RepositoryOperationRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(
            key="repository-operation-recovery",
            name="Repository Operation Recovery",
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="recovery-proxy",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.50",
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_operation_v1",
                        "repository_cleanup_v1",
                        "repository_cleanup_v2",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="recovery-repository",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.node.id,
            config={
                "proxy_node_base_dir": "/data",
                "proxy_node_dir": "/data/hfl-repo-recovery",
                "proxy_fs_layout": "managed_subdir_v1",
            },
        )
        reserve_repository_location(self.repository)
        mark_repository_location_owned(self.repository)
        mark_repository_location_ownership_verified(self.repository)
        discover_repository_execution_targets()

    def _maintenance_task(self) -> RepositoryTask:
        return create_repository_operation_task(
            target_id=self.repository.execution_targets.get().id,
            operation_type=RepositoryTask.OperationType.MAINTENANCE_QUICK,
        )

    def _start_maintenance(self, repository_task: RepositoryTask) -> None:
        start_task(
            task_uuid=repository_task.task.task_uuid,
            organization_id=self.org.id,
        )
        set_task_step(
            repository_task.task,
            "run_repository_operation",
            status=TaskStep.Status.RUNNING,
            progress=25,
        )

    def test_sealed_delivery_timeout_is_a_retryable_failure_not_unknown_state(self):
        self.repository.status = Repository.Status.CREATE_FAILED
        self.repository.save(update_fields=["status", "updated_at"])
        repository_task = enqueue_repository_create_task(
            repository=self.repository,
            dispatch=False,
        )
        start_task(
            task_uuid=repository_task.task.task_uuid,
            organization_id=self.org.id,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.node,
            parent_task=repository_task.task,
            correlation_type="repository_create",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repo.initialize",
            payload={
                "repository_id": self.repository.id,
                "operation_type": repository_task.operation_type,
            },
            status=NodeTask.Status.TIMEOUT,
            result={
                "diagnostic_error_code": "AGENT_ACK_TIMEOUT",
                "delivery_timeout_sealed": True,
            },
            last_error="AGENT_ACK_TIMEOUT: Agent did not accept task.command",
            watchdog_deadline_at=timezone.now(),
        )

        with self.assertRaises(RepositoryAgentOperationError) as context:
            resolve_or_dispatch_repository_create_agent_task(
                repository_task=repository_task,
                node=self.node,
                payload={"repository_id": self.repository.id},
                persisted_payload={"repository_id": self.repository.id},
            )

        self.assertEqual(context.exception.result["error_code"], "AGENT_ACK_TIMEOUT")
        self.assertNotIsInstance(
            context.exception,
            RepositoryAgentOperationStateUnknown,
        )
        self.assertEqual(repository_task.remote_task_id, node_task.id)
        self.assertEqual(node_task.status, NodeTask.Status.TIMEOUT)

    def _controller_maintenance_task(self) -> RepositoryTask:
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="controller-recovery-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="controller-recovery-bucket",
            config={"prefix": "controller/recovery"},
        )
        reserve_repository_location(repository)
        mark_repository_location_owned(repository)
        mark_repository_location_ownership_verified(repository)
        discover_repository_execution_targets()
        return create_repository_operation_task(
            target_id=repository.execution_targets.get().id,
            operation_type=RepositoryTask.OperationType.MAINTENANCE_QUICK,
        )

    @mock.patch("apps.storage.tasks.sync_organization_repositories")
    @mock.patch(
        "apps.storage.services.internal.repository_agent_operation.run_agent_task_async"
    )
    def test_recovers_successful_agent_child_by_correlation_without_redispatch(
        self,
        run_agent_task_async,
        sync_repositories,
    ):
        repository_task = self._maintenance_task()
        self._start_maintenance(repository_task)
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            correlation_type="repository_operation",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.SUCCESS,
            result={
                "operation_type": "maintenance.quick",
                "maintenance": {"exit_code": 0},
            },
            watchdog_deadline_at=timezone.now(),
        )

        result = execute_repository_operation.run(repository_task_id=repository_task.id)

        repository_task.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(repository_task.remote_task_id, node_task.id)
        self.assertEqual(repository_task.task.status, Task.Status.SUCCESS)
        self.assertTrue(
            repository_task.task.events.filter(
                message="Control-plane recovery decision: resume"
            ).exists()
        )
        run_agent_task_async.assert_not_called()
        sync_repositories.assert_called_once()

    def test_active_agent_child_keeps_parent_running(self):
        repository_task = self._maintenance_task()
        self._start_maintenance(repository_task)
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            correlation_type="repository_operation",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=5),
        )

        result = execute_repository_operation.run(repository_task_id=repository_task.id)

        repository_task.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(repository_task.remote_task_id, node_task.id)
        self.assertEqual(repository_task.task.status, Task.Status.RUNNING)

    @mock.patch("apps.storage.tasks.execute_repository_operation.apply_async")
    def test_terminal_agent_result_queues_repository_parent_by_correlation(
        self,
        apply_async,
    ):
        repository_task = self._maintenance_task()
        self._start_maintenance(repository_task)
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            correlation_type="repository_operation",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.SUCCESS,
            result={"maintenance": {"exit_code": 0}},
            watchdog_deadline_at=timezone.now(),
        )

        queued = queue_repository_agent_result_followup(node_task=node_task)

        self.assertTrue(queued)
        apply_async.assert_called_once_with(
            kwargs={"repository_task_id": repository_task.id},
            countdown=1,
        )

    @mock.patch("apps.storage.tasks.execute_repository_operation.apply_async")
    @mock.patch("apps.storage.tasks.cache.add", return_value=False)
    def test_cleanup_lock_conflict_reschedules_instead_of_dropping_wakeup(
        self,
        _cache_add,
        apply_async,
    ):
        repository_task = create_repository_cleanup_task(
            repository=self.repository,
            dispatch=False,
        )

        result = execute_repository_operation.run(
            repository_task_id=repository_task.id,
        )

        self.assertEqual(result["status"], "rescheduled")
        self.assertEqual(result["retry_in_seconds"], 3)
        apply_async.assert_called_once_with(
            kwargs={"repository_task_id": repository_task.id},
            countdown=3,
        )

    @mock.patch(
        "apps.storage.services.internal.repository_agent_operation.run_agent_task_async"
    )
    def test_unknown_maintenance_state_fails_without_redispatch_or_replacement(
        self,
        run_agent_task_async,
    ):
        repository_task = self._maintenance_task()
        self._start_maintenance(repository_task)

        result = execute_repository_operation.run(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        repository_task.execution_target.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(
            repository_task.task.error_code,
            CONTROL_PLANE_RESTART_INTERRUPTED,
        )
        self.assertIsNone(repository_task.execution_target.active_task_id)
        self.assertFalse(hasattr(repository_task.task, "replacement_task"))
        run_agent_task_async.assert_not_called()

    def test_failed_agent_child_fails_parent_and_releases_target(self):
        repository_task = self._maintenance_task()
        self._start_maintenance(repository_task)
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            correlation_type="repository_operation",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.FAILED,
            last_error="agent maintenance failed",
            watchdog_deadline_at=timezone.now(),
        )

        result = execute_repository_operation.run(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        repository_task.execution_target.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(
            repository_task.task.error_code,
            "REPOSITORY_OPERATION_FAILED",
        )
        self.assertEqual(
            repository_task.task.error_message,
            "agent maintenance failed",
        )
        self.assertIsNone(repository_task.execution_target.active_task_id)

    @mock.patch("apps.storage.tasks.execute_repository_operation.apply_async")
    def test_reconciler_queues_only_active_agent_operations(self, apply_async):
        active = self._maintenance_task()
        late_repository = Repository.objects.create(
            organization_id=self.org.id,
            name="late-create-result",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.node.id,
            config={"proxy_node_dir": "/data/late-create-result"},
        )
        late_create = enqueue_repository_create_task(
            repository=late_repository,
            dispatch=False,
        )
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            parent_task=late_create.task,
            correlation_type="repository_create",
            correlation_id=str(late_create.task.task_uuid),
            kind="repo.initialize",
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
        )
        # Simulate a control-plane interruption after the Agent task committed
        # but before RepositoryTask.remote_task_id was persisted.
        Task.objects.filter(id=late_create.task_id).update(status=Task.Status.BLOCKED)

        result = reconcile_repository_operations.run(limit=10)

        self.assertCountEqual(
            result["repository_task_ids"],
            [active.id, late_create.id],
        )
        self.assertEqual(apply_async.call_count, 2)
        apply_async.assert_any_call(kwargs={"repository_task_id": active.id})
        apply_async.assert_any_call(kwargs={"repository_task_id": late_create.id})

    def test_fresh_controller_heartbeat_keeps_parent_running(self):
        repository_task = self._controller_maintenance_task()
        token = start_controller_repository_operation(
            repository_task_id=repository_task.id
        )
        self.assertIsNotNone(token)

        result = execute_repository_operation.run(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        repository_task.execution_target.refresh_from_db()
        self.assertEqual(result["status"], Task.Status.RUNNING)
        self.assertEqual(repository_task.task.status, Task.Status.RUNNING)
        self.assertEqual(
            repository_task.execution_target.active_task_id,
            repository_task.task_id,
        )

    def test_stale_controller_heartbeat_fails_and_releases_target(self):
        repository_task = self._controller_maintenance_task()
        token = start_controller_repository_operation(
            repository_task_id=repository_task.id
        )
        self.assertIsNotNone(token)
        RepositoryTask.objects.filter(pk=repository_task.id).update(
            execution_heartbeat_at=timezone.now() - timedelta(seconds=61)
        )

        result = execute_repository_operation.run(repository_task_id=repository_task.id)

        repository_task.refresh_from_db()
        repository_task.task.refresh_from_db()
        repository_task.execution_target.refresh_from_db()
        self.assertEqual(result["status"], Task.Status.FAILED)
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(
            repository_task.task.error_code,
            CONTROL_PLANE_RESTART_INTERRUPTED,
        )
        self.assertIsNone(repository_task.execution_target.active_task_id)
        self.assertIsNone(repository_task.execution_token)

    def test_legacy_tokenless_controller_waits_for_timeout_then_recovers(self):
        repository_task = self._controller_maintenance_task()
        start_task(
            task_uuid=repository_task.task.task_uuid,
            organization_id=self.org.id,
        )

        waiting = execute_repository_operation.run(
            repository_task_id=repository_task.id
        )
        self.assertEqual(waiting["status"], Task.Status.RUNNING)

        Task.objects.filter(pk=repository_task.task_id).update(
            started_at=timezone.now() - timedelta(hours=6, seconds=1)
        )
        recovered = execute_repository_operation.run(
            repository_task_id=repository_task.id
        )

        repository_task.task.refresh_from_db()
        repository_task.execution_target.refresh_from_db()
        self.assertEqual(recovered["status"], Task.Status.FAILED)
        self.assertEqual(
            repository_task.task.error_code,
            CONTROL_PLANE_RESTART_INTERRUPTED,
        )
        self.assertIsNone(repository_task.execution_target.active_task_id)

    def test_lost_controller_token_cannot_update_steps_or_finalize(self):
        repository_task = self._controller_maintenance_task()
        old_token = start_controller_repository_operation(
            repository_task_id=repository_task.id
        )
        recovery_token = fence_controller_repository_operation(
            repository_task_id=repository_task.id,
            expected_execution_token=old_token,
        )
        self.assertIsNotNone(recovery_token)

        decision = set_controller_repository_operation_step(
            repository_task_id=repository_task.id,
            expected_execution_token=old_token,
            step_name="run_repository_operation",
            status=TaskStep.Status.SUCCESS,
            progress=80,
        )
        stale_result = finalize_repository_operation(
            repository_task_id=repository_task.id,
            succeeded=True,
            expected_execution_token=old_token,
        )

        repository_task.task.refresh_from_db()
        self.assertEqual(decision, "lost_lease")
        self.assertEqual(stale_result.status, Task.Status.RUNNING)
        self.assertEqual(repository_task.task.status, Task.Status.RUNNING)
        self.assertNotEqual(
            repository_task.task.steps.get(step_name="run_repository_operation").status,
            TaskStep.Status.SUCCESS,
        )

    def test_uncertain_cleanup_fails_original_and_creates_replacement(self):
        repository_task = create_repository_cleanup_task(
            repository=self.repository,
            dispatch=False,
        )
        start_task(
            task_uuid=repository_task.task.task_uuid,
            organization_id=self.org.id,
        )
        set_task_step(
            repository_task.task,
            "delete_physical_repository",
            status=TaskStep.Status.RUNNING,
            progress=40,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        replacement = repository_task.task.replacement_task
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(
            repository_task.task.error_code,
            CONTROL_PLANE_RESTART_INTERRUPTED,
        )
        self.assertEqual(replacement.status, Task.Status.PENDING)
        self.assertEqual(replacement.trigger_type, Task.TriggerType.RETRY)
        self.assertEqual(replacement.recovery_attempt, 1)
        self.assertEqual(
            replacement.repository_operation.operation_type,
            repository_task.operation_type,
        )

        repeated = run_repository_cleanup_task(repository_task_id=repository_task.id)
        self.assertEqual(repeated["status"], Task.Status.FAILED)
        self.assertEqual(
            Task.objects.filter(replaces_task=repository_task.task).count(), 1
        )

    @mock.patch(
        "apps.storage.services.internal.repository_agent_operation.run_agent_task_async"
    )
    def test_cleanup_resumes_metadata_after_recovered_successful_child(
        self,
        run_agent_task_async,
    ):
        repository_task = create_repository_cleanup_task(
            repository=self.repository,
            dispatch=False,
        )
        start_task(
            task_uuid=repository_task.task.task_uuid,
            organization_id=self.org.id,
        )
        set_task_step(
            repository_task.task,
            "delete_physical_repository",
            status=TaskStep.Status.RUNNING,
            progress=40,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            correlation_type="repository_cleanup",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.SUCCESS,
            result={"deleted": True},
            watchdog_deadline_at=timezone.now(),
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository_task.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(repository_task.remote_task_id, node_task.id)
        self.assertEqual(repository_task.task.status, Task.Status.SUCCESS)
        run_agent_task_async.assert_not_called()

    def test_failed_agent_cleanup_persists_owner_proof_for_manual_retry(self):
        repository_task = create_repository_cleanup_task(
            repository=self.repository,
            dispatch=False,
        )
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            correlation_type="repository_cleanup",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.FAILED,
            result={"ownership_verified": True},
            last_error="physical delete stopped after ownership verification",
            watchdog_deadline_at=timezone.now(),
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            repository_task.task.request_payload["agent_cleanup_owner_verified"]
        )

    def test_cleanup_stops_replacing_after_maximum_attempts(self):
        repository_task = create_repository_cleanup_task(
            repository=self.repository,
            dispatch=False,
        )
        repository_task.task.recovery_attempt = 3
        repository_task.task.save(update_fields=["recovery_attempt", "updated_at"])
        start_task(
            task_uuid=repository_task.task.task_uuid,
            organization_id=self.org.id,
        )
        set_task_step(
            repository_task.task,
            "delete_physical_repository",
            status=TaskStep.Status.RUNNING,
            progress=40,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        self.assertEqual(result["replacement_task_uuid"], None)
        self.assertFalse(hasattr(repository_task.task, "replacement_task"))
