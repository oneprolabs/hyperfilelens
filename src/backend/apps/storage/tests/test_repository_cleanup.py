from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.storage.repositories.models import (
    Repository,
    RepositoryTask,
    RepositoryUsageShard,
)
from apps.storage.services.internal.repository_cleanup import (
    _execute_physical_cleanup,
    create_direct_nas_target_cleanup_task,
    create_repository_cleanup_task,
    direct_nas_cleanup_target_ids,
    repository_cleanup_preflight,
    run_repository_cleanup_task,
)
from apps.task.models import Task, TaskResource
from apps.task.services.interface import create_task


class RepositoryCleanupTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="repository-cleanup-org",
            name="Repository Cleanup Org",
        )

    def _s3_repository(self, name: str = "cleanup-s3") -> Repository:
        return Repository.objects.create(
            organization_id=self.org.id,
            name=name,
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="cleanup-bucket",
            config={"prefix": "managed/repository/", "access_key_id": "test-key"},
        )

    def test_legacy_local_disk_preflight_warns_that_physical_data_is_preserved(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="legacy-local-disk",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"proxy_node_dir": "/data/legacy-mixed-directory"},
        )

        preflight = repository_cleanup_preflight(repository=repository)

        warning = next(
            item
            for item in preflight["warnings"]
            if item["code"] == "legacy_local_disk_preserved"
        )
        self.assertIn("/data/legacy-mixed-directory", warning["detail"])

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={
            "physical_cleanup": "preserved_legacy_directory",
            "cleanup_complete": True,
            "retained_resources": ["legacy_local_disk_directory"],
        },
    )
    def test_legacy_local_disk_cleanup_records_preserved_result(self, _execute_cleanup):
        proxy = Node.objects.create(
            organization=self.org,
            name="legacy-cleanup-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="legacy-local-disk-cleanup",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"proxy_node_dir": "/data/legacy-repository"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
        )
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["outcome"], "cleanup_success_data_preserved")
        self.assertEqual(result["retained_resources"], ["legacy_local_disk_directory"])
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(repository.cleanup_result, Repository.CleanupResult.PRESERVED)

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={
            "mount_status": "not_mounted",
            "physical_cleanup": "skipped_unmounted",
            "cleanup_complete": False,
            "local_state_cleanup": "completed",
            "cleanup_failures": [
                {
                    "code": "NAS_NOT_MOUNTED",
                    "detail": "Remote repository cleanup was skipped because the NAS was not mounted.",
                }
            ],
            "retained_resources": ["nas_repository:17"],
        },
    )
    def test_unmounted_nas_cleanup_succeeds_with_retained_resource_warning(self, _execute_cleanup):
        proxy = Node.objects.create(
            organization=self.org,
            name="unmounted-nas-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": ["repository_cleanup_v1"]}},
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="unmounted-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.SMB,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            config={"server_address": "192.0.2.1", "share_path": "/backup"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
        )
        repository_task = create_repository_cleanup_task(repository=repository, dispatch=False)

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        warning_step = repository_task.task.steps.get(step_name="delete_physical_repository")
        warning_event = repository_task.task.events.filter(level="WARN").get()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["outcome"], "cleanup_success_with_retained_resources")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(warning_step.status, warning_step.Status.WARNING)
        self.assertEqual(warning_event.metadata["mount_status"], "not_mounted")
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(repository.cleanup_result, Repository.CleanupResult.FORCE_SKIPPED)

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation"
    )
    def test_nas_cleanup_dispatches_explicit_unmounted_policy(self, dispatch):
        proxy = Node.objects.create(
            organization=self.org,
            name="nas-policy-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": ["repository_cleanup_v1"]}},
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="nas-policy",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "192.0.2.1", "share_path": "/backup"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
        )
        repository_task = create_repository_cleanup_task(repository=repository, dispatch=False)

        _execute_physical_cleanup(repository_task)

        self.assertEqual(
            dispatch.call_args.kwargs["payload"]["unmounted_policy"],
            "retain_and_continue",
        )

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation"
    )
    def test_legacy_local_disk_on_v1_agent_is_preserved_without_dispatch(self, dispatch):
        proxy = Node.objects.create(
            organization=self.org,
            name="legacy-v1-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": ["repository_cleanup_v1"]}},
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="legacy-v1-local-disk",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"proxy_node_dir": "/data/mixed"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
        )
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        result = _execute_physical_cleanup(repository_task)

        self.assertEqual(result["physical_cleanup"], "preserved_legacy_directory")
        dispatch.assert_not_called()

    def test_managed_local_disk_requires_cleanup_v2(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="managed-v1-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": ["repository_cleanup_v1"]}},
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="managed-v1-local-disk",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={
                "proxy_node_base_dir": "/data",
                "proxy_node_dir": "/data/hfl-repo-123",
                "proxy_fs_layout": "managed_subdir_v1",
            },
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
        )
        repository.config["proxy_node_dir"] = f"/data/hfl-repo-{repository.id}"
        repository.save(update_fields=["config", "updated_at"])
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        with self.assertRaisesMessage(ValidationError, "repository_cleanup_v2"):
            _execute_physical_cleanup(repository_task)

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_repository_cleanup_tombstones_and_duplicate_delivery_is_idempotent(
        self,
        execute_cleanup,
    ):
        repository = self._s3_repository()
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        self.assertEqual(
            repository_task.task.display_name,
            "Delete Repository · cleanup-s3",
        )
        cleanup_plan = repository_task.task.request_payload["cleanup_plan"]
        self.assertEqual(cleanup_plan["repository"]["id"], repository.id)
        self.assertEqual(cleanup_plan["repository"]["prefix"], "managed/repository/")
        self.assertNotIn("access_key_id", cleanup_plan["repository"])

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)
        duplicate_result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(duplicate_result["physical_cleanup"], "deleted")
        self.assertEqual(repository_task.operation_type, RepositoryTask.OperationType.CLEANUP_REPOSITORY)
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(repository.cleanup_result, Repository.CleanupResult.DELETED)
        self.assertIsNotNone(repository.removed_at)
        self.assertEqual(repository_task.task.status, Task.Status.SUCCESS)
        self.assertTrue(
            TaskResource.objects.filter(
                task=repository_task.task,
                resource_type=TaskResource.Type.REPOSITORY,
                resource_id=repository.id,
            ).exists()
        )
        execute_cleanup.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_bucket_if_empty",
        return_value={"bucket": "cleanup-bucket", "status": "failed", "reason": "denied"},
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_prefix",
        return_value={"bucket": "cleanup-bucket", "prefix": "managed/repository/"},
    )
    def test_owned_bucket_cleanup_outcome_is_recorded_without_failing_task(
        self,
        _delete_prefix,
        delete_bucket,
    ):
        repository = self._s3_repository("owned-s3")
        repository.s3_bucket_mode = Repository.S3BucketMode.NEW
        repository.save(update_fields=["s3_bucket_mode"])
        repository_task = create_repository_cleanup_task(repository=repository, dispatch=False)

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            repository_task.task.result_payload["bucket_cleanup"]["status"],
            "failed",
        )
        delete_bucket.assert_called_once()

    @mock.patch("apps.storage.services.internal.repository_cleanup.delete_s3_bucket_if_empty")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_prefix",
        return_value={"bucket": "cleanup-bucket", "prefix": "managed/repository/"},
    )
    def test_existing_bucket_is_never_deleted(self, _delete_prefix, delete_bucket):
        repository = self._s3_repository("existing-s3")
        repository_task = create_repository_cleanup_task(repository=repository, dispatch=False)

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        self.assertEqual(
            result["bucket_cleanup"]["status"],
            "skipped_existing_bucket",
        )
        delete_bucket.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._tombstone_repository",
        side_effect=RuntimeError("metadata finalize failed"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_repository_cleanup_does_not_succeed_before_metadata_finalize(
        self,
        execute_cleanup,
        tombstone_repository,
    ):
        repository = self._s3_repository("metadata-finalize-s3")
        repository_task = create_repository_cleanup_task(repository=repository, dispatch=False)

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(repository.status, Repository.Status.REMOVE_FAILED)
        execute_cleanup.assert_called_once()
        tombstone_repository.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        side_effect=RuntimeError("owner offline"),
    )
    def test_force_cleanup_attempts_physical_delete_and_records_residue(
        self,
        execute_cleanup,
    ):
        repository = self._s3_repository("force-s3")
        forced_task = create_repository_cleanup_task(
            repository=repository,
            force=True,
            dispatch=False,
        )
        run_repository_cleanup_task(repository_task_id=forced_task.id)

        forced_task.task.refresh_from_db()
        repository.refresh_from_db()
        self.assertTrue(forced_task.force)
        self.assertEqual(forced_task.task.status, Task.Status.SUCCESS)
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(repository.cleanup_result, Repository.CleanupResult.FORCE_SKIPPED)
        self.assertFalse(forced_task.task.result_payload["cleanup_complete"])
        self.assertEqual(
            forced_task.task.result_payload["outcome"],
            "force_cleanup_success",
        )
        self.assertTrue(forced_task.task.result_payload["cleanup_failures"])
        execute_cleanup.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={
            "physical_cleanup": "partially_deleted",
            "cleanup_complete": False,
            "cleanup_failures": [
                {
                    "code": "repository_cleanup_incomplete",
                    "detail": "A repository shard was retained.",
                }
            ],
            "retained_resources": ["repository_shard:1"],
        },
    )
    def test_strict_cleanup_fails_when_physical_result_is_incomplete(
        self,
        execute_cleanup,
    ):
        repository = self._s3_repository("strict-incomplete-s3")
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(
            repository_task.task.error_code,
            "REPOSITORY_CLEANUP_INVALID",
        )
        self.assertEqual(
            repository_task.task.error_message,
            "A repository shard was retained.",
        )
        self.assertEqual(repository.status, Repository.Status.REMOVE_FAILED)
        self.assertIsNone(repository.removed_at)
        execute_cleanup.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        side_effect=RuntimeError("owner offline"),
    )
    def test_remove_failed_repository_delete_creates_an_independent_task(self, execute_cleanup):
        repository = self._s3_repository("delete-again-s3")
        failed_task = create_repository_cleanup_task(repository=repository, dispatch=False)
        run_repository_cleanup_task(repository_task_id=failed_task.id)
        repository.refresh_from_db()

        next_task = create_repository_cleanup_task(repository=repository, dispatch=False)

        self.assertNotEqual(next_task.id, failed_task.id)
        self.assertFalse(next_task.force)
        self.assertEqual(next_task.task.trigger_type, Task.TriggerType.MANUAL)
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.REMOVING)
        execute_cleanup.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup._execute_physical_cleanup",
        return_value={"physical_cleanup": "deleted"},
    )
    def test_direct_nas_target_tasks_are_independent_from_logical_cleanup(self, execute_cleanup):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={"server_address": "10.0.0.1", "share_path": "/backups"},
        )
        nodes = []
        config_ids = []
        shards = []
        for index in range(2):
            node = Node.objects.create(
                organization=self.org,
                name=f"agent-{index}",
                role=Node.Role.AGENT,
                status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
                metadata={"inventory": {"capabilities": ["repository_cleanup_v1"]}},
            )
            nodes.append(node)
            config_id = index + 100
            config_ids.append(config_id)
            shards.append(
                RepositoryUsageShard.objects.create(
                    organization_id=self.org.id,
                    repository_id=repository.id,
                    node_id=node.id,
                    repository_subdir=f"hp-repos/agent-{node.id}",
                    source_config_count=1,
                    source_config_ids=[config_id],
                    status=RepositoryUsageShard.Status.SUCCESS,
                )
            )
        source_unregister = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Unregister Direct NAS source",
            resources=[],
            steps=["cleanup_direct_nas_repositories"],
        )

        preflight = repository_cleanup_preflight(repository=repository)
        self.assertTrue(preflight["allowed"])
        self.assertTrue(
            any(
                item["code"] == "physical_targets_to_cleanup"
                for item in preflight["warnings"]
            )
        )

        physical_tasks = []
        for index, (node, config_id) in enumerate(zip(nodes, config_ids, strict=True)):
            target_ids = direct_nas_cleanup_target_ids(
                repository=repository,
                backup_config_ids=[config_id],
                owner_node_id=node.id,
            )
            self.assertEqual(len(target_ids), 1)
            physical_task = create_direct_nas_target_cleanup_task(
                repository=repository,
                target_id=target_ids[0],
                triggered_by_task=source_unregister,
            )
            self.assertEqual(
                physical_task.task.display_name,
                f"Delete Subrepository · {node.name}",
            )
            if index == 0:
                physical_task.task.status = Task.Status.FAILED
                physical_task.task.save(update_fields=["status", "updated_at"])
                failed_physical_task = physical_task
                same_attempt_task = create_direct_nas_target_cleanup_task(
                    repository=repository,
                    target_id=target_ids[0],
                    triggered_by_task=source_unregister,
                )
                self.assertEqual(same_attempt_task.id, failed_physical_task.id)
                source_unregister.retry_count += 1
                source_unregister.save(update_fields=["retry_count", "updated_at"])
                physical_task = create_direct_nas_target_cleanup_task(
                    repository=repository,
                    target_id=target_ids[0],
                    triggered_by_task=source_unregister,
                )
                self.assertNotEqual(physical_task.id, failed_physical_task.id)
                self.assertEqual(
                    physical_task.task.request_payload["source_unregister_attempt"],
                    1,
                )
            run_repository_cleanup_task(repository_task_id=physical_task.id)
            physical_tasks.append(physical_task)

        repository.refresh_from_db()
        for shard in shards:
            shard.refresh_from_db()
            self.assertFalse(shard.is_active)
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(
            {task.operation_type for task in physical_tasks},
            {RepositoryTask.OperationType.CLEANUP_TARGET},
        )
        self.assertEqual(
            {task.triggered_by_task_id for task in physical_tasks},
            {source_unregister.id},
        )

        logical_task = create_repository_cleanup_task(repository=repository, dispatch=False)
        self.assertEqual(logical_task.operation_type, RepositoryTask.OperationType.CLEANUP_REPOSITORY)
        self.assertEqual(logical_task.task.display_name, "Delete Repository · direct-nas")
        self.assertIsNone(logical_task.execution_target_id)
        self.assertIsNone(logical_task.triggered_by_task_id)
        run_repository_cleanup_task(repository_task_id=logical_task.id)
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(execute_cleanup.call_count, 3)

    def test_direct_nas_parent_cleans_historical_targets_before_tombstone(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="direct-nas-parent",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "10.0.0.9", "share_path": "/parent"},
        )
        node = Node.objects.create(
            organization=self.org,
            name="historical-owner",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": ["repository_cleanup_v1"]}},
        )
        shard = RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repository.id,
            node_id=node.id,
            repository_subdir=f"hp-repos/agent-{node.id}",
            status=RepositoryUsageShard.Status.SUCCESS,
        )
        parent = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        with mock.patch(
            "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation",
            return_value={"physical_cleanup": "deleted"},
        ):
            result = run_repository_cleanup_task(repository_task_id=parent.id)

        repository.refresh_from_db()
        shard.refresh_from_db()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertFalse(shard.is_active)
        self.assertTrue(
            RepositoryTask.objects.filter(
                repository=repository,
                operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
                triggered_by_task=parent.task,
            ).exists()
        )

    def test_force_direct_nas_parent_aggregates_child_residue(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="force-direct-nas-parent",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "10.0.0.10", "share_path": "/force-parent"},
        )
        node = Node.objects.create(
            organization=self.org,
            name="force-historical-owner",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": ["repository_cleanup_v1"]}},
        )
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repository.id,
            node_id=node.id,
            repository_subdir=f"hp-repos/agent-{node.id}",
            status=RepositoryUsageShard.Status.SUCCESS,
        )
        parent = create_repository_cleanup_task(
            repository=repository,
            force=True,
            dispatch=False,
        )

        with mock.patch(
            "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation",
            side_effect=RuntimeError("target owner unreachable"),
        ):
            result = run_repository_cleanup_task(repository_task_id=parent.id)

        repository.refresh_from_db()
        parent.task.refresh_from_db()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(parent.task.status, Task.Status.SUCCESS)
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(
            repository.cleanup_result,
            Repository.CleanupResult.FORCE_SKIPPED,
        )
        self.assertFalse(result["cleanup_complete"])
        self.assertTrue(result["cleanup_failures"])
        self.assertTrue(result["retained_resources"])
        child = RepositoryTask.objects.get(
            repository=repository,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
            triggered_by_task=parent.task,
        )
        child.task.refresh_from_db()
        self.assertEqual(child.task.status, Task.Status.SUCCESS)
        self.assertFalse(child.task.result_payload["cleanup_complete"])

    def test_preflight_reports_active_repository_task(self):
        repository = self._s3_repository("blocked-s3")
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Active backup",
            request_payload={"repository_id": repository.id},
            resources=[
                {
                    "resource_type": TaskResource.Type.REPOSITORY,
                    "resource_id": repository.id,
                    "is_primary": True,
                }
            ],
        )

        preflight = repository_cleanup_preflight(repository=repository)

        self.assertFalse(preflight["allowed"])
        blocker = next(item for item in preflight["blockers"] if item["code"] == "active_task")
        self.assertEqual(blocker["task_uuid"], str(task.task_uuid))

    def test_historical_restore_record_is_a_warning_not_a_blocker(self):
        repository = self._s3_repository("restore-bound-s3")
        restore_task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Historical restore",
        )
        restore_task.status = Task.Status.SUCCESS
        restore_task.save(update_fields=["status", "updated_at"])
        record = RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=self.org.id,
            target_execution_node_id=102,
            restore_uid="restore-bound-record",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=restore_task.id,
            task_uuid=restore_task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=101,
            source_snapshot_id=201,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=102,
            target_path="/restore",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )
        RestoreRecordItem.objects.create(
            organization_id=self.org.id,
            restore_record=record,
            source_snapshot_directory_id=301,
            backup_config_dir_id=401,
            repository_id=repository.id,
            kopia_snapshot_id="kopia-restore-bound",
            source_path="/source",
            target_path="/restore/source",
            conflict_mode=RestoreRecordItem.ConflictMode.OVERWRITE,
            status=RestoreRecordItem.Status.SUCCESS,
        )

        preflight = repository_cleanup_preflight(
            repository=repository,
            force=True,
        )

        self.assertTrue(preflight["allowed"])
        self.assertEqual(preflight["restore_record_count"], 1)
        self.assertTrue(
            any(
                warning["code"] == "associated_restore_records"
                for warning in preflight["warnings"]
            )
        )
        self.assertFalse(
            any(
                blocker["code"] == "associated_restore_records"
                for blocker in preflight["blockers"]
            )
        )


class RepositoryCleanupApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(
            key="repository-cleanup-api-org",
            name="Repository Cleanup API Org",
        )
        self.user = get_user_model().objects.create_user(
            username="repository-cleanup-api@test.local",
            password="test-pass",
        )
        Membership.objects.create(
            organization=self.org,
            user=self.user,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(self.user)
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="cleanup-api-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="cleanup-api-bucket",
            config={"prefix": "cleanup/api/"},
        )

    def test_force_cleanup_is_selected_on_delete_and_requires_exact_confirmation(self):
        wrong = self.client.delete(
            f"/api/v1/storage/repositories/{self.repository.id}/",
            {
                "force": True,
                "confirmation": "force cleanup",
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )
        accepted = self.client.delete(
            f"/api/v1/storage/repositories/{self.repository.id}/",
            {
                "force": True,
                "confirmation": "FORCE CLEANUP",
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(wrong.status_code, 400, wrong.content)
        self.assertEqual(accepted.status_code, 202, accepted.content)
        self.assertEqual(accepted.data["operation_type"], "cleanup.repository")
        self.assertTrue(accepted.data["repository_cleanup"]["force"])

    def test_retry_and_force_action_endpoints_are_removed(self):
        for action in ("retry", "force"):
            response = self.client.post(
                f"/api/v1/storage/repositories/{self.repository.id}/cleanup/{action}/",
                {},
                format="json",
                HTTP_X_ORG_KEY=self.org.key,
            )
            self.assertEqual(response.status_code, 404, response.content)

    def test_preflight_plans_active_direct_nas_target_cleanup(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="force-direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={"server_address": "10.0.0.10", "share_path": "/force"},
        )
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repository.id,
            node_id=99,
            repository_subdir="hp-repos/agent-99",
            status=RepositoryUsageShard.Status.SUCCESS,
        )

        response = self.client.post(
            f"/api/v1/storage/repositories/{repository.id}/cleanup/preflight/",
            {"force": True},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.data["allowed"])
        self.assertTrue(response.data["force"])
        self.assertEqual(
            response.data["warnings"][0]["code"],
            "physical_targets_to_cleanup",
        )

    def test_cleanup_request_endpoint_is_removed(self):
        response = self.client.get(
            f"/api/v1/storage/repositories/{self.repository.id}/cleanup-requests/unused/",
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_delete_unassociated_direct_nas_creates_logical_cleanup_task(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="unused-direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={"server_address": "10.0.0.9", "share_path": "/unused"},
        )

        response = self.client.delete(
            f"/api/v1/storage/repositories/{repository.id}/",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 202, response.content)
        repository_task = RepositoryTask.objects.get(
            repository=repository,
            operation_type=RepositoryTask.OperationType.CLEANUP_REPOSITORY,
        )
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.REMOVING)
        self.assertIsNone(repository_task.execution_target_id)
        self.assertEqual(response.data["task_uuid"], str(repository_task.task.task_uuid))
