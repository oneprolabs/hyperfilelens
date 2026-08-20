from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node, NodeTask
from apps.protection.models import BackupSourceSnapshot
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.storage.repositories.models import (
    Repository,
    RepositoryExecutionTarget,
    RepositoryLocationClaim,
    RepositoryTask,
    RepositoryUsageShard,
)
from apps.storage.services.internal.repository_cleanup import (
    _create_replacement_cleanup_task,
    _ensure_cleanup_targets,
    _execute_physical_cleanup,
    create_direct_nas_target_cleanup_task,
    create_repository_cleanup_task,
    direct_nas_cleanup_target_ids,
    repository_cleanup_preflight,
    run_repository_cleanup_task,
)
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryAgentOperationError,
    RepositoryAgentOperationResult,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_initializing,
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    release_repository_location,
    reserve_direct_nas_location,
    reserve_repository_location,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
)
from apps.storage.services.internal.repository_ownership import (
    RepositoryOwnershipMarkerMissingError,
)
from apps.task.models import Task, TaskResource
from apps.task.services.interface import create_task


class RepositoryCleanupTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="repository-cleanup-org",
            name="Repository Cleanup Org",
        )

    def _s3_repository(
        self,
        name: str = "cleanup-s3",
        *,
        prefix: str = "managed/repository/",
    ) -> Repository:
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name=name,
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="cleanup-bucket",
            config={
                "endpoint": "s3.amazonaws.com",
                "prefix": prefix,
                "access_key_id": "test-key",
            },
        )
        reserve_repository_location(repository)
        mark_repository_location_owned(repository)
        mark_repository_location_ownership_verified(repository)
        return repository

    def _mark_owned_location(
        self,
        repository: Repository,
        *,
        node_id: int | None = None,
        repository_subdir: str = "",
    ) -> None:
        if (
            repository.repo_type == Repository.Type.NAS
            and repository.bind_node_id is None
        ):
            reserve_direct_nas_location(
                repository=repository,
                node_id=int(node_id or 0),
                repository_subdir=repository_subdir,
            )
            mark_repository_location_owned(repository, owner_node_id=node_id)
            mark_repository_location_ownership_verified(
                repository,
                owner_node_id=node_id,
                repository_subdir=repository_subdir,
            )
            return
        reserve_repository_location(repository)
        mark_repository_location_owned(repository)
        mark_repository_location_ownership_verified(repository)

    def _mark_s3_location_legacy(
        self,
        repository: Repository,
    ) -> RepositoryLocationClaim:
        claim = repository.location_claims.get(
            scope=RepositoryLocationClaim.Scope.REPOSITORY,
            state=RepositoryLocationClaim.State.OWNED,
        )
        claim.ownership_verified_at = None
        claim.legacy_adoption_required = True
        claim.save(
            update_fields=[
                "ownership_verified_at",
                "legacy_adoption_required",
                "updated_at",
            ]
        )
        return claim

    def test_initialization_in_progress_is_never_treated_as_unused_storage(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="unknown-initialization-result",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATE_FAILED,
            health=Repository.Health.OFFLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="cleanup-bucket",
            config={"prefix": "unknown/result/", "access_key_id": "test-key"},
        )
        reserve_repository_location(repository)
        mark_repository_location_initializing(repository)

        strict = repository_cleanup_preflight(repository=repository, force=False)
        forced = repository_cleanup_preflight(repository=repository, force=True)

        self.assertFalse(strict["allowed"])
        self.assertEqual(
            strict["blockers"][0]["code"],
            "repository_initialization_in_progress",
        )
        self.assertFalse(forced["allowed"])
        self.assertEqual(
            forced["blockers"][0]["code"],
            "repository_initialization_in_progress",
        )

    def test_active_agent_initialization_blocks_force_cleanup(self):
        repository = self._s3_repository("active-agent-initialization")
        node = Node.objects.create(
            organization=self.org,
            name="initializing-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=node,
            kind="repo.initialize",
            payload={"repository": {"id": repository.id}},
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )

        forced = repository_cleanup_preflight(repository=repository, force=True)

        self.assertFalse(forced["allowed"])
        blocker = next(
            item
            for item in forced["blockers"]
            if item["code"] == "active_repository_node_task"
        )
        self.assertEqual(blocker["node_task_id"], str(node_task.id))

    def test_active_agent_operation_with_persisted_repository_id_blocks_cleanup(self):
        repository = self._s3_repository("active-persisted-agent-operation")
        node = Node.objects.create(
            organization=self.org,
            name="persisted-operation-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=node,
            kind="repository.operation",
            # This is the durable form written by run_agent_task_async when a
            # delivery payload is protected for redelivery.
            payload={"repository_id": repository.id},
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )

        forced = repository_cleanup_preflight(repository=repository, force=True)

        self.assertFalse(forced["allowed"])
        blocker = next(
            item
            for item in forced["blockers"]
            if item["code"] == "active_repository_node_task"
        )
        self.assertEqual(blocker["node_task_id"], str(node_task.id))

    def test_reused_direct_nas_shard_reactivates_cleanup_target(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="reused-direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "10.0.0.8", "share_path": "/backup"},
        )
        node = Node.objects.create(
            organization=self.org,
            name="reused-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        subdir = f"hp-repos/agent-{node.id}"
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repository.id,
            node_id=node.id,
            repository_subdir=subdir,
            status=RepositoryUsageShard.Status.SUCCESS,
            is_active=True,
        )
        target = RepositoryExecutionTarget.objects.create(
            organization_id=self.org.id,
            repository=repository,
            target_key=(f"repository:{repository.id}:node:{node.id}:subdir:{subdir}"),
            owner_type=RepositoryExecutionTarget.OwnerType.NODE,
            owner_node_id=node.id,
            owner_identity=f"hfl-cleanup@node-{node.id}",
            repository_subdir=subdir,
            is_active=False,
        )

        targets = _ensure_cleanup_targets(repository)

        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertEqual([item.id for item in targets], [target.id])

    def test_inactive_direct_nas_shard_does_not_create_active_cleanup_target(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="inactive-direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "10.0.0.9", "share_path": "/backup"},
        )
        node = Node.objects.create(
            organization=self.org,
            name="inactive-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repository.id,
            node_id=node.id,
            repository_subdir=f"hp-repos/agent-{node.id}",
            status=RepositoryUsageShard.Status.SUCCESS,
            is_active=False,
        )

        targets = _ensure_cleanup_targets(repository)

        self.assertEqual(len(targets), 1)
        self.assertFalse(targets[0].is_active)

    def test_inactive_direct_nas_owned_location_is_retained_until_confirmed(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="inactive-owned-direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        node = Node.objects.create(
            organization=self.org,
            name="inactive-owned-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
        )
        subdir = f"hp-repos/agent-{node.id}"
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repository.id,
            node_id=node.id,
            repository_subdir=subdir,
            status=RepositoryUsageShard.Status.SUCCESS,
            is_active=False,
        )
        self._mark_owned_location(
            repository,
            node_id=node.id,
            repository_subdir=subdir,
        )

        strict_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        strict_result = run_repository_cleanup_task(repository_task_id=strict_task.id)

        repository.refresh_from_db()
        strict_task.task.refresh_from_db()
        claim = RepositoryLocationClaim.objects.get(repository=repository)
        self.assertEqual(strict_result["status"], "failed")
        self.assertEqual(strict_task.task.status, Task.Status.FAILED)
        self.assertEqual(repository.status, Repository.Status.REMOVE_FAILED)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)

        force_task = create_repository_cleanup_task(
            repository=repository,
            force=True,
            dispatch=False,
        )
        force_result = run_repository_cleanup_task(repository_task_id=force_task.id)

        repository.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(force_result["status"], "success")
        self.assertFalse(force_result["cleanup_complete"])
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(
            repository.cleanup_result,
            Repository.CleanupResult.FORCE_SKIPPED,
        )
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        self.assertIn(
            f"repository_location_claim:{claim.id}",
            force_result["retained_resources"],
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
        return_value=RepositoryAgentOperationResult(
            waiting=False,
            node_task_id=None,
            result={
                "mount_status": "not_mounted",
                "physical_cleanup": "skipped_unmounted",
                "cleanup_complete": False,
                "local_state_cleanup": "completed",
                "cleanup_failures": [
                    {
                        "code": "NAS_NOT_MOUNTED",
                        "detail": (
                            "Remote repository cleanup was skipped because the "
                            "NAS was not mounted."
                        ),
                    }
                ],
                "retained_resources": ["nas_repository:17"],
            },
        ),
    )
    def test_unmounted_nas_cleanup_succeeds_with_retained_resource_warning(
        self, _execute_cleanup
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="unmounted-nas-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_cleanup_v1",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
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
        self._mark_owned_location(repository)
        repository_task = create_repository_cleanup_task(
            repository=repository, dispatch=False
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        warning_step = repository_task.task.steps.get(
            step_name="delete_physical_repository"
        )
        warning_event = repository_task.task.events.filter(level="WARN").get()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["outcome"], "cleanup_success_with_retained_resources")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(warning_step.status, warning_step.Status.WARNING)
        self.assertEqual(warning_event.metadata["mount_status"], "not_mounted")
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(
            repository.cleanup_result, Repository.CleanupResult.FORCE_SKIPPED
        )
        self.assertTrue(
            repository.location_claims.filter(
                state=RepositoryLocationClaim.State.RESIDUAL,
            ).exists()
        )

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup."
        "resolve_or_dispatch_repository_agent_operation"
    )
    def test_force_bound_nas_retains_historical_direct_nas_locations(
        self,
        dispatch,
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="mixed-history-nas-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_cleanup_v1",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="mixed-history-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "192.0.2.2", "share_path": "/backup"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
        )
        self._mark_owned_location(repository)
        bound_claim = repository.location_claims.get(
            scope=RepositoryLocationClaim.Scope.REPOSITORY,
        )
        historical_claim = RepositoryLocationClaim.objects.create(
            organization_id=self.org.id,
            repository=repository,
            namespace=bound_claim.namespace,
            scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
            root_path="hp-repos/agent-99",
            owner_node_id=99,
            state=RepositoryLocationClaim.State.RESIDUAL,
        )
        strict_preflight = repository_cleanup_preflight(repository=repository)
        self.assertFalse(strict_preflight["allowed"])
        self.assertIn(
            "historical_direct_nas_locations",
            {item["code"] for item in strict_preflight["blockers"]},
        )
        repository_task = create_repository_cleanup_task(
            repository=repository,
            force=True,
            dispatch=False,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        dispatch.assert_not_called()
        repository.refresh_from_db()
        bound_claim.refresh_from_db()
        historical_claim.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["cleanup_complete"])
        self.assertIn(
            f"repository_location_claim:{historical_claim.id}",
            result["retained_resources"],
        )
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        self.assertEqual(
            repository.cleanup_result,
            Repository.CleanupResult.FORCE_SKIPPED,
        )
        self.assertEqual(bound_claim.state, RepositoryLocationClaim.State.RESIDUAL)
        self.assertEqual(
            historical_claim.state,
            RepositoryLocationClaim.State.RESIDUAL,
        )

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
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_cleanup_v1",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
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
        repository_task = create_repository_cleanup_task(
            repository=repository, dispatch=False
        )
        self._mark_owned_location(repository)

        _execute_physical_cleanup(repository_task)

        self.assertEqual(
            dispatch.call_args.kwargs["payload"]["unmounted_policy"],
            "retain_and_continue",
        )

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation"
    )
    def test_manual_agent_cleanup_retry_inherits_owner_verification(self, dispatch):
        proxy = Node.objects.create(
            organization=self.org,
            name="partial-cleanup-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_cleanup_v1",
                        "repository_cleanup_v2",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="partial-agent-cleanup",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={
                "proxy_node_base_dir": "/data",
                "proxy_node_dir": "/data/hfl-repo-partial",
                "proxy_fs_layout": "managed_subdir_v1",
            },
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
        )
        self._mark_owned_location(repository)
        original = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        dispatch.side_effect = RepositoryAgentOperationError(
            "partial physical delete",
            result={"ownership_verified": True},
        )

        failed = run_repository_cleanup_task(repository_task_id=original.id)

        original.task.refresh_from_db()
        repository.refresh_from_db()
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(original.task.request_payload["agent_cleanup_owner_verified"])
        self.assertEqual(repository.status, Repository.Status.REMOVE_FAILED)

        retry = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        self.assertTrue(retry.task.request_payload["agent_cleanup_owner_verified"])
        dispatch.reset_mock()
        dispatch.side_effect = None
        dispatch.return_value = RepositoryAgentOperationResult(
            waiting=True,
            node_task_id=None,
            result={},
        )

        _execute_physical_cleanup(retry)

        self.assertIs(
            dispatch.call_args.kwargs["payload"]["ownership_verified"],
            True,
        )

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation"
    )
    def test_legacy_local_disk_on_v1_agent_is_preserved_without_dispatch(
        self, dispatch
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="legacy-v1-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_cleanup_v1",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
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
        self._mark_owned_location(repository)

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
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_cleanup_v1",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
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
        self._mark_owned_location(repository)
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
        duplicate_result = run_repository_cleanup_task(
            repository_task_id=repository_task.id
        )

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(duplicate_result["physical_cleanup"], "deleted")
        self.assertEqual(
            repository_task.operation_type,
            RepositoryTask.OperationType.CLEANUP_REPOSITORY,
        )
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
        "apps.storage.services.internal.repository_cleanup."
        "verify_s3_repository_deletion_ownership"
    )
    @mock.patch("apps.storage.services.internal.repository_cleanup.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_bucket_if_empty",
        return_value={
            "bucket": "cleanup-bucket",
            "status": "failed",
            "reason": "denied",
        },
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_prefix",
        return_value={"bucket": "cleanup-bucket", "prefix": "managed/repository/"},
    )
    def test_owned_bucket_cleanup_outcome_is_recorded_without_failing_task(
        self,
        _delete_prefix,
        delete_bucket,
        _check_repository,
        _verify_owner,
    ):
        repository = self._s3_repository("owned-s3")
        repository.s3_bucket_mode = Repository.S3BucketMode.NEW
        repository.save(update_fields=["s3_bucket_mode"])
        repository_task = create_repository_cleanup_task(
            repository=repository, dispatch=False
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertTrue(
            repository_task.task.request_payload["s3_cleanup_owner_verified"]
        )
        self.assertEqual(
            repository_task.task.result_payload["bucket_cleanup"]["status"],
            "failed",
        )
        _check_repository.assert_not_called()
        delete_bucket.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership"
    )
    @mock.patch("apps.storage.services.internal.repository_cleanup.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_bucket_if_empty"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_prefix",
        return_value={"bucket": "cleanup-bucket", "prefix": "managed/repository/"},
    )
    def test_existing_bucket_is_never_deleted(
        self,
        _delete_prefix,
        delete_bucket,
        _check_repository,
        _verify_owner,
    ):
        repository = self._s3_repository("existing-s3")
        repository_task = create_repository_cleanup_task(
            repository=repository, dispatch=False
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        self.assertEqual(
            result["bucket_cleanup"]["status"],
            "skipped_existing_bucket",
        )
        _check_repository.assert_not_called()
        delete_bucket.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_prefix",
        return_value={"bucket": "cleanup-bucket", "prefix": "managed/repository/"},
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership"
    )
    @mock.patch("apps.storage.services.internal.repository_cleanup.check_s3_repository")
    def test_legacy_s3_cleanup_adopts_only_after_kopia_access_is_proven(
        self,
        check_repository,
        verify_owner,
        delete_prefix,
    ):
        repository = self._s3_repository("legacy-s3")
        claim = self._mark_s3_location_legacy(repository)

        def prove_legacy_repository(candidate: Repository) -> None:
            mark_repository_location_ownership_verified(candidate)

        check_repository.side_effect = prove_legacy_repository
        verify_owner.side_effect = [
            RepositoryOwnershipMarkerMissingError("marker missing"),
            None,
            None,
        ]
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        claim.refresh_from_db()
        self.assertEqual(result["status"], "success", result)
        check_repository.assert_called_once_with(repository)
        self.assertIsNotNone(claim.ownership_verified_at)
        self.assertFalse(claim.legacy_adoption_required)
        self.assertEqual(verify_owner.call_count, 3)
        delete_prefix.assert_called_once()

    @mock.patch("apps.storage.services.internal.repository_cleanup.delete_s3_prefix")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership",
        side_effect=RepositoryOwnershipMarkerMissingError("marker missing"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.check_s3_repository",
        side_effect=RepositoryInitializationError("Kopia ownership proof failed."),
    )
    def test_legacy_s3_cleanup_fails_closed_when_kopia_proof_fails(
        self,
        check_repository,
        verify_owner,
        delete_prefix,
    ):
        repository = self._s3_repository("unproven-legacy-s3")
        claim = self._mark_s3_location_legacy(repository)
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(repository.status, Repository.Status.REMOVE_FAILED)
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(
            repository_task.task.error_code,
            "REPOSITORY_CLEANUP_INVALID",
        )
        self.assertIsNone(claim.ownership_verified_at)
        self.assertTrue(claim.legacy_adoption_required)
        check_repository.assert_called_once_with(repository)
        verify_owner.assert_called_once_with(repository)
        delete_prefix.assert_not_called()

    @mock.patch("apps.storage.services.internal.repository_cleanup.delete_s3_prefix")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership"
    )
    @mock.patch("apps.storage.services.internal.repository_cleanup.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation",
        return_value=RepositoryAgentOperationResult(
            waiting=False,
            node_task_id=None,
            result={
                "ownership_verified": True,
                "physical_cleanup": "deleted",
                "scope": "s3_prefix",
                "cleanup_complete": True,
            },
        ),
    )
    def test_s3_cleanup_prefers_one_capable_agent(
        self,
        dispatch_agent,
        _check_repository,
        verify_owner,
        controller_delete,
    ):
        repository = self._s3_repository("agent-s3")
        agent = Node.objects.create(
            organization=self.org,
            name="s3-cleanup-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": ["repository_cleanup_s3_v1"]}},
        )
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["executor"], "agent")
        self.assertEqual(
            repository_task.task.request_payload["s3_cleanup_agent_node_id"],
            agent.id,
        )
        self.assertEqual(verify_owner.call_count, 1)
        controller_delete.assert_not_called()
        call = dispatch_agent.call_args.kwargs
        self.assertEqual(call["node"].id, agent.id)
        self.assertEqual(call["payload"]["repository"]["prefix"], "managed/repository/")
        self.assertNotIn("secret_access_key", call["persisted_payload"])

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership",
        side_effect=AssertionError("must not re-authorize a completed Agent cleanup"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation"
    )
    def test_s3_cleanup_resumes_after_agent_deleted_marker_before_controller_commit(
        self,
        dispatch_agent,
        verify_owner,
    ):
        repository = self._s3_repository("agent-restart-window")
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        node = Node.objects.create(
            organization=self.org,
            name="completed-s3-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=node,
            correlation_type="repository_cleanup",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.SUCCESS,
            payload={
                "repository_id": repository.id,
                "operation_type": repository_task.operation_type,
            },
            result={
                "ownership_verified": True,
                "cleanup_complete": True,
                "physical_cleanup": "deleted",
                "scope": "s3_prefix",
            },
            watchdog_deadline_at=timezone.now(),
        )
        repository_task.remote_task_id = node_task.id
        repository_task.save(update_fields=["remote_task_id", "updated_at"])
        repository_task.task.status = Task.Status.RUNNING
        repository_task.task.current_step = "delete_physical_repository"
        repository_task.task.progress = 40
        repository_task.task.save(
            update_fields=["status", "current_step", "progress", "updated_at"]
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["executor"], "agent")
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.REMOVED)
        dispatch_agent.assert_not_called()
        verify_owner.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership",
        side_effect=AssertionError(
            "must not inspect a marker while Agent cleanup runs"
        ),
    )
    def test_s3_cleanup_waits_for_running_agent_before_reading_marker(
        self,
        verify_owner,
    ):
        repository = self._s3_repository("agent-running-window")
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        node = Node.objects.create(
            organization=self.org,
            name="running-s3-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=node,
            correlation_type="repository_cleanup",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.RUNNING,
            payload={
                "repository_id": repository.id,
                "operation_type": repository_task.operation_type,
            },
            watchdog_deadline_at=timezone.now(),
        )
        repository_task.remote_task_id = node_task.id
        repository_task.save(update_fields=["remote_task_id", "updated_at"])

        repository_task.task.status = Task.Status.RUNNING
        repository_task.task.current_step = "delete_physical_repository"
        repository_task.task.progress = 40
        repository_task.task.save(
            update_fields=["status", "current_step", "progress", "updated_at"]
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["remote_task_id"], str(node_task.id))
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.REMOVING)
        verify_owner.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership",
        side_effect=AssertionError("malformed Agent proof must fail closed"),
    )
    def test_s3_cleanup_rejects_malformed_success_without_reading_marker(
        self,
        verify_owner,
    ):
        repository = self._s3_repository("agent-malformed-success")
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        node = Node.objects.create(
            organization=self.org,
            name="malformed-s3-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=node,
            correlation_type="repository_cleanup",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.SUCCESS,
            payload={
                "repository_id": repository.id,
                "operation_type": repository_task.operation_type,
            },
            result={"cleanup_complete": True},
            watchdog_deadline_at=timezone.now(),
        )
        repository_task.remote_task_id = node_task.id
        repository_task.save(update_fields=["remote_task_id", "updated_at"])

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertIsNotNone(result["replacement_task_uuid"])
        repository.refresh_from_db()
        # An untrusted terminal attestation cannot prove whether physical
        # deletion happened. Keep the repository in its in-progress state and
        # require recovery instead of authorizing another executor.
        self.assertEqual(repository.status, Repository.Status.REMOVING)
        verify_owner.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership",
        side_effect=ValidationError("marker missing"),
    )
    def test_s3_cleanup_recovery_rejects_result_for_another_repository(
        self,
        verify_owner,
    ):
        repository = self._s3_repository("agent-recovery-repository-mismatch")
        other_repository = self._s3_repository(
            "agent-recovery-other-repository",
            prefix="managed/other-repository/",
        )
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        node = Node.objects.create(
            organization=self.org,
            name="mismatched-s3-agent-result",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=node,
            correlation_type="repository_cleanup",
            correlation_id=str(repository_task.task.task_uuid),
            kind="repository.operation",
            status=NodeTask.Status.SUCCESS,
            payload={
                "repository_id": other_repository.id,
                "operation_type": repository_task.operation_type,
            },
            result={
                "ownership_verified": True,
                "cleanup_complete": True,
                "physical_cleanup": "deleted",
                "scope": "s3_prefix",
            },
            watchdog_deadline_at=timezone.now(),
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        repository_task.refresh_from_db()
        self.assertIsNone(repository_task.remote_task_id)
        verify_owner.assert_not_called()
        self.assertNotEqual(repository_task.remote_task_id, node_task.id)

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_bucket_if_empty"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_prefix",
        return_value={"bucket": "cleanup-bucket", "prefix": "managed/repository/"},
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership"
    )
    @mock.patch("apps.storage.services.internal.repository_cleanup.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation",
        side_effect=RepositoryAgentOperationError(
            "Agent cannot reach object storage.",
            result={"failure_class": "storage", "cleanup_complete": False},
        ),
    )
    def test_s3_agent_storage_failure_reverifies_before_controller_fallback(
        self,
        _dispatch_agent,
        _check_repository,
        verify_owner,
        controller_delete,
        delete_bucket,
    ):
        repository = self._s3_repository("agent-fallback-s3")
        Node.objects.create(
            organization=self.org,
            name="s3-fallback-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"capabilities": ["repository_cleanup_s3_v1"]},
        )
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["executor"], "controller")
        self.assertTrue(
            repository_task.task.request_payload["s3_cleanup_agent_attempted"]
        )
        self.assertEqual(verify_owner.call_count, 2)
        controller_delete.assert_called_once()
        delete_bucket.assert_not_called()

    @mock.patch("apps.storage.services.internal.repository_cleanup.delete_s3_prefix")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership"
    )
    @mock.patch("apps.storage.services.internal.repository_cleanup.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation",
        side_effect=RepositoryAgentOperationError(
            "Repository ownership marker changed.",
            result={"failure_class": "ownership", "cleanup_complete": False},
        ),
    )
    def test_s3_agent_ownership_failure_never_falls_back_to_controller(
        self,
        _dispatch_agent,
        _check_repository,
        _verify_owner,
        controller_delete,
    ):
        repository = self._s3_repository("agent-owner-mismatch-s3")
        Node.objects.create(
            organization=self.org,
            name="s3-owner-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"capabilities": ["repository_cleanup_s3_v1"]},
        )
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        with self.assertRaises(ValidationError):
            _execute_physical_cleanup(repository_task)

        controller_delete.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership"
    )
    @mock.patch("apps.storage.services.internal.repository_cleanup.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_bucket_if_empty"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_prefix",
        return_value={"bucket": "cleanup-bucket", "prefix": "managed/repository/"},
    )
    def test_resumed_s3_delete_is_idempotent_after_owner_verification(
        self,
        delete_prefix,
        delete_bucket,
        check_repository,
        _verify_owner,
    ):
        repository = self._s3_repository("resumed-s3")
        repository_task = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        task = repository_task.task
        task.status = Task.Status.RUNNING
        task.current_step = "delete_physical_repository"
        task.progress = 40
        task.request_payload = {
            **(task.request_payload or {}),
            "s3_cleanup_owner_verified": True,
        }
        task.save(
            update_fields=[
                "status",
                "current_step",
                "progress",
                "request_payload",
                "updated_at",
            ]
        )

        result = run_repository_cleanup_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "success")
        check_repository.assert_not_called()
        delete_prefix.assert_called_once()
        delete_bucket.assert_not_called()

    def test_s3_recovery_replacement_inherits_owner_verification(self):
        repository = self._s3_repository("replacement-s3")
        original = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        original.task.status = Task.Status.FAILED
        original.task.request_payload = {
            **(original.task.request_payload or {}),
            "s3_cleanup_owner_verified": True,
        }
        original.task.save(update_fields=["status", "request_payload", "updated_at"])

        replacement = _create_replacement_cleanup_task(repository_task_id=original.id)

        self.assertIsNotNone(replacement)
        self.assertTrue(replacement.task.request_payload["s3_cleanup_owner_verified"])

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.verify_s3_repository_deletion_ownership"
    )
    @mock.patch("apps.storage.services.internal.repository_cleanup.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_bucket_if_empty"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.delete_s3_prefix",
        return_value={"bucket": "cleanup-bucket", "prefix": "managed/repository/"},
    )
    def test_manual_s3_cleanup_retry_inherits_owner_verification(
        self,
        delete_prefix,
        delete_bucket,
        check_repository,
        _verify_owner,
    ):
        repository = self._s3_repository("manual-retry-s3")
        original = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        original.task.status = Task.Status.FAILED
        original.task.request_payload = {
            **(original.task.request_payload or {}),
            "s3_cleanup_owner_verified": True,
        }
        original.task.save(update_fields=["status", "request_payload", "updated_at"])
        repository.status = Repository.Status.REMOVE_FAILED
        repository.save(update_fields=["status", "updated_at"])

        retry = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        result = run_repository_cleanup_task(repository_task_id=retry.id)

        self.assertEqual(result["status"], "success")
        self.assertTrue(retry.task.request_payload["s3_cleanup_owner_verified"])
        check_repository.assert_not_called()
        delete_prefix.assert_called_once()
        delete_bucket.assert_not_called()

    def test_cleanup_retry_does_not_inherit_proof_after_claim_is_recreated(self):
        repository = self._s3_repository("new-claim-generation-s3")
        original = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )
        original.task.status = Task.Status.FAILED
        original.task.request_payload = {
            **(original.task.request_payload or {}),
            "s3_cleanup_owner_verified": True,
        }
        original.task.save(update_fields=["status", "request_payload", "updated_at"])
        previous_claim_ids = original.task.request_payload["cleanup_plan"][
            "location_claim_ids"
        ]

        release_repository_location(repository)
        replacement_claim = reserve_repository_location(repository)
        mark_repository_location_owned(repository)
        repository.status = Repository.Status.REMOVE_FAILED
        repository.save(update_fields=["status", "updated_at"])

        retry = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        self.assertNotEqual(
            retry.task.request_payload["cleanup_plan"]["location_claim_ids"],
            previous_claim_ids,
        )
        self.assertEqual(
            retry.task.request_payload["cleanup_plan"]["location_claim_ids"],
            [replacement_claim.id],
        )
        self.assertNotIn("s3_cleanup_owner_verified", retry.task.request_payload)

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
        repository_task = create_repository_cleanup_task(
            repository=repository, dispatch=False
        )

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
        self.assertEqual(
            repository.cleanup_result, Repository.CleanupResult.FORCE_SKIPPED
        )
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
    def test_remove_failed_repository_delete_creates_an_independent_task(
        self, execute_cleanup
    ):
        repository = self._s3_repository("delete-again-s3")
        failed_task = create_repository_cleanup_task(
            repository=repository, dispatch=False
        )
        run_repository_cleanup_task(repository_task_id=failed_task.id)
        repository.refresh_from_db()

        next_task = create_repository_cleanup_task(
            repository=repository, dispatch=False
        )

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
    def test_direct_nas_target_tasks_are_independent_from_logical_cleanup(
        self, execute_cleanup
    ):
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
                status=Node.Status.ACTIVE,
                availability=Node.Availability.ONLINE,
                metadata={
                    "inventory": {
                        "capabilities": [
                            "repository_cleanup_v1",
                            "repository_cleanup_ownership_v1",
                        ]
                    }
                },
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

        logical_task = create_repository_cleanup_task(
            repository=repository, dispatch=False
        )
        self.assertEqual(
            logical_task.operation_type, RepositoryTask.OperationType.CLEANUP_REPOSITORY
        )
        self.assertEqual(
            logical_task.task.display_name, "Delete Repository · direct-nas"
        )
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
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_cleanup_v1",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
        )
        shard = RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repository.id,
            node_id=node.id,
            repository_subdir=f"hp-repos/agent-{node.id}",
            status=RepositoryUsageShard.Status.SUCCESS,
        )
        self._mark_owned_location(
            repository,
            node_id=node.id,
            repository_subdir=shard.repository_subdir,
        )
        parent = create_repository_cleanup_task(
            repository=repository,
            dispatch=False,
        )

        with mock.patch(
            "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation",
            return_value=RepositoryAgentOperationResult(
                waiting=False,
                node_task_id=None,
                result={"physical_cleanup": "deleted"},
            ),
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
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={
                "inventory": {
                    "capabilities": [
                        "repository_cleanup_v1",
                        "repository_cleanup_ownership_v1",
                    ]
                }
            },
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
        blocker = next(
            item for item in preflight["blockers"] if item["code"] == "active_task"
        )
        self.assertEqual(blocker["task_uuid"], str(task.task_uuid))

    def test_preflight_blocks_waiting_backup_config_provision(self):
        repository = self._s3_repository("waiting-storage-validation")
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_PROVISION,
            display_name="Waiting storage validation",
            resources=[
                {
                    "resource_type": TaskResource.Type.REPOSITORY,
                    "resource_id": repository.id,
                }
            ],
        )
        task.status = Task.Status.WAITING
        task.save(update_fields=["status", "updated_at"])

        preflight = repository_cleanup_preflight(repository=repository, force=True)

        self.assertFalse(preflight["allowed"])
        self.assertTrue(
            any(
                blocker.get("task_uuid") == str(task.task_uuid)
                for blocker in preflight["blockers"]
            )
        )

    def test_preflight_finds_active_backup_from_snapshot_association(self):
        repository = self._s3_repository("active-backup-s3")
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Active backup without repository resource",
        )
        BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="active-backup-snapshot",
            idempotency_key="active-backup-snapshot",
            source_type="agent",
            source_ref_id=101,
            backup_config_id=201,
            repository_id=repository.id,
            task_id=task.id,
            task_uuid=task.task_uuid,
        )

        preflight = repository_cleanup_preflight(
            repository=repository,
            allow_associations=True,
        )

        self.assertFalse(preflight["allowed"])
        self.assertTrue(
            any(
                blocker.get("task_uuid") == str(task.task_uuid)
                for blocker in preflight["blockers"]
            )
        )

    def test_preflight_finds_active_restore_from_record_item_association(self):
        repository = self._s3_repository("active-restore-s3")
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Active restore without repository resource",
        )
        record = RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=self.org.id,
            target_execution_node_id=102,
            restore_uid="active-restore-record",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=task.id,
            task_uuid=task.task_uuid,
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
            kopia_snapshot_id="active-restore-snapshot",
            source_path="/source",
            target_path="/restore/source",
            conflict_mode=RestoreRecordItem.ConflictMode.OVERWRITE,
        )

        preflight = repository_cleanup_preflight(
            repository=repository,
            allow_associations=True,
        )

        self.assertFalse(preflight["allowed"])
        self.assertTrue(
            any(
                blocker.get("task_uuid") == str(task.task_uuid)
                for blocker in preflight["blockers"]
            )
        )

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
        self.assertEqual(
            response.data["task_uuid"], str(repository_task.task.task_uuid)
        )
