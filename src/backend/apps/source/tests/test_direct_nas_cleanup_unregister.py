from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node, NodeTask
from apps.node.services.internal.node_lifecycle import NodeLifecycleError
from apps.protection.models import BackupConfig
from apps.source.services.internal.backup_source_delete import (
    BackupSourceDeleteFailed,
    delete_backup_sources,
    preflight_delete_backup_sources,
    run_source_unregister_task,
)
from apps.storage.repositories.models import (
    Repository,
    RepositoryTask,
    RepositoryUsageShard,
)
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryAgentOperationResult,
)
from apps.task.models import Task, TaskResource
from apps.task.services.interface import create_task, retry_task, start_task


class DirectNasCleanupUnregisterTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="direct-nas-unregister-org",
            name="Direct NAS Unregister Org",
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="direct-nas-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": [
                "repository_cleanup_v1",
                "repository_cleanup_ownership_v1",
            ]}},
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "10.0.0.8", "share_path": "/backups"},
        )
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="direct NAS config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=self.repository.id,
        )
        self.shard = RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=self.repository.id,
            node_id=self.agent.id,
            repository_subdir=f"hp-repos/agent-{self.agent.id}",
            source_config_count=1,
            source_config_ids=[self.config.id],
            status=RepositoryUsageShard.Status.SUCCESS,
        )

    def test_active_direct_nas_repository_task_blocks_before_unregister_creation(self):
        Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name="Active repository maintenance",
            status=Task.Status.RUNNING,
            request_payload={"repository_id": self.repository.id},
        )

        preflight = preflight_delete_backup_sources(
            organization_id=self.org.id,
            ids=[f"agent:{self.agent.id}"],
        )

        self.assertTrue(preflight["delete_disabled"])
        self.assertEqual(
            [reason["code"] for reason in preflight["blocking"]],
            ["repository_cleanup_blocked"],
        )
        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
                force=True,
            )
        self.assertEqual(
            raised.exception.reasons[0].code,
            "repository_cleanup_blocked",
        )
        self.assertFalse(
            Task.objects.filter(task_type=Task.Type.SOURCE_UNREGISTER).exists()
        )

    def test_active_direct_nas_cleanup_child_from_other_parent_blocks_unregister(self):
        parent = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Other source unregister",
            status=Task.Status.RUNNING,
        )
        child = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name="Active physical repository cleanup",
            status=Task.Status.RUNNING,
            request_payload={
                "repository_id": self.repository.id,
                "source_unregister_attempt": 0,
            },
        )
        RepositoryTask.objects.create(
            task=child,
            repository=self.repository,
            triggered_by_task=parent,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
            owner_type="controller",
            owner_identity="hfl-cleanup@controller",
        )

        preflight = preflight_delete_backup_sources(
            organization_id=self.org.id,
            ids=[f"agent:{self.agent.id}"],
        )

        self.assertTrue(preflight["delete_disabled"])
        self.assertEqual(
            [reason["code"] for reason in preflight["blocking"]],
            ["repository_cleanup_blocked"],
        )
        with self.assertRaises(BackupSourceDeleteFailed):
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
                force=True,
            )
        self.assertEqual(
            Task.objects.filter(task_type=Task.Type.SOURCE_UNREGISTER).count(),
            1,
        )

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
        return_value={"task_id": 123, "operation_id": "remove-1", "state": "removing"},
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_unregister_runs_independent_target_cleanup_before_removing_config(
        self,
        execute_cleanup,
        start_node_remove,
        agent_status,
    ):
        result = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
        )

        cleanup_task = RepositoryTask.objects.get(
            repository=self.repository,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
        )
        source_unregister = cleanup_task.triggered_by_task
        self.repository.refresh_from_db()
        self.shard.refresh_from_db()
        cleanup_task.task.refresh_from_db()
        source_unregister.refresh_from_db()
        self.assertEqual(cleanup_task.task.status, Task.Status.SUCCESS)
        self.assertEqual(source_unregister.status, Task.Status.RUNNING)
        self.assertEqual(result["result"], "waiting")
        cleanup_plan = source_unregister.request_payload["cleanup_plan"]
        self.assertEqual(cleanup_plan["source_id"], f"agent:{self.agent.id}")
        self.assertEqual(cleanup_plan["backup_config_ids"], [self.config.id])
        self.assertEqual(cleanup_plan["repository_ids"], [self.repository.id])
        self.assertEqual(self.repository.status, Repository.Status.CREATED)
        self.assertFalse(self.shard.is_active)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())
        self.assertFalse(self.agent.is_deleted)
        self.assertEqual(
            result["repository_cleanup_tasks"][0]["triggered_by_task_uuid"],
            str(source_unregister.task_uuid),
        )
        execute_cleanup.assert_called_once()
        start_node_remove.assert_called_once()
        agent_status.assert_called()

        NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="agent.uninstall",
            status=NodeTask.Status.SUCCESS,
            payload={"source_unregister_task_id": source_unregister.id},
            result={
                "mode": "local_detached",
                "completion_received_at": timezone.now().isoformat(),
                "cleanup_complete": True,
            },
            watchdog_deadline_at=timezone.now(),
            correlation_type="node.lifecycle",
            correlation_id=f"remove:{self.agent.id}",
        )
        agent_status.return_value = "offline"

        completed = run_source_unregister_task(
            organization_id=self.org.id,
            task_uuid=str(source_unregister.task_uuid),
        )

        source_unregister.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(completed["result"], "success")
        self.assertEqual(source_unregister.status, Task.Status.SUCCESS)
        self.assertTrue(self.agent.is_deleted)
        self.assertFalse(BackupConfig.objects.filter(pk=self.config.id).exists())

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch("apps.node.services.internal.node_lifecycle.start_node_remove")
    @mock.patch(
        "apps.source.services.internal.backup_source_delete.create_direct_nas_target_cleanup_task",
        side_effect=RuntimeError("cleanup task dispatch unavailable"),
    )
    def test_force_unregister_records_direct_nas_task_creation_failure(
        self,
        _create_cleanup_task,
        start_node_remove,
        _agent_status,
    ):
        def purge_agent(**_kwargs):
            self.agent.soft_delete()
            return {
                "state": "removed",
                "purged": True,
                "cleanup_complete": True,
                "cleanup_failures": [],
                "retained_resources": [],
            }

        start_node_remove.side_effect = purge_agent

        result = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
            force=True,
        )

        unregister_task = Task.objects.get(pk=result["task_id"])
        self.agent.refresh_from_db()
        self.assertEqual(result["result"], "partial_success")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(
            result["cleanup_failures"][0]["code"],
            "repository_cleanup_create_failed",
        )
        target_id = _create_cleanup_task.call_args.kwargs["target_id"]
        self.assertEqual(
            result["retained_resources"],
            [
                f"repository_target:repository:{self.repository.id}:"
                f"target:{target_id}"
            ],
        )
        self.assertEqual(unregister_task.status, Task.Status.SUCCESS)
        self.assertTrue(self.agent.is_deleted)
        self.assertFalse(BackupConfig.objects.filter(pk=self.config.id).exists())
        start_node_remove.assert_called_once()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch("apps.node.services.internal.node_lifecycle.start_node_remove")
    @mock.patch(
        "apps.source.services.internal.backup_source_delete.create_direct_nas_target_cleanup_task",
        side_effect=RuntimeError("cleanup task dispatch unavailable"),
    )
    def test_force_cleanup_residue_survives_async_agent_uninstall(
        self,
        create_cleanup_task,
        start_node_remove,
        _agent_status,
    ):
        def create_uninstall(**kwargs):
            node_task = NodeTask.objects.create(
                organization=self.org,
                node=self.agent,
                kind="agent.uninstall",
                status=NodeTask.Status.RUNNING,
                payload={"source_unregister_task_id": kwargs["triggered_by_task_id"]},
                result={"mode": "local_detached"},
                watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=10),
                correlation_type="node.lifecycle",
                correlation_id=f"remove:{self.agent.id}",
            )
            return {
                "task_id": str(node_task.id),
                "operation_id": node_task.correlation_id,
                "state": "removing",
            }

        start_node_remove.side_effect = create_uninstall
        waiting = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
            force=True,
        )

        unregister_task = Task.objects.get(pk=waiting["task_id"])
        uninstall_task = NodeTask.objects.get(
            node=self.agent,
            kind="agent.uninstall",
        )
        uninstall_task.status = NodeTask.Status.SUCCESS
        uninstall_task.result = {
            "mode": "local_detached",
            "completion_received_at": timezone.now().isoformat(),
            "cleanup_complete": True,
        }
        uninstall_task.save(update_fields=["status", "result", "updated_at"])

        completed = run_source_unregister_task(
            organization_id=self.org.id,
            task_uuid=str(unregister_task.task_uuid),
        )

        target_id = create_cleanup_task.call_args.kwargs["target_id"]
        self.assertEqual(completed["result"], "partial_success")
        self.assertFalse(completed["cleanup_complete"])
        self.assertEqual(
            completed["cleanup_failures"][0]["code"],
            "repository_cleanup_create_failed",
        )
        self.assertEqual(
            completed["retained_resources"],
            [
                f"repository_target:repository:{self.repository.id}:"
                f"target:{target_id}"
            ],
        )
        unregister_task.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(unregister_task.status, Task.Status.SUCCESS)
        self.assertTrue(self.agent.is_deleted)
        self.assertEqual(create_cleanup_task.call_count, 2)
        start_node_remove.assert_called_once()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch("apps.node.services.internal.node_lifecycle.start_node_remove")
    @mock.patch(
        "apps.source.services.internal.backup_source_delete.direct_nas_cleanup_target_ids",
        return_value=[],
    )
    def test_force_unregister_records_unresolved_direct_nas_target(
        self,
        _target_ids,
        start_node_remove,
        _agent_status,
    ):
        def purge_agent(**_kwargs):
            self.agent.soft_delete()
            return {
                "state": "removed",
                "purged": True,
                "cleanup_complete": True,
            }

        start_node_remove.side_effect = purge_agent

        result = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
            force=True,
        )

        self.assertEqual(result["result"], "partial_success")
        self.assertEqual(
            result["cleanup_failures"][0]["code"],
            "repository_cleanup_target_missing",
        )
        self.assertEqual(
            result["retained_resources"],
            [f"repository_target:repository:{self.repository.id}:unresolved"],
        )
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.is_deleted)

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch("apps.node.services.internal.node_lifecycle.start_node_remove")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        side_effect=RuntimeError("physical cleanup failed"),
    )
    def test_force_unregister_propagates_partial_direct_nas_cleanup(
        self,
        _execute_cleanup,
        start_node_remove,
        _agent_status,
    ):
        def purge_agent(**_kwargs):
            self.agent.soft_delete()
            return {
                "state": "removed",
                "purged": True,
                "cleanup_complete": True,
                "cleanup_failures": [],
                "retained_resources": [],
            }

        start_node_remove.side_effect = purge_agent

        result = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
            force=True,
        )

        cleanup_task = RepositoryTask.objects.get(
            repository=self.repository,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
        )
        cleanup_task.task.refresh_from_db()
        self.assertEqual(cleanup_task.task.status, Task.Status.SUCCESS)
        self.assertFalse(cleanup_task.task.result_payload["cleanup_complete"])
        self.assertEqual(result["result"], "partial_success")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(
            result["cleanup_failures"][0]["code"],
            "REPOSITORY_CLEANUP_FAILED",
        )
        self.assertEqual(
            result["retained_resources"],
            cleanup_task.task.result_payload["retained_resources"],
        )
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.is_deleted)

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch("apps.node.services.internal.node_lifecycle.start_node_remove")
    @mock.patch(
        "apps.source.services.internal.backup_source_delete.run_repository_cleanup_task"
    )
    def test_force_unregister_does_not_bypass_direct_nas_cleanup_blocker(
        self,
        run_cleanup_task,
        start_node_remove,
        _agent_status,
    ):
        def block_cleanup(*, repository_task_id):
            repository_task = RepositoryTask.objects.select_related("task").get(
                pk=repository_task_id
            )
            task = repository_task.task
            task.status = Task.Status.FAILED
            task.error_code = "REPOSITORY_CLEANUP_BLOCKED"
            task.error_message = "A configured dependency still uses this target."
            task.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )
            return {"status": "failed"}

        run_cleanup_task.side_effect = block_cleanup

        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
                force=True,
            )

        self.assertEqual(
            raised.exception.reasons[0].code,
            "repository_cleanup_blocked",
        )
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_deleted)
        start_node_remove.assert_not_called()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch("apps.node.services.internal.node_lifecycle.start_node_remove")
    @mock.patch(
        "apps.source.services.internal.backup_source_delete.run_repository_cleanup_task"
    )
    def test_strict_unregister_rejects_successful_incomplete_cleanup_child(
        self,
        run_cleanup_task,
        start_node_remove,
        _agent_status,
    ):
        def finish_incomplete(*, repository_task_id):
            repository_task = RepositoryTask.objects.select_related("task").get(
                pk=repository_task_id
            )
            task = repository_task.task
            task.status = Task.Status.SUCCESS
            task.result_payload = {
                "cleanup_complete": False,
                "cleanup_failures": [
                    {
                        "code": "repository_cleanup_incomplete",
                        "detail": "A Direct NAS shard was retained.",
                    }
                ],
                "retained_resources": ["repository_shard:1"],
            }
            task.save(
                update_fields=[
                    "status",
                    "result_payload",
                    "updated_at",
                ]
            )
            return {"status": "success", **task.result_payload}

        run_cleanup_task.side_effect = finish_incomplete

        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
            )

        self.assertEqual(
            raised.exception.reasons[0].code,
            "repository_cleanup_incomplete",
        )
        self.assertEqual(
            raised.exception.reasons[0].detail,
            "A Direct NAS shard was retained.",
        )
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_deleted)
        start_node_remove.assert_not_called()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch("apps.node.services.internal.node_lifecycle.start_node_remove")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_late_backup_fails_unregister_with_domain_blocker(
        self,
        _execute_cleanup,
        start_node_remove,
        _agent_status,
    ):
        def start_remove_after_late_backup(**_kwargs):
            create_task(
                organization_id=self.org.id,
                task_type=Task.Type.BACKUP,
                display_name="Late backup",
                resources=[
                    {
                        "resource_type": TaskResource.Type.BACKUP_SOURCE,
                        "resource_subtype": "agent",
                        "resource_id": self.agent.id,
                        "is_primary": True,
                    }
                ],
            )
            return {
                "state": "completed",
                "purged": False,
                "control_plane_purge_deferred": True,
                "cleanup_complete": True,
                "cleanup_failures": [],
                "retained_resources": [],
            }

        start_node_remove.side_effect = start_remove_after_late_backup

        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
                force=True,
            )

        unregister_task = Task.objects.get(task_type=Task.Type.SOURCE_UNREGISTER)
        unregister_task.refresh_from_db()
        self.assertEqual(raised.exception.reasons[0].code, "running_tasks")
        self.assertEqual(unregister_task.status, Task.Status.FAILED)
        self.assertEqual(
            unregister_task.error_code,
            "SOURCE_UNREGISTER_FAILED",
        )
        self.assertEqual(
            unregister_task.result_payload["reasons"][0]["code"],
            "running_tasks",
        )
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_deleted)

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
        side_effect=NodeLifecycleError(
            "Agent uninstall dispatch failed.",
            code="agent_dispatch_failed",
        ),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_force_unregister_does_not_bypass_agent_lifecycle_rejection(
        self,
        _execute_cleanup,
        _start_node_remove,
        _agent_status,
    ):
        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
                force=True,
            )

        self.assertEqual(
            raised.exception.reasons[0].code,
            "agent_dispatch_failed",
        )
        unregister_task = Task.objects.get(task_type=Task.Type.SOURCE_UNREGISTER)
        self.assertEqual(unregister_task.status, Task.Status.FAILED)
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_deleted)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
        side_effect=RuntimeError("agent task broker unavailable"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_force_unregister_handles_unexpected_agent_dispatch_exception(
        self,
        _execute_cleanup,
        _start_node_remove,
        _agent_status,
    ):
        result = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
            force=True,
        )

        unregister_task = Task.objects.get(pk=result["task_id"])
        self.assertEqual(result["result"], "partial_success")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(
            result["cleanup_failures"][0]["code"],
            "agent_uninstall_dispatch_failed",
        )
        self.assertEqual(result["retained_resources"], ["agent_installation"])
        self.assertEqual(unregister_task.status, Task.Status.SUCCESS)
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.is_deleted)

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
        side_effect=RuntimeError("agent task broker unavailable"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_strict_unregister_handles_unexpected_agent_dispatch_exception(
        self,
        _execute_cleanup,
        _start_node_remove,
        _agent_status,
    ):
        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
                force=False,
            )

        unregister_task = Task.objects.get(task_type=Task.Type.SOURCE_UNREGISTER)
        self.assertEqual(
            raised.exception.reasons[0].code,
            "agent_uninstall_dispatch_failed",
        )
        self.assertEqual(unregister_task.status, Task.Status.FAILED)
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_deleted)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
        return_value={"task_id": 123, "operation_id": "remove-1", "state": "removing"},
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_force_unregister_records_failed_agent_uninstall_as_residue(
        self,
        _execute_cleanup,
        start_node_remove,
        _agent_status,
    ):
        waiting = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
            force=True,
        )
        unregister_task = Task.objects.get(pk=waiting["task_id"])
        NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="agent.uninstall",
            status=NodeTask.Status.TIMEOUT,
            payload={
                "source_unregister_task_id": unregister_task.id,
                "force_cleanup": True,
            },
            result={},
            last_error="Uninstall timed out waiting for its completion callback.",
            watchdog_deadline_at=timezone.now(),
            correlation_type="node.lifecycle",
            correlation_id=f"remove:{self.agent.id}",
        )

        completed = run_source_unregister_task(
            organization_id=self.org.id,
            task_uuid=str(unregister_task.task_uuid),
        )

        unregister_task.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(completed["result"], "partial_success")
        self.assertEqual(completed["outcome"], "force_cleanup_success")
        self.assertFalse(completed["cleanup_complete"])
        self.assertEqual(
            completed["cleanup_failures"][0]["code"],
            "agent_uninstall_failed",
        )
        self.assertEqual(completed["retained_resources"], ["agent_installation"])
        self.assertEqual(unregister_task.status, Task.Status.SUCCESS)
        self.assertTrue(self.agent.is_deleted)
        start_node_remove.assert_called_once()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
        return_value={"task_id": 123, "operation_id": "remove-1", "state": "removing"},
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_strict_retry_dispatches_new_uninstall_after_previous_failure(
        self,
        _execute_cleanup,
        start_node_remove,
        _agent_status,
    ):
        first = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
        )
        first_unregister = Task.objects.get(pk=first["task_id"])
        NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="agent.uninstall",
            status=NodeTask.Status.FAILED,
            payload={"source_unregister_task_id": first_unregister.id},
            result={
                "cleanup_complete": False,
                "cleanup_failures": [
                    {
                        "code": "agent_uninstall_failed",
                        "detail": "Agent cleanup failed.",
                    }
                ],
                "retained_resources": ["agent_installation"],
            },
            last_error="Agent cleanup failed.",
            watchdog_deadline_at=timezone.now(),
            correlation_type="node.lifecycle",
            correlation_id=f"remove:{self.agent.id}",
        )

        with self.assertRaises(BackupSourceDeleteFailed):
            run_source_unregister_task(
                organization_id=self.org.id,
                task_uuid=str(first_unregister.task_uuid),
            )

        first_unregister.refresh_from_db()
        self.assertEqual(first_unregister.status, Task.Status.FAILED)

        retried_task = retry_task(
            task_uuid=first_unregister.task_uuid,
            organization_id=self.org.id,
        )
        start_task(
            task_uuid=retried_task.task_uuid,
            organization_id=self.org.id,
        )
        retried = run_source_unregister_task(
            organization_id=self.org.id,
            task_uuid=str(retried_task.task_uuid),
        )

        self.assertEqual(retried["result"], "waiting")
        self.assertEqual(retried["task_id"], first_unregister.id)
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_deleted)
        self.assertEqual(start_node_remove.call_count, 2)
        self.assertEqual(
            start_node_remove.call_args.kwargs["triggered_by_task_attempt"],
            1,
        )

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
        return_value={"task_id": 123, "operation_id": "remove-1", "state": "removing"},
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_strict_retry_adopts_authoritative_successful_prior_uninstall(
        self,
        _execute_cleanup,
        start_node_remove,
        _agent_status,
    ):
        first = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
        )
        unregister_task = Task.objects.get(pk=first["task_id"])
        NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="agent.uninstall",
            status=NodeTask.Status.SUCCESS,
            payload={
                "source_unregister_task_id": unregister_task.id,
                "source_unregister_attempt": 0,
            },
            result={
                "completion_received_at": timezone.now().isoformat(),
                "cleanup_complete": True,
                "cleanup_failures": [],
                "retained_resources": [],
            },
            watchdog_deadline_at=timezone.now(),
            correlation_type="node.lifecycle",
            correlation_id=f"remove:{self.agent.id}",
        )
        unregister_task.status = Task.Status.FAILED
        unregister_task.finished_at = timezone.now()
        unregister_task.save(
            update_fields=["status", "finished_at", "updated_at"]
        )

        retried_task = retry_task(
            task_uuid=unregister_task.task_uuid,
            organization_id=self.org.id,
        )
        start_task(
            task_uuid=retried_task.task_uuid,
            organization_id=self.org.id,
        )
        completed = run_source_unregister_task(
            organization_id=self.org.id,
            task_uuid=str(retried_task.task_uuid),
        )

        self.assertEqual(completed["result"], "success")
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.is_deleted)
        self.assertFalse(BackupConfig.objects.filter(pk=self.config.id).exists())
        start_node_remove.assert_called_once()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
        return_value={"task_id": 123, "operation_id": "remove-1", "state": "removing"},
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        side_effect=[
            RepositoryAgentOperationResult(
                waiting=True,
                node_task_id=uuid4(),
                result={},
            ),
            {"physical_cleanup": "deleted"},
        ],
    )
    def test_unregister_waits_for_target_cleanup_then_agent_uninstall(
        self,
        execute_cleanup,
        start_node_remove,
        agent_status,
    ):
        waiting = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
        )

        unregister_task = Task.objects.get(pk=waiting["task_id"])
        cleanup_task = RepositoryTask.objects.get(triggered_by_task=unregister_task)
        unregister_task.refresh_from_db()
        cleanup_task.task.refresh_from_db()
        self.assertEqual(waiting["result"], "waiting")
        self.assertEqual(unregister_task.status, Task.Status.RUNNING)
        self.assertEqual(cleanup_task.task.status, Task.Status.RUNNING)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())
        self.assertFalse(self.agent.is_deleted)

        with (
            mock.patch(
                "apps.source.tasks.source_unregister.queue_source_unregister_task"
            ) as queue_parent,
            self.captureOnCommitCallbacks(execute=True),
        ):
            resumed = run_source_unregister_task(
                organization_id=self.org.id,
                task_uuid=str(unregister_task.task_uuid),
            )

        unregister_task.refresh_from_db()
        cleanup_task.task.refresh_from_db()
        self.assertEqual(resumed["result"], "waiting")
        self.assertEqual(unregister_task.status, Task.Status.RUNNING)
        self.assertEqual(cleanup_task.task.status, Task.Status.SUCCESS)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())
        queue_parent.assert_called_once_with(
            task_id=unregister_task.id,
            countdown_seconds=1,
        )
        execute_cleanup.assert_has_calls(
            [
                mock.call(cleanup_task, allow_dispatch=True),
                mock.call(cleanup_task, allow_dispatch=False),
            ]
        )
        start_node_remove.assert_called_once()
        agent_status.assert_called()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_force_unregister_finishes_if_agent_goes_offline_before_uninstall_dispatch(
        self,
        _execute_cleanup,
        start_node_remove,
        _agent_status,
    ):
        def defer_offline_node(**_kwargs):
            return {
                "state": "completed",
                "purged": False,
                "control_plane_purge_deferred": True,
                "force": True,
                "cleanup_complete": False,
                "cleanup_failures": [
                    {
                        "code": "agent_offline",
                        "detail": "Agent disconnected before uninstall dispatch.",
                    }
                ],
                "retained_resources": ["agent_installation"],
            }

        start_node_remove.side_effect = defer_offline_node

        result = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
            force=True,
        )

        unregister_task = Task.objects.get(pk=result["task_id"])
        self.assertEqual(result["result"], "partial_success")
        self.assertEqual(result["pending_removals"], [])
        self.assertEqual(result["deleted"], [f"agent:{self.agent.id}"])
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(len(result["cleanup_failures"]), 1)
        self.assertEqual(
            result["cleanup_failures"][0]["source_id"],
            f"agent:{self.agent.id}",
        )
        self.assertEqual(result["retained_resources"], ["agent_installation"])
        self.assertEqual(unregister_task.status, Task.Status.SUCCESS)
        start_node_remove.assert_called_once()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete._complete_source_unregister_transaction",
        side_effect=RuntimeError("worker exited before parent finalization"),
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.agent_ws_routable",
        return_value=False,
    )
    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_force_offline_race_retains_source_until_parent_finalization(
        self,
        _execute_cleanup,
        _agent_status,
        _agent_routable,
        _complete_parent,
    ):
        self.agent.metadata = {
            "inventory": {
                "capabilities": [
                    "repository_cleanup_v1",
                    "repository_cleanup_ownership_v1",
                    "detached_uninstall_v2",
                ]
            }
        }
        self.agent.save(update_fields=["metadata", "updated_at"])

        with self.assertRaises(RuntimeError):
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
                force=True,
            )

        unregister_task = Task.objects.get(task_type=Task.Type.SOURCE_UNREGISTER)
        unregister_task.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(unregister_task.status, Task.Status.RUNNING)
        self.assertFalse(self.agent.is_deleted)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.node.services.internal.node_lifecycle.start_node_remove",
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_parent_redelivery_waits_for_its_existing_agent_uninstall(
        self,
        _execute_cleanup,
        start_node_remove,
        _agent_status,
    ):
        def create_uninstall(**kwargs):
            parent_id = kwargs["triggered_by_task_id"]
            node_task = NodeTask.objects.create(
                organization=self.org,
                node=self.agent,
                kind="agent.uninstall",
                status=NodeTask.Status.RUNNING,
                payload={"source_unregister_task_id": parent_id},
                result={"mode": "local_detached"},
                watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=10),
                correlation_type="node.lifecycle",
                correlation_id=f"remove:{self.agent.id}",
            )
            return {
                "task_id": str(node_task.id),
                "operation_id": node_task.correlation_id,
                "state": "removing",
            }

        start_node_remove.side_effect = create_uninstall
        first = delete_backup_sources(
            org=self.org,
            ids=[f"agent:{self.agent.id}"],
        )
        unregister_task = Task.objects.get(pk=first["task_id"])

        redelivered = run_source_unregister_task(
            organization_id=self.org.id,
            task_uuid=str(unregister_task.task_uuid),
        )

        unregister_task.refresh_from_db()
        self.assertEqual(redelivered["result"], "waiting")
        self.assertEqual(unregister_task.status, Task.Status.RUNNING)
        self.assertEqual(len(redelivered["pending_removals"]), 1)
        start_node_remove.assert_called_once()

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        side_effect=RuntimeError("agent unreachable"),
    )
    def test_cleanup_failure_fails_triggering_unregister_and_preserves_source_configuration(
        self,
        execute_cleanup,
        agent_status,
    ):
        with self.assertRaises(BackupSourceDeleteFailed):
            delete_backup_sources(
                org=self.org,
                ids=[f"agent:{self.agent.id}"],
            )

        cleanup_task = RepositoryTask.objects.get(
            repository=self.repository,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
        )
        cleanup_task.task.refresh_from_db()
        cleanup_task.triggered_by_task.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(cleanup_task.task.status, Task.Status.FAILED)
        self.assertEqual(cleanup_task.triggered_by_task.status, Task.Status.FAILED)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())
        self.assertFalse(self.agent.is_deleted)
        execute_cleanup.assert_called_once()
        agent_status.assert_called()

        user = get_user_model().objects.create_user(
            username="direct-nas-retry@test.local",
            password="test-pass",
        )
        Membership.objects.create(
            organization=self.org,
            user=user,
            role=Membership.Role.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user)
        with mock.patch(
            "apps.source.tasks.source_unregister."
            "execute_source_unregister_task.delay"
        ) as queue_unregister:
            with self.captureOnCommitCallbacks(execute=True):
                retry_response = client.post(
                    f"/api/v1/tasks/{cleanup_task.triggered_by_task.task_uuid}/retry/",
                    {},
                    format="json",
                    HTTP_X_ORG_KEY=self.org.key,
                )
        self.assertEqual(retry_response.status_code, 200, retry_response.content)
        queue_unregister.assert_called_once_with(
            task_id=cleanup_task.triggered_by_task_id
        )
