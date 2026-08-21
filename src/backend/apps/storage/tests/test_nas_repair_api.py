"""Tests for the NAS storage repository "repair" endpoint.

The repair flow has three top-level scenarios:

* The repository is not bound to a Proxy. The endpoint may either save
  config-only changes, or bind a new Proxy (which triggers Kopia init).
* The repository is already bound to a Proxy. The endpoint may save
  config-only changes, or replace the Proxy with a different online one.
  Replacing the Proxy must not re-initialize the Kopia repository; it must
  mount the share on the new Proxy and unmount the old one.
* The repository is currently being used by a running or pending backup
  task. The endpoint must refuse to swap the Proxy in this state.
"""

from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.protection.models import BackupConfig
from apps.storage.repositories.models import (
    Credential,
    Repository,
    RepositoryLocationClaim,
    RepositoryTask,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
    RepositoryAlreadyExistsError,
)
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryAgentOperationResult,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_direct_nas_location,
    reserve_repository_location,
)
from apps.task.models import Task


class NasRepairApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="nas-repair@test.local",
            email="nas-repair@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="nas-repair-org",
            name="NAS Repair Org",
        )
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)
        self.proxy_a = Node.objects.create(
            organization=self.org,
            name="proxy-a",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.31",
        )
        self.proxy_b = Node.objects.create(
            organization=self.org,
            name="proxy-b",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.32",
        )
        self.proxy_offline = Node.objects.create(
            organization=self.org,
            name="proxy-c",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
            ip_address="10.0.0.33",
        )

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def _patch_repair(self, repo_id, payload):
        with mock.patch("apps.storage.tasks.execute_repository_operation.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                return self.client.patch(
                    f"/api/v1/storage/repositories/{repo_id}/repair/",
                    payload,
                    format="json",
                    **self._headers(),
                )

    def _run_create_task(
        self,
        repository,
        *,
        operation_type=RepositoryTask.OperationType.REPAIR_BIND,
    ):
        from apps.storage.services.internal.repository_create import (
            run_repository_create_task,
        )

        repository_task = RepositoryTask.objects.get(
            repository=repository,
            operation_type=operation_type,
        )
        return run_repository_create_task(repository_task_id=repository_task.id)

    def _make_unbound_nas(self, *, protocol=Repository.NasProtocol.SMB):
        return Repository.objects.create(
            organization_id=self.org.id,
            name="unbound-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=protocol,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/backup",
                "mount_options": "ro,soft",
                "quota_gb": 100,
                "smb_username": "u",
                "smb_password": "p",
            },
        )

    def _make_bound_nas(self):
        return Repository.objects.create(
            organization_id=self.org.id,
            name="bound-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.SMB,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy_a.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/backup",
                "mount_options": "ro,soft",
                "quota_gb": 100,
                "smb_username": "u",
                "smb_password": "p",
            },
        )

    def _failed_proxy_provision_residual(self, repository, *, owner=None):
        owner = owner or self.proxy_b
        owner.metadata = {
            "inventory": {
                "capabilities": [
                    "repository_cleanup_v1",
                    "repository_cleanup_ownership_v1",
                ]
            }
        }
        owner.save(update_fields=["metadata", "updated_at"])
        repository_subdir = f"hp-repos/agent-{owner.id}"
        claim = reserve_direct_nas_location(
            repository=repository,
            node_id=owner.id,
            repository_subdir=repository_subdir,
        )
        claim.state = RepositoryLocationClaim.State.RESIDUAL
        claim.save(update_fields=["state", "updated_at"])
        parent = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_PROVISION,
            display_name="Failed Direct NAS provisioning",
            status=Task.Status.FAILED,
            request_payload={"repository_id": repository.id},
        )
        NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=owner,
            parent_task=parent,
            kind="repo.initialize",
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
            payload={
                "repository_id": repository.id,
                "repository": {
                    "id": repository.id,
                    "subdir": repository_subdir,
                },
            },
            result={"ownership_verified": True},
        )
        return claim

    # --- save-only scenarios ---------------------------------------------

    @mock.patch(
        "apps.storage.services.internal.nas_repair.enqueue_repository_usage_refresh"
    )
    def test_repair_unbound_save_only(self, _sync):
        repo = self._make_unbound_nas()
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {
                "name": "renamed",
                "config": {
                    "quota_gb": 200,
                    "quota_unit": "PB",
                    "mount_options": "rw,soft",
                },
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        repo.refresh_from_db()
        self.assertEqual(repo.name, "renamed")
        self.assertEqual(repo.config["quota_gb"], 200)
        self.assertEqual(repo.config["quota_unit"], "PB")
        self.assertEqual(repo.config["mount_options"], "rw,soft")
        # Mount options should be replaced, not appended.
        self.assertNotIn("ro,soft", repo.config["mount_options"])
        # No binding happened.
        self.assertFalse(repo.bind_node_id)
        self.assertNotEqual(repo.bind_node_type, Repository.BindNodeType.PROXY)

    def test_repair_rejects_invalid_quota_unit(self):
        repo = self._make_unbound_nas()

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"config": {"quota_unit": "MB"}},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("quota_unit", str(response.data))
        repo.refresh_from_db()
        self.assertNotIn("quota_unit", repo.config)

    @mock.patch(
        "apps.storage.services.internal.nas_repair.enqueue_repository_usage_refresh"
    )
    def test_repair_unbound_password_blank_keeps_existing(self, _sync):
        repo = self._make_unbound_nas()
        original_password = repo.config["smb_password"]
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"config": {"smb_password": ""}},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        repo.refresh_from_db()
        self.assertEqual(repo.config["smb_password"], original_password)

    @mock.patch(
        "apps.storage.services.internal.nas_repair.enqueue_repository_usage_refresh"
    )
    def test_repair_unbound_password_nonblank_updates(self, _sync):
        repo = self._make_unbound_nas()
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"config": {"smb_password": "new-secret"}},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        repo.refresh_from_db()
        self.assertNotIn("smb_password", repo.config)
        credential = Credential.objects.get(id=repo.credential_id)
        self.assertEqual(credential.get_secret_payload()["smb_password"], "new-secret")

    def test_repair_rejects_smb_fields_on_nfs(self):
        repo = self._make_unbound_nas(protocol=Repository.NasProtocol.NFS)
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"config": {"smb_username": "x"}},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_repair_rejects_repository_location_changes(self):
        repo = self._make_unbound_nas()

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {
                "config": {
                    "server_address": "10.0.0.99",
                    "share_path": "/other-share",
                }
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_repair_rejects_repository_during_removal(self):
        repo = self._make_bound_nas()
        repo.status = Repository.Status.REMOVING
        repo.save(update_fields=["status", "updated_at"])

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"name": "must-not-change"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("during or after removal", str(response.data))
        repo.refresh_from_db()
        self.assertEqual(repo.name, "bound-nas")
        repo.refresh_from_db()
        self.assertEqual(repo.config["server_address"], "10.0.0.10")
        self.assertEqual(repo.config["share_path"], "/backup")

    # --- bind (currently unbound) ----------------------------------------

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    def test_repair_unbound_binds_proxy_and_inits_kopia(self, init_proxy):
        repo = self._make_unbound_nas()
        response = self._patch_repair(repo.id, {"bind_node_id": self.proxy_a.id})
        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["status"], Repository.Status.CREATING)
        self.assertIsNotNone(response.data.get("active_create_task"))
        repo.refresh_from_db()
        self.assertEqual(repo.bind_node_id, self.proxy_a.id)
        self.assertEqual(repo.bind_node_type, Repository.BindNodeType.PROXY)
        result = self._run_create_task(
            repo,
            operation_type=RepositoryTask.OperationType.REPAIR_BIND,
        )
        self.assertEqual(result["status"], "success")
        repo.refresh_from_db()
        self.assertEqual(repo.status, Repository.Status.CREATED)
        self.assertEqual(repo.health, Repository.Health.ONLINE)
        self.assertTrue(
            repo.location_claims.filter(
                state=RepositoryLocationClaim.State.OWNED,
                root_path=f"hp-repos/storage-{repo.id}",
            ).exists()
        )
        init_proxy.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_create.check_proxy_nas_repository",
        side_effect=RuntimeError("wrong repository password"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    def test_repair_unbound_rejects_existing_repository_and_restores_binding(
        self, initialize, check_repository
    ):
        repository = self._make_unbound_nas()
        initialize.side_effect = RepositoryAlreadyExistsError(
            "repository already exists"
        )

        response = self._patch_repair(repository.id, {"bind_node_id": self.proxy_a.id})

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        result = self._run_create_task(
            repository,
            operation_type=RepositoryTask.OperationType.REPAIR_BIND,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        repository.refresh_from_db()
        self.assertIsNone(repository.bind_node_type)
        self.assertIsNone(repository.bind_node_id)
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.UNVERIFIED)
        self.assertNotIn("proxy_mount_path", repository.config)
        self.assertTrue(
            repository.location_claims.filter(
                state=RepositoryLocationClaim.State.RESIDUAL,
                root_path=f"hp-repos/storage-{repository.id}",
            ).exists()
        )
        check_repository.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.nas_repair.enqueue_repository_usage_refresh"
    )
    def test_repair_unbound_bind_offline_proxy_rejected(self, _sync):
        repo = self._make_unbound_nas()
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"bind_node_id": self.proxy_offline.id},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_repair_validation_failure_does_not_partially_rotate_credentials(self):
        repo = self._make_unbound_nas()
        credential_count = Credential.objects.count()

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {
                "name": "must-not-be-persisted",
                "config": {"smb_password": "new-secret"},
                "bind_node_id": self.proxy_offline.id,
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        repo.refresh_from_db()
        self.assertEqual(repo.name, "unbound-nas")
        self.assertIsNone(repo.credential_id)
        self.assertEqual(repo.config["smb_password"], "p")
        self.assertEqual(Credential.objects.count(), credential_count)

    def test_repair_unbound_bind_rejected_after_backup_config_associated(self):
        repo = self._make_unbound_nas()
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="associated-config",
            source_type="agent",
            source_ref_id=123,
            repository_id=repo.id,
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"bind_node_id": self.proxy_a.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        repo.refresh_from_db()
        self.assertFalse(repo.bind_node_id)
        self.assertFalse(
            RepositoryTask.objects.filter(
                repository=repo,
                operation_type=RepositoryTask.OperationType.REPAIR_BIND,
            ).exists()
        )

    def test_repair_unbound_bind_rejects_unreleased_direct_nas_target(self):
        repo = self._make_unbound_nas(protocol=Repository.NasProtocol.NFS)
        agent = Node.objects.create(
            organization=self.org,
            name="historical-direct-nas-agent",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.41",
        )
        repository_subdir = f"hp-repos/agent-{agent.id}"
        reserve_direct_nas_location(
            repository=repo,
            node_id=agent.id,
            repository_subdir=repository_subdir,
        )
        mark_repository_location_owned(repo, owner_node_id=agent.id)

        response = self._patch_repair(
            repo.id,
            {"bind_node_id": self.proxy_a.id},
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        problem = response.data["data"]
        self.assertEqual(problem["code"], "STORAGE.NAS_BIND_BLOCKED")
        blocker = problem["meta"]["binding_blocker"]
        self.assertEqual(blocker["blocker_code"], "DIRECT_NAS_ACTIVE_TARGETS")
        self.assertEqual(blocker["claim_count"], 1)
        self.assertEqual(blocker["owners"][0]["node_role"], NodeRole.AGENT)
        self.assertNotIn("repository_subdir", str(blocker))
        repo.refresh_from_db()
        self.assertIsNone(repo.bind_node_id)
        self.assertEqual(repo.status, Repository.Status.CREATED)
        self.assertFalse(RepositoryTask.objects.filter(repository=repo).exists())

    def test_binding_preflight_reports_proxy_owner_and_upgrade_action(self):
        repo = self._make_unbound_nas(protocol=Repository.NasProtocol.NFS)
        claim = self._failed_proxy_provision_residual(repo)
        self.proxy_b.metadata = {"inventory": {"capabilities": []}}
        self.proxy_b.save(update_fields=["metadata", "updated_at"])

        response = self.client.post(
            f"/api/v1/storage/repositories/{repo.id}/repair/binding-preflight/",
            {"bind_node_id": self.proxy_a.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertFalse(response.data["allowed"])
        self.assertFalse(response.data["recovery_eligible"])
        self.assertEqual(
            response.data["blocker_code"], "DIRECT_NAS_OWNER_UPGRADE_REQUIRED"
        )
        self.assertEqual(response.data["required_action"], "upgrade_owner_node")
        self.assertEqual(response.data["owners"][0]["node_role"], NodeRole.PROXY)
        self.assertIn(self.proxy_b.name, response.data["message"])
        self.assertNotIn(claim.root_path, str(response.data))
        self.assertNotIn("recovery_claim_ids", response.data)

    def test_binding_preflight_reports_associated_source_without_physical_claims(self):
        repo = self._make_unbound_nas()
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="preflight-associated-config",
            source_type="agent",
            source_ref_id=456,
            repository_id=repo.id,
        )

        response = self.client.post(
            f"/api/v1/storage/repositories/{repo.id}/repair/binding-preflight/",
            {"bind_node_id": self.proxy_a.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertFalse(response.data["allowed"])
        self.assertFalse(response.data["recovery_eligible"])
        self.assertEqual(response.data["blocker_code"], "DIRECT_NAS_BIND_DEPENDENCIES")
        self.assertEqual(response.data["required_action"], "remove_dependencies")
        self.assertEqual(response.data["claim_count"], 0)

    def test_binding_preflight_rejects_proxy_from_another_organization(self):
        other_org = Organization.objects.create(
            key="other-nas-repair-org",
            name="Other NAS Repair Org",
        )
        other_proxy = Node.objects.create(
            organization=other_org,
            name="private-other-org-proxy",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.1.31",
        )
        repo = self._make_unbound_nas()

        response = self.client.post(
            f"/api/v1/storage/repositories/{repo.id}/repair/binding-preflight/",
            {"bind_node_id": other_proxy.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn(other_proxy.name, str(response.data))

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation"
    )
    def test_confirmed_failed_provision_cleanup_binds_only_after_verified_delete(
        self,
        resolve_operation,
        initialize_proxy,
    ):
        repo = self._make_unbound_nas(protocol=Repository.NasProtocol.NFS)
        claim = self._failed_proxy_provision_residual(repo)
        resolve_operation.return_value = RepositoryAgentOperationResult(
            waiting=False,
            node_task_id=None,
            result={"ownership_verified": True, "cleanup_complete": True},
        )

        blocked = self._patch_repair(repo.id, {"bind_node_id": self.proxy_a.id})
        self.assertEqual(blocked.status_code, status.HTTP_409_CONFLICT)
        repo.refresh_from_db()
        self.assertIsNone(repo.bind_node_id)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)

        accepted = self._patch_repair(
            repo.id,
            {
                "bind_node_id": self.proxy_a.id,
                "cleanup_failed_provisioning_targets": True,
                "cleanup_confirmation": "CLEAN UP AND BIND",
            },
        )
        self.assertEqual(accepted.status_code, status.HTTP_202_ACCEPTED, accepted.content)
        repo.refresh_from_db()
        self.assertIsNone(repo.bind_node_id)
        self.assertEqual(repo.status, Repository.Status.CREATING)

        result = self._run_create_task(
            repo,
            operation_type=RepositoryTask.OperationType.REPAIR_BIND,
        )

        self.assertEqual(result["status"], "success")
        repo.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RELEASED)
        self.assertEqual(repo.bind_node_id, self.proxy_a.id)
        self.assertEqual(repo.bind_node_type, Repository.BindNodeType.PROXY)
        self.assertEqual(repo.status, Repository.Status.CREATED)
        cleanup_payload = resolve_operation.call_args.kwargs["payload"]
        self.assertFalse(cleanup_payload["ownership_verified"])
        self.assertEqual(
            cleanup_payload["repository"]["subdir"],
            f"hp-repos/agent-{self.proxy_b.id}",
        )
        cleanup_task = RepositoryTask.objects.get(
            repository=repo,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
        )
        self.assertEqual(cleanup_task.requested_by_id, self.user.id)
        initialize_proxy.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation",
        side_effect=RuntimeError("cleanup failed"),
    )
    def test_cleanup_failure_keeps_repository_unbound_and_claim_residual(
        self,
        _resolve_operation,
    ):
        repo = self._make_unbound_nas(protocol=Repository.NasProtocol.NFS)
        claim = self._failed_proxy_provision_residual(repo)
        accepted = self._patch_repair(
            repo.id,
            {
                "bind_node_id": self.proxy_a.id,
                "cleanup_failed_provisioning_targets": True,
                "cleanup_confirmation": "CLEAN UP AND BIND",
            },
        )
        self.assertEqual(accepted.status_code, status.HTTP_202_ACCEPTED)

        result = self._run_create_task(
            repo,
            operation_type=RepositoryTask.OperationType.REPAIR_BIND,
        )

        self.assertEqual(result["status"], "failed")
        repo.refresh_from_db()
        claim.refresh_from_db()
        self.assertIsNone(repo.bind_node_id)
        self.assertEqual(repo.status, Repository.Status.CREATED)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation"
    )
    def test_cleanup_without_fresh_ownership_proof_keeps_residual(
        self,
        resolve_operation,
    ):
        resolve_operation.return_value = RepositoryAgentOperationResult(
            waiting=False,
            node_task_id=None,
            result={"ownership_verified": False, "cleanup_complete": True},
        )
        repo = self._make_unbound_nas(protocol=Repository.NasProtocol.NFS)
        claim = self._failed_proxy_provision_residual(repo)
        accepted = self._patch_repair(
            repo.id,
            {
                "bind_node_id": self.proxy_a.id,
                "cleanup_failed_provisioning_targets": True,
                "cleanup_confirmation": "CLEAN UP AND BIND",
            },
        )
        self.assertEqual(accepted.status_code, status.HTTP_202_ACCEPTED)

        result = self._run_create_task(repo)

        self.assertEqual(result["status"], "failed")
        repo.refresh_from_db()
        claim.refresh_from_db()
        self.assertIsNone(repo.bind_node_id)
        self.assertEqual(repo.status, Repository.Status.CREATED)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)

    @mock.patch(
        "apps.storage.services.internal.repository_cleanup.resolve_or_dispatch_repository_agent_operation"
    )
    def test_dependency_created_during_cleanup_prevents_binding_and_claim_release(
        self,
        resolve_operation,
    ):
        repo = self._make_unbound_nas(protocol=Repository.NasProtocol.NFS)
        claim = self._failed_proxy_provision_residual(repo)

        def complete_cleanup_after_dependency_appears(**_kwargs):
            BackupConfig.objects.create(
                organization_id=self.org.id,
                name="concurrent-associated-config",
                source_type="agent",
                source_ref_id=789,
                repository_id=repo.id,
            )
            return RepositoryAgentOperationResult(
                waiting=False,
                node_task_id=None,
                result={"ownership_verified": True, "cleanup_complete": True},
            )

        resolve_operation.side_effect = complete_cleanup_after_dependency_appears
        accepted = self._patch_repair(
            repo.id,
            {
                "bind_node_id": self.proxy_a.id,
                "cleanup_failed_provisioning_targets": True,
                "cleanup_confirmation": "CLEAN UP AND BIND",
            },
        )
        self.assertEqual(accepted.status_code, status.HTTP_202_ACCEPTED)

        result = self._run_create_task(repo)

        self.assertEqual(result["status"], "failed")
        repo.refresh_from_db()
        claim.refresh_from_db()
        self.assertIsNone(repo.bind_node_id)
        self.assertEqual(repo.status, Repository.Status.CREATED)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        self.assertTrue(
            BackupConfig.objects.filter(
                organization_id=self.org.id,
                repository_id=repo.id,
                name="concurrent-associated-config",
            ).exists()
        )

    # --- swap (currently bound) ------------------------------------------

    @mock.patch(
        "apps.storage.services.internal.nas_repair.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.storage.services.internal.nas_repair.run_agent_task_sync")
    def test_repair_bound_swap_proxy_unmounts_old(
        self,
        run_agent_task_sync,
        _sync,
    ):
        run_agent_task_sync.return_value = mock.Mock(
            task=mock.Mock(status="success", last_error=""),
            result={
                "mount_point": "/mnt/hfl/storage-repositories/repo-34-node-45",
                "ownership_verified": True,
            },
            ok=True,
        )
        repo = self._make_bound_nas()
        old_claim = reserve_repository_location(repo)
        mark_repository_location_owned(repo, owner_node_id=self.proxy_a.id)
        mark_repository_location_ownership_verified(
            repo,
            owner_node_id=self.proxy_a.id,
        )
        response = self._patch_repair(repo.id, {"bind_node_id": self.proxy_b.id})
        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["status"], Repository.Status.CREATING)
        self.assertIsNotNone(response.data.get("active_create_task"))
        new_claim = repo.location_claims.get(owner_node_id=self.proxy_b.id)
        self.assertEqual(new_claim.state, RepositoryLocationClaim.State.RESERVED)
        result = self._run_create_task(
            repo,
            operation_type=RepositoryTask.OperationType.REPAIR_REMOUNT,
        )
        self.assertEqual(result["status"], "success")
        repo.refresh_from_db()
        self.assertEqual(repo.bind_node_id, self.proxy_b.id)
        self.assertEqual(repo.status, Repository.Status.CREATED)
        self.assertEqual(repo.health, Repository.Health.ONLINE)
        old_claim.refresh_from_db()
        new_claim.refresh_from_db()
        self.assertEqual(old_claim.state, RepositoryLocationClaim.State.RELEASED)
        self.assertEqual(new_claim.state, RepositoryLocationClaim.State.OWNED)
        self.assertIsNotNone(new_claim.ownership_verified_at)
        self.assertEqual(
            repo.config["proxy_mount_path"],
            "/mnt/hfl/storage-repositories/repo-34-node-45",
        )

        # Two agent task sync calls: remount (new proxy) and unmount (old).
        kinds = [call.kwargs["kind"] for call in run_agent_task_sync.call_args_list]
        self.assertIn("repo.status", kinds)
        self.assertIn("nas.unmount", kinds)
        # Old proxy should be the node used for the unmount call.
        unmount_call = next(
            c
            for c in run_agent_task_sync.call_args_list
            if c.kwargs["kind"] == "nas.unmount"
        )
        self.assertEqual(unmount_call.kwargs["node_id"], self.proxy_a.id)

    @mock.patch("apps.storage.tasks.execute_repository_operation.apply_async")
    @mock.patch(
        "apps.storage.services.internal.nas_repair.enqueue_repository_usage_refresh"
    )
    def test_repair_bound_swap_rejected_when_busy(
        self,
        _sync,
        apply_async,
    ):
        repo = self._make_bound_nas()
        config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="busy-config",
            source_type="nas",
            source_ref_id=999,
            repository_id=repo.id,
        )
        Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="running backup",
            status=Task.Status.RUNNING,
            request_payload={"backup_config_id": config.id},
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"bind_node_id": self.proxy_b.id},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Original binding unchanged.
        repo.refresh_from_db()
        self.assertEqual(repo.bind_node_id, self.proxy_a.id)
        apply_async.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.nas_repair.enqueue_repository_usage_refresh"
    )
    def test_repair_bound_swap_rejects_same_proxy(self, _sync):
        repo = self._make_bound_nas()
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"bind_node_id": self.proxy_a.id},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch(
        "apps.storage.services.internal.nas_repair.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.storage.services.internal.nas_repair.check_proxy_nas_repository")
    def test_repair_bound_save_only_does_not_touch_binding(
        self,
        check_proxy,
        _sync,
    ):
        repo = self._make_bound_nas()
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {
                "name": "renamed",
                "config": {"quota_gb": 250},
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        repo.refresh_from_db()
        self.assertEqual(repo.name, "renamed")
        self.assertEqual(repo.config["quota_gb"], 250)
        self.assertEqual(repo.bind_node_id, self.proxy_a.id)
        check_proxy.assert_called_once()

    def test_repair_rejects_non_nas_repo(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="s3-repo",
            repo_type=Repository.Type.S3,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="b",
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={},
        )
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/repair/",
            {"name": "x"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
