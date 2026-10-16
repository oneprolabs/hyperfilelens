"""Repository creation must not mistake unprojected capabilities for old Agents."""

from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.storage.repositories.models import Repository, RepositoryTask
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryAgentOperationStateUnknown,
    RepositoryCreateAgentTaskResult,
)
from apps.storage.services.internal.repository_capabilities import (
    CAPABILITIES_NOT_READY,
    RepositoryCapabilitiesError,
    require_repository_ownership_capability,
)
from apps.storage.services.internal.repository_create import (
    _fail_create_keep_row,
    _set_create_step,
    enqueue_repository_create_task,
    run_repository_create_task,
)
from apps.storage.services.internal.repository_errors import RepositoryAlreadyExistsError
from apps.storage.services.internal.repository_location import reserve_repository_location
from apps.storage.services.internal.nas_repository import NASRepositoryError
from apps.storage.tasks import reconcile_repository_operations
from apps.task.models import Task, TaskStep
from apps.task.services.interface import resume_waiting_task


class RepositoryCapabilityReadinessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(key="capability-readiness", name="Readiness")
        self.node = Node.objects.create(
            organization=self.org, name="proxy", role=Node.Role.PROXY,
            availability=Node.Availability.ONLINE, version="0.3.7",
        )
        self.route = mock.patch(
            "apps.storage.services.internal.repository_capabilities."
            "redis_store.get_agent_session", return_value="current",
        ).start()
        self.addCleanup(mock.patch.stopall)
        self.dispatch = mock.patch(
            "apps.storage.tasks.execute_repository_operation.apply_async",
        ).start()
        self.initialize = mock.patch(
            "apps.storage.services.internal.repository_create._run_initialize",
        ).start()
        mock.patch(
            "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh",
        ).start()

    def snapshot(self, capabilities=None, *, session="current"):
        self.node.metadata = {
            "inventory_session_id": session,
            "inventory_capabilities_session_id": session,
            "inventory": {"capabilities": (
                ["repository_ownership_v1"] if capabilities is None else capabilities
            )},
        }
        self.node.save(update_fields=["metadata"])

    def create(self, repo_type=Repository.Type.NAS):
        repository = Repository.objects.create(
            organization_id=self.org.id, name=f"repo-{repo_type}",
            repo_type=repo_type, nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATING,
            bind_node_type=Repository.BindNodeType.PROXY, bind_node_id=self.node.id,
            config={
                "server_address": "10.0.0.10", "share_path": "/backup",
                "proxy_node_base_dir": "/data", "proxy_node_dir": "/data/repo",
            },
        )
        claim = reserve_repository_location(repository)
        operation = enqueue_repository_create_task(repository=repository, dispatch=False)
        return repository, claim, operation

    def run_create(self, operation):
        return run_repository_create_task(repository_task_id=operation.id)

    def test_create_before_inventory_waits_without_physical_work(self):
        for repo_type in (Repository.Type.NAS, Repository.Type.PROXY_FS):
            with self.subTest(repo_type=repo_type):
                repository, claim, operation = self.create(repo_type)
                original_claim_state = claim.state
                with self.captureOnCommitCallbacks(execute=True):
                    result = self.run_create(operation)
                operation.task.refresh_from_db()
                claim.refresh_from_db()
                self.assertEqual(result["status"], Task.Status.WAITING)
                self.assertEqual(operation.task.error_code, CAPABILITIES_NOT_READY)
                self.assertEqual(operation.task.current_step, "verify_repository_owner")
                self.assertEqual(operation.task.steps.get(
                    step_name="initialize_repository",
                ).status, TaskStep.Status.PENDING)
                self.assertEqual(claim.state, original_claim_state)
                self.assertFalse(NodeTask.objects.filter(parent_task=operation.task).exists())
                self.assertEqual(repository.bind_node_id, self.node.id)
        self.initialize.assert_not_called()
        self.assertEqual(self.dispatch.call_count, 2)
        self.assertEqual(self.dispatch.call_args.kwargs["countdown"], 5)
        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)

    def test_same_task_continues_after_snapshot_is_projected(self):
        repository, _, operation = self.create()
        self.run_create(operation)
        self.snapshot()
        RepositoryTask.objects.filter(pk=operation.pk).update(due_at=timezone.now())
        self.assertEqual(self.run_create(operation)["status"], "success")
        self.assertEqual(self.run_create(operation)["status"], "success")
        self.initialize.assert_called_once()
        self.assertFalse(self.initialize.call_args.kwargs["recovery"])
        self.assertEqual(RepositoryTask.objects.filter(repository=repository).count(), 1)

    def test_wait_deadline_is_not_extended_and_timeout_is_not_upgrade(self):
        _, claim, operation = self.create()
        original_state = claim.state
        self.run_create(operation)
        operation.task.refresh_from_db()
        payload = dict(operation.task.result_payload)
        RepositoryTask.objects.filter(pk=operation.pk).update(due_at=timezone.now())
        self.run_create(operation)
        operation.task.refresh_from_db()
        self.assertEqual(operation.task.result_payload, payload)
        payload["capabilities_wait_deadline"] = (
            timezone.now() - timedelta(seconds=1)
        ).isoformat()
        Task.objects.filter(pk=operation.task_id).update(result_payload=payload)
        RepositoryTask.objects.filter(pk=operation.pk).update(due_at=timezone.now())
        result = self.run_create(operation)
        operation.task.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(operation.task.error_code, CAPABILITIES_NOT_READY)
        self.assertIn("timed out", operation.task.error_message)
        self.assertFalse(operation.task.steps.filter(status=TaskStep.Status.RUNNING).exists())
        self.assertEqual(claim.state, original_state)
        self.initialize.assert_not_called()

    def test_explicitly_unsupported_snapshot_requires_upgrade(self):
        self.snapshot([])
        _, _, operation = self.create()
        result = self.run_create(operation)
        operation.task.refresh_from_db()
        self.assertEqual(result["error_code"], "AGENT_UPGRADE_REQUIRED")
        self.assertEqual(operation.task.status, Task.Status.FAILED)
        self.assertEqual(operation.task.current_step, "verify_repository_owner")
        self.assertFalse(operation.task.steps.filter(status=TaskStep.Status.RUNNING).exists())
        self.initialize.assert_not_called()

    def test_evidence_wait_does_not_allow_adopting_existing_storage(self):
        _, _, operation = self.create()
        self.run_create(operation)
        self.snapshot()
        RepositoryTask.objects.filter(pk=operation.pk).update(due_at=timezone.now())
        self.initialize.side_effect = RepositoryAlreadyExistsError("already exists")
        with mock.patch(
            "apps.storage.services.internal.repository_create._verify_existing_repository_access",
        ) as verify:
            self.assertEqual(self.run_create(operation)["status"], "failed")
        verify.assert_not_called()

    def test_crash_after_resuming_evidence_wait_is_still_first_physical_attempt(self):
        _, _, operation = self.create()
        self.run_create(operation)
        resume_waiting_task(
            task_uuid=operation.task.task_uuid,
            organization_id=self.org.id,
        )
        self.snapshot()
        self.initialize.side_effect = RepositoryAlreadyExistsError("already exists")
        with mock.patch(
            "apps.storage.services.internal.repository_create._verify_existing_repository_access",
        ) as verify:
            self.assertEqual(self.run_create(operation)["status"], "failed")
        self.assertFalse(self.initialize.call_args.kwargs["recovery"])
        verify.assert_not_called()

    def test_crash_after_resume_does_not_restart_capability_wait_deadline(self):
        _, _, operation = self.create()
        self.run_create(operation)
        operation.task.refresh_from_db()
        payload = dict(operation.task.result_payload)
        payload["capabilities_wait_deadline"] = (
            timezone.now() - timedelta(seconds=1)
        ).isoformat()
        Task.objects.filter(pk=operation.task_id).update(result_payload=payload)
        resume_waiting_task(
            task_uuid=operation.task.task_uuid,
            organization_id=self.org.id,
        )
        result = self.run_create(operation)
        operation.task.refresh_from_db()
        self.assertEqual(result["status"], Task.Status.FAILED)
        self.assertEqual(operation.task.error_code, CAPABILITIES_NOT_READY)
        self.assertIn("timed out", operation.task.error_message)
        self.assertFalse(operation.task.steps.filter(
            status=TaskStep.Status.RUNNING,
        ).exists())
        self.initialize.assert_not_called()

    def test_duplicate_callback_before_due_does_not_resume_or_dispatch(self):
        _, _, operation = self.create()
        self.run_create(operation)
        event_count = operation.task.events.count()
        self.assertEqual(self.run_create(operation)["status"], Task.Status.WAITING)
        self.assertEqual(operation.task.events.count(), event_count)
        self.initialize.assert_not_called()

    def test_failed_enqueue_keeps_wait_durable_for_periodic_recovery(self):
        _, _, operation = self.create()
        self.dispatch.side_effect = RuntimeError("broker unavailable")
        with self.assertLogs(
            "apps.storage.services.internal.repository_create", level="ERROR",
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.run_create(operation)["status"], Task.Status.WAITING)
        operation.task.refresh_from_db()
        self.assertIn("capabilities_wait_deadline", operation.task.result_payload)
        self.initialize.assert_not_called()

    def test_stale_failure_cannot_overwrite_a_terminal_task(self):
        repository, _, operation = self.create()
        Task.objects.filter(pk=operation.task_id).update(
            status=Task.Status.SUCCESS, current_step="finalize_repository_create",
            progress=100,
        )
        Repository.objects.filter(pk=repository.pk).update(
            status=Repository.Status.CREATED,
        )
        _fail_create_keep_row(
            operation, error_code="STALE_FAILURE", message="late failure",
            physical_initialize_done=False,
        )
        operation.task.refresh_from_db()
        repository.refresh_from_db()
        self.assertEqual(operation.task.status, Task.Status.SUCCESS)
        self.assertEqual(repository.status, Repository.Status.CREATED)

    def test_old_session_missing_route_and_malformed_snapshot_are_not_ready(self):
        for capabilities, session, route in (
            (["repository_ownership_v1"], "old", "current"),
            (["repository_ownership_v1"], "current", None),
            ("repository_ownership_v1", "current", "current"),
            ([None], "current", "current"),
        ):
            with self.subTest(capabilities=capabilities, session=session, route=route):
                self.snapshot(capabilities, session=session)
                self.route.return_value = route
                with self.assertRaises(RepositoryCapabilitiesError) as raised:
                    require_repository_ownership_capability(self.node)
                self.assertEqual(raised.exception.error_code, CAPABILITIES_NOT_READY)

    def test_failure_marks_latest_step_not_stale_related_task(self):
        self.snapshot()
        _, _, operation = self.create()
        self.initialize.side_effect = NASRepositoryError("write denied")
        self.assertEqual(self.run_create(operation)["status"], "failed")
        operation.task.refresh_from_db()
        steps = dict(operation.task.steps.values_list("step_name", "status"))
        self.assertEqual(steps["verify_repository_owner"], TaskStep.Status.SUCCESS)
        self.assertEqual(steps["initialize_repository"], TaskStep.Status.FAILED)
        self.assertNotIn(TaskStep.Status.RUNNING, steps.values())
        self.assertEqual(operation.task.current_step, "initialize_repository")

    def test_step_checkpoint_rolls_back_when_task_save_fails(self):
        _, _, operation = self.create()
        Task.objects.filter(pk=operation.task_id).update(
            status=Task.Status.RUNNING,
            current_step="verify_repository_owner",
            progress=35,
        )
        operation.task.steps.filter(step_name="verify_repository_owner").update(
            status=TaskStep.Status.SUCCESS, progress=35,
        )
        # A step transition must not commit its running row if saving the
        # parent checkpoint fails. Otherwise even a fresh Task names the
        # previous successful step during failure finalization.
        with mock.patch.object(Task, "save", side_effect=RuntimeError("save failed")):
            with self.assertRaisesMessage(RuntimeError, "save failed"):
                _set_create_step(
                    operation.task, "initialize_repository", TaskStep.Status.RUNNING, 45,
                )
        operation.task.refresh_from_db()
        steps = dict(operation.task.steps.values_list("step_name", "status"))
        self.assertEqual(steps["verify_repository_owner"], TaskStep.Status.SUCCESS)
        self.assertEqual(steps["initialize_repository"], TaskStep.Status.PENDING)
        self.assertNotIn(TaskStep.Status.RUNNING, steps.values())
        self.assertEqual(operation.task.current_step, "verify_repository_owner")
        _fail_create_keep_row(
            operation, error_code="INITIALIZE_FAILED", message="checkpoint failed",
            physical_initialize_done=False,
        )
        operation.task.refresh_from_db()
        steps = dict(operation.task.steps.values_list("step_name", "status"))
        self.assertEqual(steps["verify_repository_owner"], TaskStep.Status.SUCCESS)
        self.assertEqual(steps["initialize_repository"], TaskStep.Status.FAILED)
        self.assertNotIn(TaskStep.Status.RUNNING, steps.values())

    def test_reconcile_respects_next_capability_check_time(self):
        _, _, operation = self.create()
        self.run_create(operation)
        self.assertEqual(reconcile_repository_operations()["redispatched"], 0)
        RepositoryTask.objects.filter(pk=operation.pk).update(due_at=timezone.now())
        self.assertEqual(reconcile_repository_operations()["redispatched"], 1)

    def test_pending_durable_initialization_does_not_repeat_capability_gate(self):
        _, _, operation = self.create()
        child = NodeTask.objects.create(
            organization=self.org, node=self.node, parent_task=operation.task,
            kind="repo.initialize", correlation_type="repository_create",
            correlation_id=str(operation.task.task_uuid),
            status=NodeTask.Status.RUNNING, watchdog_deadline_at=timezone.now(),
        )
        self.initialize.return_value = RepositoryCreateAgentTaskResult(
            waiting=True, state_unknown=False, node_task_id=child.id, result={},
        )
        with mock.patch(
            "apps.storage.services.internal.repository_create."
            "require_repository_ownership_capability",
        ) as capability_gate:
            self.assertEqual(self.run_create(operation)["status"], Task.Status.WAITING)
        capability_gate.assert_not_called()
        self.initialize.assert_called_once()

    def test_remote_reference_keeps_unknown_outcome_instead_of_capability_wait(self):
        _, _, operation = self.create()
        operation.remote_task_id = uuid4()
        operation.save(update_fields=["remote_task_id"])
        self.initialize.side_effect = RepositoryAgentOperationStateUnknown(
            "The durable initialization child could not be found.",
        )
        with mock.patch(
            "apps.storage.services.internal.repository_create."
            "require_repository_ownership_capability",
        ) as capability_gate:
            result = self.run_create(operation)
        operation.task.refresh_from_db()
        self.assertEqual(result["status"], Task.Status.BLOCKED)
        self.assertEqual(operation.task.error_code, "REMOTE_RESULT_UNKNOWN")
        self.assertNotIn(
            "capabilities_wait_deadline", operation.task.result_payload or {},
        )
        capability_gate.assert_not_called()
