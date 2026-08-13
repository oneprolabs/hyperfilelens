from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.lens_bridge.models import LensGatewayLink, LensKnowledgeSource
from apps.lens_bridge.services import platform_lens
from apps.lens_bridge.services.gateway_execution import create_workspace_binding
from apps.node.models import Node, NodeTask
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.restore.models import RestorePlan, RestoreRecord, RestoreRecordItem
from apps.restore.services import interface as restore_service
from apps.restore.services.reconciliation import reconcile_restore_node_task_projections
from apps.restore.services.task_events import (
    RESTORE_EVENT_SCHEMA_KEY,
    RESTORE_EVENT_SCHEMA_VERSION,
    append_restore_execution_started_event,
    append_restore_item_terminal_event,
)
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository
from apps.task.models import Task, TaskEvent, TaskResource


class RestoreApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="restore-api@test.local",
            email="restore-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="restore-test-org", name="Restore Test Org"
        )
        Membership.objects.create(
            user=self.user, organization=self.org, role=Membership.Role.ADMIN
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="restore-agent-1",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.51",
        )
        self.target = Node.objects.create(
            organization=self.org,
            name="restore-target-1",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.52",
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="restore-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_bucket="restore-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "access_key_id": "ak",
                "secret_access_key": "sk",
                "kopia_password": "123456",
                "use_tls": False,
            },
        )
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Restore config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=self.repository.id,
            compression_level=BackupConfig.CompressionLevel.BALANCED,
            recovery_plan_enabled=True,
        )
        self.config_dir = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data",
            path_type=BackupConfigDirectory.PathType.DIRECTORY,
            sort_order=0,
        )
        self.snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="snap-restore-1",
            idempotency_key="snap-restore-1",
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=1,
            successful_directory_count=1,
        )
        self.snapshot_dir = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=self.config_dir.id,
            source_path="/data",
            path_type=BackupSourceSnapshotDirectory.PathType.DIRECTORY,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-1",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        self.client.force_authenticate(user=self.user)

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def _plan_payload(self):
        return {
            "backup_config_id": self.config.id,
            "backup_config_dir_id": self.config_dir.id,
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "source_path": "/data",
            "target_type": "agent",
            "target_ref_id": self.target.id,
            "restore_dir": "/restore/data",
            "conflict_mode": "overwrite",
        }

    def _manual_restore_payload(self):
        return {
            "source_snapshot_id": self.snapshot.id,
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "target_type": "agent",
            "target_ref_id": self.target.id,
            "target_path": "/restore/manual",
            "scope": "paths",
            "conflict_mode": "skip",
            "items": [
                {
                    "source_snapshot_directory_id": self.snapshot_dir.id,
                    "selected_paths": ["subdir/file.txt"],
                }
            ],
        }

    def _workspace_binding(self, gateway_link: LensGatewayLink):
        knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.org,
            name="Restore test KS",
            gateway=gateway_link.gateway,
            gateway_link=gateway_link,
            backup_source_snapshot_id=self.snapshot.id,
            backup_snapshot_directory_id=self.snapshot_dir.id,
            source_path="/data",
            created_by=self.user,
        )
        binding = create_workspace_binding(
            tenant_organization=self.org,
            knowledge_source=knowledge_source,
        )
        binding.state = binding.State.READY
        binding.identity_status = binding.IdentityStatus.READY
        binding.save(update_fields=["state", "identity_status", "updated_at"])
        return binding

    def _bound_repository_lens_restore(self, *, repository_type: str):
        private_gateway = Node.objects.create(
            organization=self.org,
            name=f"private-{repository_type}-lens-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        repository_proxy = Node.objects.create(
            organization=self.org,
            name=f"chat-{repository_type}-repository-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.65",
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=private_gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        workspace_binding = self._workspace_binding(gateway_link)
        self.repository.repo_type = repository_type
        self.repository.s3_bucket = ""
        self.repository.nas_protocol = (
            Repository.NasProtocol.NFS
            if repository_type == Repository.Type.NAS
            else None
        )
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = repository_proxy.id
        self.repository.config = (
            {
                "server_address": "10.0.0.20",
                "share_path": "/volume1/backup",
                "kopia_password": "repo-pass",
                "proxy_repository_server_host": "10.0.0.65",
            }
            if repository_type == Repository.Type.NAS
            else {
                "proxy_node_dir": "/srv/hfl-repository",
                "kopia_password": "repo-pass",
                "proxy_repository_server_host": "10.0.0.65",
            }
        )
        self.repository.save()
        payload = self._manual_restore_payload()
        payload.update(
            {
                "target_ref_id": private_gateway.id,
                "target_path": workspace_binding.resolved_path(),
                "idempotency_key": f"lens-workspace:{repository_type}:1",
            }
        )
        record = restore_service.create_lens_workspace_restore_record(
            organization_id=self.org.id,
            workspace_binding_id=workspace_binding.id,
            data=payload,
        )
        server_task = NodeTask.objects.get(
            kind="repository.server.start",
            correlation_id=str(record.task_uuid),
        )
        return record, server_task, repository_proxy

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_lens_workspace_restore_uses_platform_execution_identity(self, _ready):
        platform_org = platform_lens.get_or_create_platform_org()
        platform_gateway = Node.objects.create(
            organization=platform_org,
            name="platform-restore-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=platform_org,
            gateway=platform_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )
        workspace_binding = self._workspace_binding(gateway_link)
        self.assertEqual(
            workspace_binding.relative_path,
            f"hfl-ks-{workspace_binding.workspace_uid}",
        )
        self.assertEqual(
            workspace_binding.resolved_path(),
            f"{gateway_link.resolved_workspace_root()}/hfl-ks-{workspace_binding.workspace_uid}",
        )
        payload = self._manual_restore_payload()
        payload.update(
            {
                "target_ref_id": platform_gateway.id,
                "target_path": workspace_binding.resolved_path(),
                "idempotency_key": "lens-workspace:test:1",
            }
        )

        record = restore_service.create_lens_workspace_restore_record(
            organization_id=self.org.id,
            workspace_binding_id=workspace_binding.id,
            data=payload,
        )

        self.assertEqual(record.organization_id, self.org.id)
        self.assertEqual(record.purpose, RestoreRecord.Purpose.LENS_WORKSPACE)
        self.assertEqual(record.target_execution_organization_id, platform_org.id)
        self.assertEqual(record.target_execution_node_id, platform_gateway.id)
        product_task = Task.objects.get(pk=record.task_id)
        self.assertEqual(product_task.organization_id, self.org.id)
        self.assertEqual(
            product_task.task_type,
            Task.Type.INSIGHT_WORKSPACE_RESTORE,
        )
        self.assertEqual(product_task.trigger_type, Task.TriggerType.SYSTEM)
        node_task = NodeTask.objects.get(
            kind="restore.run",
            correlation_id=str(record.task_uuid),
        )
        self.assertEqual(node_task.organization_id, platform_org.id)
        self.assertEqual(node_task.requesting_organization_id, self.org.id)
        retried = restore_service.create_lens_workspace_restore_record(
            organization_id=self.org.id,
            workspace_binding_id=workspace_binding.id,
            data=payload,
        )
        self.assertEqual(retried.id, record.id)
        self.assertEqual(
            RestoreRecord.objects.filter(
                purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
                idempotency_key="lens-workspace:test:1",
            ).count(),
            1,
        )
        # Simulate an in-flight restore created before the task type migration.
        product_task.task_type = Task.Type.RESTORE
        product_task.save(update_fields=["task_type", "updated_at"])
        node_task.status = NodeTask.Status.SUCCESS
        node_task.result = {"restored": True}
        node_task.save(update_fields=["status", "result", "updated_at"])
        item = record.items.get()
        item.refresh_from_db()
        self.assertEqual(item.status, RestoreRecordItem.Status.SUCCESS)
        product_task.refresh_from_db()
        self.assertEqual(product_task.status, Task.Status.SUCCESS)
        self.assertEqual(
            product_task.task_type,
            Task.Type.INSIGHT_WORKSPACE_RESTORE,
        )

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_legacy_active_lens_restore_is_classified_after_failure(self, _ready):
        platform_org = platform_lens.get_or_create_platform_org()
        platform_gateway = Node.objects.create(
            organization=platform_org,
            name="platform-failed-restore-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=platform_org,
            gateway=platform_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )
        workspace_binding = self._workspace_binding(gateway_link)
        payload = self._manual_restore_payload()
        payload.update(
            {
                "target_ref_id": platform_gateway.id,
                "target_path": workspace_binding.resolved_path(),
                "idempotency_key": "lens-workspace:legacy-failure",
            }
        )
        record = restore_service.create_lens_workspace_restore_record(
            organization_id=self.org.id,
            workspace_binding_id=workspace_binding.id,
            data=payload,
        )
        product_task = Task.objects.get(pk=record.task_id)
        product_task.task_type = Task.Type.RESTORE
        product_task.save(update_fields=["task_type", "updated_at"])
        node_task = NodeTask.objects.get(
            kind="restore.run",
            correlation_id=str(record.task_uuid),
        )

        node_task.status = NodeTask.Status.FAILED
        node_task.last_error = "workspace restore failed"
        node_task.save(update_fields=["status", "last_error", "updated_at"])

        product_task.refresh_from_db()
        self.assertEqual(product_task.status, Task.Status.FAILED)
        self.assertEqual(
            product_task.task_type,
            Task.Type.INSIGHT_WORKSPACE_RESTORE,
        )

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_reconciler_classifies_lens_restore_completed_by_legacy_release(
        self, _ready
    ):
        record, _server_task, _repository_proxy = self._bound_repository_lens_restore(
            repository_type=Repository.Type.PROXY_FS,
        )
        product_task = Task.objects.get(id=record.task_id)
        Task.objects.filter(id=product_task.id).update(
            task_type=Task.Type.RESTORE,
            status=Task.Status.SUCCESS,
        )

        result = reconcile_restore_node_task_projections(limit=20)

        product_task.refresh_from_db()
        self.assertEqual(result["classified"], 1)
        self.assertEqual(
            product_task.task_type,
            Task.Type.INSIGHT_WORKSPACE_RESTORE,
        )

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_reconciler_retries_classification_when_projection_repair_fails(
        self, _ready
    ):
        record, _server_task, _repository_proxy = self._bound_repository_lens_restore(
            repository_type=Repository.Type.PROXY_FS,
        )
        product_task = Task.objects.get(id=record.task_id)
        Task.objects.filter(id=product_task.id).update(
            task_type=Task.Type.RESTORE,
            status=Task.Status.SUCCESS,
        )

        with patch(
            "apps.source.services.internal.source_pipeline.sync_task_pipeline_projection",
            side_effect=RuntimeError("projection unavailable"),
        ):
            failed_result = reconcile_restore_node_task_projections(limit=20)

        product_task.refresh_from_db()
        self.assertEqual(failed_result["classified"], 0)
        self.assertEqual(failed_result["classification_failed"], 1)
        self.assertEqual(product_task.task_type, Task.Type.RESTORE)

        result = reconcile_restore_node_task_projections(limit=20)

        product_task.refresh_from_db()
        self.assertEqual(result["classified"], 1)
        self.assertEqual(
            product_task.task_type,
            Task.Type.INSIGHT_WORKSPACE_RESTORE,
        )

    def test_public_restore_cannot_target_platform_gateway(self):
        platform_org = platform_lens.get_or_create_platform_org()
        platform_gateway = Node.objects.create(
            organization=platform_org,
            name="forbidden-platform-target",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        payload = self._manual_restore_payload()
        payload["target_ref_id"] = platform_gateway.id

        response = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RestoreRecord.objects.exists())

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_lens_workspace_restore_rejects_nonbinding_target_path(self, _ready):
        platform_org = platform_lens.get_or_create_platform_org()
        platform_gateway = Node.objects.create(
            organization=platform_org,
            name="platform-path-boundary-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=platform_org,
            gateway=platform_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )
        workspace_binding = self._workspace_binding(gateway_link)
        payload = self._manual_restore_payload()
        payload.update(
            {
                "target_ref_id": platform_gateway.id,
                "target_path": "/workspace/platform/another-tenant",
                "idempotency_key": "lens-workspace:path-boundary",
            }
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Lens workspace target path is not authoritative",
        ):
            restore_service.create_lens_workspace_restore_record(
                organization_id=self.org.id,
                workspace_binding_id=workspace_binding.id,
                data=payload,
            )

        self.assertFalse(RestoreRecord.objects.exists())

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_lens_workspace_restore_on_private_gateway_remains_in_tenant(self, _ready):
        private_gateway = Node.objects.create(
            organization=self.org,
            name="private-lens-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=private_gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        workspace_binding = self._workspace_binding(gateway_link)
        payload = self._manual_restore_payload()
        payload.update(
            {
                "target_ref_id": private_gateway.id,
                "target_path": workspace_binding.resolved_path(),
                "idempotency_key": "lens-workspace:private:1",
            }
        )

        record = restore_service.create_lens_workspace_restore_record(
            organization_id=self.org.id,
            workspace_binding_id=workspace_binding.id,
            data=payload,
        )

        self.assertEqual(record.target_execution_organization_id, self.org.id)
        node_task = NodeTask.objects.get(
            kind="restore.run",
            correlation_id=str(record.task_uuid),
        )
        self.assertEqual(node_task.organization_id, self.org.id)
        self.assertEqual(node_task.requesting_organization_id, self.org.id)

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_lens_workspace_restore_from_direct_nas_preserves_backup_shard(
        self, _ready
    ):
        private_gateway = Node.objects.create(
            organization=self.org,
            name="private-nas-lens-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=private_gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        workspace_binding = self._workspace_binding(gateway_link)
        backup_node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id="chat-direct-nas-backup",
            status=NodeTask.Status.SUCCESS,
            payload={},
            result={},
            watchdog_deadline_at=timezone.now(),
        )
        self.snapshot_dir.node_task_id = backup_node_task.id
        self.snapshot_dir.save(update_fields=["node_task_id", "updated_at"])
        self.repository.repo_type = Repository.Type.NAS
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.s3_bucket = ""
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/volume1/backup",
            "kopia_password": "repo-pass",
        }
        self.repository.save(
            update_fields=["repo_type", "nas_protocol", "s3_bucket", "config"]
        )
        payload = self._manual_restore_payload()
        payload.update(
            {
                "target_ref_id": private_gateway.id,
                "target_path": workspace_binding.resolved_path(),
                "idempotency_key": "lens-workspace:direct-nas:1",
            }
        )

        record = restore_service.create_lens_workspace_restore_record(
            organization_id=self.org.id,
            workspace_binding_id=workspace_binding.id,
            data=payload,
        )

        node_task = NodeTask.objects.get(
            kind="restore.run",
            correlation_id=str(record.task_uuid),
        )
        self.assertEqual(node_task.node_id, private_gateway.id)
        self.assertEqual(node_task.payload["repository"]["type"], Repository.Type.NAS)
        self.assertEqual(
            node_task.payload["repository"]["subdir"],
            f"hp-repos/agent-{self.agent.id}",
        )
        self.assertNotEqual(
            node_task.payload["repository"]["subdir"],
            f"hp-repos/agent-{private_gateway.id}",
        )

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_lens_workspace_restore_from_proxy_bound_nas_starts_server(self, _ready):
        record, server_task, repository_proxy = self._bound_repository_lens_restore(
            repository_type=Repository.Type.NAS
        )

        self.assertEqual(server_task.node_id, repository_proxy.id)
        self.assertEqual(server_task.payload["repository"]["type"], Repository.Type.NAS)
        self.assertEqual(
            server_task.payload["repository"]["subdir"],
            f"hp-repos/storage-{self.repository.id}",
        )
        self.assertFalse(
            NodeTask.objects.filter(
                kind="restore.run",
                correlation_id=str(record.task_uuid),
            ).exists()
        )

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_lens_workspace_restore_from_proxy_fs_starts_server(self, _ready):
        record, server_task, repository_proxy = self._bound_repository_lens_restore(
            repository_type=Repository.Type.PROXY_FS
        )

        self.assertEqual(server_task.node_id, repository_proxy.id)
        self.assertEqual(
            server_task.payload["repository"]["type"],
            Repository.Type.PROXY_FS,
        )
        self.assertFalse(
            NodeTask.objects.filter(
                kind="restore.run",
                correlation_id=str(record.task_uuid),
            ).exists()
        )

    def test_nas_restore_dispatches_mount_payload_and_execution_path(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="restore-nas-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.53",
        )
        target_nas = SourceResource.objects.create(
            organization=self.org,
            name="restore-target-nas",
            resource_type=ResourceType.NAS,
            bound_node=proxy,
            availability="online",
            config={"server": "10.0.0.20", "share": "restore"},
        )
        target_nas.set_credentials(
            {"username": "restore-user", "password": "nas-plain-secret"}
        )
        target_nas.save(update_fields=["credentials", "updated_at"])
        payload = self._manual_restore_payload()
        payload.update(
            {
                "target_type": "nas",
                "target_ref_id": target_nas.id,
                "target_path": "/restored/manual",
            }
        )

        response = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        node_task = NodeTask.objects.get(kind="restore.run")
        mount_root = target_nas.effective_mount_point()
        self.assertEqual(node_task.organization_id, self.org.id)
        self.assertEqual(node_task.node_id, proxy.id)
        self.assertEqual(node_task.payload["nas"]["mount_point"], mount_root)
        self.assertNotIn("password", node_task.payload["nas"])
        self.assertNotIn("nas-plain-secret", str(node_task.payload))
        self.assertIn("_delivery_secret", node_task.payload)
        self.assertEqual(
            node_task.payload["target_path"],
            "/restored/manual/file.txt",
        )
        record = RestoreRecord.objects.get(target_ref_id=target_nas.id)
        self.assertEqual(record.target_path, "/restored/manual")
        target_nas.config = {
            "protocol": "nfs",
            "server": "10.0.0.20",
            "export_path": "/nasshare",
        }
        target_nas.save(update_fields=["config", "updated_at"])

        detail = self.client.get(
            f"/api/v1/restore/records/{record.id}/",
            **self._headers(),
        )

        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.content)
        self.assertEqual(detail.data["target_path"], "/restored/manual")
        self.assertEqual(
            detail.data["target_display_path"], "/nasshare/restored/manual"
        )
        self.assertEqual(
            detail.data["items"][0]["target_display_path"],
            "/nasshare/restored/manual/file.txt",
        )

    def _active_restore_task(
        self,
        *,
        source_ref_id: int | None = None,
        status_value: str = Task.Status.RUNNING,
    ) -> Task:
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Restore active task",
            status=status_value,
            trigger_type=Task.TriggerType.API,
        )
        TaskResource.objects.create(
            task=task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=source_ref_id or self.agent.id,
        )
        return task

    def _assert_restore_already_running(self, response, task: Task) -> None:
        self.assertEqual(
            response.status_code, status.HTTP_409_CONFLICT, response.content
        )
        problem = response.data["data"]
        self.assertEqual(problem["code"], "RESTORE.ALREADY_RUNNING")
        self.assertEqual(problem["meta"]["task_uuid"], str(task.task_uuid))
        self.assertEqual(problem["meta"]["source_type"], "agent")
        self.assertEqual(problem["meta"]["source_ref_id"], self.agent.id)

    def test_create_restore_plan_persists_and_lists_after_refresh(self):
        create = self.client.post(
            "/api/v1/restore/plans/",
            self._plan_payload(),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        plan_id = create.data["id"]

        detail = self.client.get(f"/api/v1/restore/plans/{plan_id}/", **self._headers())
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["restore_dir"], "/restore/data")

        listing = self.client.get(
            f"/api/v1/restore/plans/?backup_config_id={self.config.id}",
            **self._headers(),
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(listing.data["results"][0]["id"], plan_id)

    def test_create_restore_plan_accepts_zero_sort_order_and_nested_source_path(self):
        payload = self._plan_payload()
        payload["source_path"] = "/data/subdir"
        payload["sort_order"] = 0

        create = self.client.post(
            "/api/v1/restore/plans/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(create.data["sort_order"], 0)
        self.assertEqual(create.data["source_path"], "/data/subdir")

    def test_snapshot_list_filters_restorable_statuses_and_includes_directory_snapshots(
        self,
    ):
        BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="snap-failed",
            idempotency_key="snap-failed",
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=2,
            status=BackupSourceSnapshot.Status.FAILED,
        )

        response = self.client.get(
            "/api/v1/protection/backup-source-snapshots/",
            {
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "status": "available,partial",
                "include_directory_snapshots": "1",
                "page_size": "200",
            },
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["id"], self.snapshot.id)
        self.assertEqual(row["directories"][0]["source_path"], "/data")
        self.assertEqual(
            row["directories"][0]["status"],
            BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

    def test_create_restore_plan_rejects_relative_source_path(self):
        payload = self._plan_payload()
        payload["source_path"] = "."

        create = self.client.post(
            "/api/v1/restore/plans/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(RestorePlan.objects.count(), 0)

    def test_create_restore_plan_accepts_windows_absolute_source_path(self):
        directory = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path=r"C:\data",
            path_type=BackupConfigDirectory.PathType.DIRECTORY,
            sort_order=1,
        )
        payload = self._plan_payload()
        payload["backup_config_dir_id"] = directory.id
        payload["source_path"] = r"C:\data"

        create = self.client.post(
            "/api/v1/restore/plans/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(create.data["source_path"], r"C:\data")

    def test_patch_restore_plan_rejects_relative_source_path(self):
        plan = RestorePlan.objects.create(
            organization_id=self.org.id,
            **self._plan_payload(),
        )

        patch = self.client.patch(
            f"/api/v1/restore/plans/{plan.id}/",
            {"source_path": "."},
            format="json",
            **self._headers(),
        )

        self.assertEqual(patch.status_code, status.HTTP_400_BAD_REQUEST)
        plan.refresh_from_db()
        self.assertEqual(plan.source_path, "/data")

    def test_run_restore_plan_rejects_relative_source_path(self):
        payload = self._plan_payload()
        payload["source_path"] = "."
        plan = RestorePlan.objects.create(
            organization_id=self.org.id,
            **payload,
        )

        run = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(RestoreRecord.objects.count(), 0)

    def test_run_restore_plan_creates_restore_record_task_and_item(self):
        plan = RestorePlan.objects.create(
            organization_id=self.org.id,
            **self._plan_payload(),
        )

        run = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        self.assertEqual(record.source_mode, RestoreRecord.SourceMode.PLAN)
        self.assertEqual(record.source_snapshot_id, self.snapshot.id)
        self.assertEqual(record.items.count(), 1)
        item = record.items.get()
        self.assertEqual(item.kopia_snapshot_id, "kopia-snapshot-1")
        self.assertEqual(item.target_path, "/restore/data/data")
        task = Task.objects.get(id=record.task_id)
        self.assertEqual(task.task_type, Task.Type.RESTORE)
        self.assertEqual(task.trigger_type, Task.TriggerType.MANUAL)
        self.assertEqual(task.status, Task.Status.RUNNING)
        self.assertEqual(task.request_payload["restore_record_id"], record.id)
        node_task = NodeTask.objects.get(
            correlation_type="restore.record", correlation_id=str(record.task_uuid)
        )
        self.assertEqual(node_task.payload["repository"]["type"], Repository.Type.S3)
        self.assertEqual(node_task.payload["target_path"], "/restore/data/data")
        self.assertEqual(node_task.payload["target_path_semantics"], "final")

    def test_create_whole_snapshot_restore_plan_and_run_source_expands_snapshot_directories(
        self,
    ):
        file_dir = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/readme.txt",
            path_type=BackupConfigDirectory.PathType.FILE,
            sort_order=1,
        )
        file_snapshot_dir = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=file_dir.id,
            source_path="/data/readme.txt",
            path_type=BackupSourceSnapshotDirectory.PathType.FILE,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-file-1",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        create = self.client.post(
            "/api/v1/restore/plans/",
            {
                "backup_config_id": self.config.id,
                "scope": "snapshot",
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "target_type": "agent",
                "target_ref_id": self.target.id,
                "restore_dir": "/restore/data",
                "conflict_mode": "overwrite",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(create.data["scope"], "snapshot")
        self.assertIsNone(create.data["backup_config_dir_id"])
        self.assertEqual(create.data["source_path"], "")

        run = self.client.post(
            "/api/v1/restore/plans/run-source/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        self.assertEqual(run.data["record_count"], 1)
        record = RestoreRecord.objects.get(
            id=run.data["records"][0]["restore_record_id"]
        )
        self.assertEqual(record.items.count(), 2)
        self.assertEqual(
            set(record.items.values_list("source_snapshot_directory_id", flat=True)),
            {self.snapshot_dir.id, file_snapshot_dir.id},
        )
        target_paths = {
            item.source_snapshot_directory_id: item.target_path
            for item in record.items.all()
        }
        self.assertEqual(target_paths[self.snapshot_dir.id], "/restore/data/data")
        self.assertEqual(target_paths[file_snapshot_dir.id], "/restore/data/readme.txt")

    def test_run_restore_plan_to_proxy_bound_nas_repository_uses_proxy_for_direct_restore(
        self,
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="restore-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.53",
        )
        self.repository.repo_type = Repository.Type.NAS
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.s3_bucket = ""
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = proxy.id
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/volume1/backup",
            "kopia_password": "repo-pass",
        }
        self.repository.save(
            update_fields=[
                "repo_type",
                "nas_protocol",
                "s3_bucket",
                "bind_node_type",
                "bind_node_id",
                "config",
            ]
        )
        payload = self._plan_payload()
        payload["target_ref_id"] = proxy.id
        plan = RestorePlan.objects.create(organization_id=self.org.id, **payload)

        run = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        node_task = NodeTask.objects.get(
            correlation_type="restore.record", correlation_id=str(record.task_uuid)
        )
        self.assertEqual(node_task.node_id, proxy.id)
        self.assertEqual(node_task.payload["repository"]["type"], Repository.Type.NAS)
        self.assertEqual(
            node_task.payload["repository"]["subdir"],
            f"hp-repos/storage-{self.repository.id}",
        )
        self.assertEqual(node_task.payload["repository_reader_node_id"], proxy.id)
        self.assertEqual(
            node_task.payload["restore_transfer_mode"], "direct_proxy_restore"
        )

    def test_run_restore_plan_to_other_agent_uses_proxy_repository_server(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="restore-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.53",
        )
        self.repository.repo_type = Repository.Type.NAS
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.s3_bucket = ""
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = proxy.id
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/volume1/backup",
            "kopia_password": "repo-pass",
            "proxy_repository_server_host": "10.0.0.65",
        }
        self.repository.save(
            update_fields=[
                "repo_type",
                "nas_protocol",
                "s3_bucket",
                "bind_node_type",
                "bind_node_id",
                "config",
            ]
        )
        backup_node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.target,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id="backup-source-owner",
            status=NodeTask.Status.SUCCESS,
            payload={},
            result={
                "snapshot": {
                    "source": {
                        "userName": "hfl-backup-6020",
                        "host": "hfl-proxy-74",
                    }
                }
            },
            watchdog_deadline_at=timezone.now(),
        )
        self.snapshot_dir.node_task_id = backup_node_task.id
        self.snapshot_dir.save(update_fields=["node_task_id", "updated_at"])
        plan = RestorePlan.objects.create(
            organization_id=self.org.id, **self._plan_payload()
        )

        run = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        server_task = NodeTask.objects.get(
            kind="repository.server.start", correlation_id=str(record.task_uuid)
        )
        self.assertEqual(server_task.node_id, proxy.id)
        self.assertEqual(server_task.correlation_type, "restore.repository_server")
        self.assertEqual(server_task.payload["public_host"], "10.0.0.65")
        self.assertEqual(
            server_task.payload["username"], "hfl-backup-6020@hfl-proxy-74"
        )
        self.assertEqual(server_task.payload["repository"]["type"], Repository.Type.NAS)
        self.assertFalse(
            NodeTask.objects.filter(
                kind="restore.run", correlation_id=str(record.task_uuid)
            ).exists()
        )

        server_task.status = NodeTask.Status.SUCCESS
        server_task.result = {
            "session_id": "restore-server-session",
            "server_url": "https://10.0.0.65:51515",
            "username": "hfl-backup-6020@hfl-proxy-74",
            "password": "server-pass",
            "server_cert_fingerprint": "ABC123",
        }
        server_task.save(update_fields=["status", "result", "updated_at"])

        node_task = NodeTask.objects.get(
            kind="restore.run", correlation_id=str(record.task_uuid)
        )
        self.assertEqual(node_task.node_id, self.target.id)
        self.assertEqual(node_task.payload["repository"]["type"], "kopia_server")
        self.assertEqual(
            node_task.payload["repository"]["url"], "https://10.0.0.65:51515"
        )
        self.assertEqual(
            node_task.payload["repository"]["username"], "hfl-backup-6020@hfl-proxy-74"
        )
        self.assertEqual(
            node_task.payload["restore_transfer_mode"],
            "proxy_repository_server_restore",
        )

        server_task.save(update_fields=["status", "result", "updated_at"])
        self.assertEqual(
            NodeTask.objects.filter(
                kind="restore.run", correlation_id=str(record.task_uuid)
            ).count(),
            1,
        )

        node_task.status = NodeTask.Status.SUCCESS
        node_task.result = {
            "snapshot_id": node_task.payload["snapshot_id"],
            "target_path": node_task.payload["target_path"],
            "selected_paths": [""],
            "restore_results": [],
            "count": 1,
        }
        node_task.save(update_fields=["status", "result", "updated_at"])

        task = Task.objects.get(id=record.task_id)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(float(task.progress), 100.0)
        self.assertEqual(task.current_step, "finalize")
        self.assertEqual(task.steps.get(step_name="dispatch_agent").status, "success")

        stop_task = NodeTask.objects.get(
            kind="repository.server.stop", correlation_id=str(record.task_uuid)
        )
        stop_task.status = NodeTask.Status.SUCCESS
        stop_task.result = {"session_id": "restore-server-session"}
        stop_task.save(update_fields=["status", "result", "updated_at"])

        task.refresh_from_db()
        self.assertEqual(float(task.progress), 100.0)
        self.assertEqual(task.current_step, "finalize")
        self.assertEqual(task.steps.get(step_name="dispatch_agent").status, "success")
        self.assertEqual(
            NodeTask.objects.filter(
                kind="restore.run", correlation_id=str(record.task_uuid)
            ).count(),
            1,
        )

    def test_run_restore_plan_accepts_partial_snapshot(self):
        self.snapshot.status = BackupSourceSnapshot.Status.PARTIAL
        self.snapshot.save(update_fields=["status"])
        plan = RestorePlan.objects.create(
            organization_id=self.org.id,
            **self._plan_payload(),
        )

        run = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        self.assertEqual(record.source_snapshot_id, self.snapshot.id)

    def test_run_restore_plan_for_nested_source_path_uses_selected_path(self):
        payload = self._plan_payload()
        payload["source_path"] = "/data/subdir"
        plan = RestorePlan.objects.create(
            organization_id=self.org.id,
            **payload,
        )

        run = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        item = RestoreRecord.objects.get(id=run.data["restore_record_id"]).items.get()
        self.assertEqual(item.source_path, "/data")
        self.assertEqual(item.selected_paths, ["subdir"])

    def test_run_restore_plan_skips_newer_snapshot_without_usable_plan_directory(self):
        self.snapshot.finished_at = timezone.now()
        self.snapshot.save(update_fields=["finished_at"])
        newer_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="snap-restore-newer-unusable",
            idempotency_key="snap-restore-newer-unusable",
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=2,
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=1,
            successful_directory_count=1,
            finished_at=timezone.now(),
        )
        BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=newer_snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=self.config_dir.id,
            source_path="/data",
            repository_id=self.repository.id,
            kopia_snapshot_id="",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        plan = RestorePlan.objects.create(
            organization_id=self.org.id, **self._plan_payload()
        )

        run = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        self.assertEqual(record.source_snapshot_id, self.snapshot.id)

    def test_run_restore_plan_batch_creates_one_record_for_file_and_directory_plans(
        self,
    ):
        file_dir = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/readme.txt",
            path_type=BackupConfigDirectory.PathType.FILE,
            sort_order=1,
        )
        file_snapshot_dir = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=file_dir.id,
            source_path="/data/readme.txt",
            path_type=BackupSourceSnapshotDirectory.PathType.FILE,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-file-1",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        self.config_dir.path_type = BackupConfigDirectory.PathType.DIRECTORY
        self.config_dir.save(update_fields=["path_type"])
        self.snapshot_dir.path_type = BackupSourceSnapshotDirectory.PathType.DIRECTORY
        self.snapshot_dir.save(update_fields=["path_type"])
        RestorePlan.objects.create(organization_id=self.org.id, **self._plan_payload())
        RestorePlan.objects.create(
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=file_dir.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            source_path="/data/readme.txt",
            target_type="agent",
            target_ref_id=self.target.id,
            restore_dir="/restore/data",
            conflict_mode="overwrite",
            sort_order=1,
        )

        run = self.client.post(
            "/api/v1/restore/plans/run-batch/",
            {
                "backup_config_id": self.config.id,
                "target_type": "agent",
                "target_ref_id": self.target.id,
                "restore_dir": "/restore/data",
                "conflict_mode": "overwrite",
                "idempotency_key": "batch-restore-1",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        self.assertEqual(run.data["item_count"], 2)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        task = Task.objects.get(id=record.task_id)
        self.assertEqual(task.trigger_type, Task.TriggerType.MANUAL)
        self.assertEqual(
            task.request_payload[RESTORE_EVENT_SCHEMA_KEY],
            RESTORE_EVENT_SCHEMA_VERSION,
        )
        self.assertEqual(record.items.count(), 2)
        self.assertEqual(
            record.request_payload["plan_ids"],
            list(RestorePlan.objects.values_list("id", flat=True)),
        )
        self.assertEqual(
            set(record.items.values_list("source_snapshot_directory_id", flat=True)),
            {self.snapshot_dir.id, file_snapshot_dir.id},
        )
        target_paths = {
            item.source_snapshot_directory_id: item.target_path
            for item in record.items.all()
        }
        self.assertEqual(target_paths[self.snapshot_dir.id], "/restore/data/data")
        self.assertEqual(target_paths[file_snapshot_dir.id], "/restore/data/readme.txt")
        node_tasks = NodeTask.objects.filter(
            correlation_type="restore.record", correlation_id=str(record.task_uuid)
        )
        self.assertEqual(node_tasks.count(), 2)
        self.assertEqual(
            set(node_tasks.values_list("payload__source_path_type", flat=True)),
            {"directory", "file"},
        )
        self.assertEqual(
            set(node_tasks.values_list("payload__target_path_semantics", flat=True)),
            {"final"},
        )
        start_event = TaskEvent.objects.get(
            task=task,
            step__step_name="restore",
            message="Restore execution started",
        )
        self.assertEqual(start_event.metadata["item_count"], 2)
        self.assertEqual(
            set(start_event.metadata["object_names"]),
            {"/data", "/data/readme.txt"},
        )

        for node_task in node_tasks:
            node_task.status = NodeTask.Status.SUCCESS
            node_task.result = {"restored": True}
            node_task.save(update_fields=["status", "result", "updated_at"])

        completed_events = TaskEvent.objects.filter(
            task=task,
            step__step_name="restore",
            message="Restore item completed",
        )
        self.assertEqual(completed_events.count(), 2)
        self.assertEqual(
            {
                (event.metadata["object_id"], event.metadata["object_name"])
                for event in completed_events
            },
            {
                ("kopia-snapshot-1", "/data"),
                ("kopia-file-1", "/data/readme.txt"),
            },
        )

    def test_run_restore_plans_for_source_suffixes_same_named_directories(self):
        first_dir = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/root/a/logs",
            path_type=BackupConfigDirectory.PathType.DIRECTORY,
            sort_order=1,
        )
        second_dir = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/root/b/logs",
            path_type=BackupConfigDirectory.PathType.DIRECTORY,
            sort_order=2,
        )
        first_snapshot_dir = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=first_dir.id,
            source_path="/root/a/logs",
            path_type=BackupSourceSnapshotDirectory.PathType.DIRECTORY,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-logs-a",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        second_snapshot_dir = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=second_dir.id,
            source_path="/root/b/logs",
            path_type=BackupSourceSnapshotDirectory.PathType.DIRECTORY,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-logs-b",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        for sort_order, directory in enumerate([first_dir, second_dir], start=1):
            RestorePlan.objects.create(
                organization_id=self.org.id,
                backup_config_id=self.config.id,
                backup_config_dir_id=directory.id,
                source_type="agent",
                source_ref_id=self.agent.id,
                source_path=directory.path,
                target_type="agent",
                target_ref_id=self.target.id,
                restore_dir="/restore",
                conflict_mode="overwrite",
                sort_order=sort_order,
            )

        run = self.client.post(
            "/api/v1/restore/plans/run-source/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(
            id=run.data["records"][0]["restore_record_id"]
        )
        target_paths = {
            item.source_snapshot_directory_id: item.target_path
            for item in record.items.all()
        }
        self.assertEqual(
            target_paths[first_snapshot_dir.id], "/restore/logs--from-root_a"
        )
        self.assertEqual(
            target_paths[second_snapshot_dir.id], "/restore/logs--from-root_b"
        )
        node_task_targets = set(
            NodeTask.objects.filter(
                correlation_type="restore.record", correlation_id=str(record.task_uuid)
            ).values_list("payload__target_path", flat=True)
        )
        self.assertEqual(
            node_task_targets,
            {"/restore/logs--from-root_a", "/restore/logs--from-root_b"},
        )

    def test_run_restore_plans_for_source_suffixes_same_named_files(self):
        first_file = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/root/a/test.txt",
            path_type=BackupConfigDirectory.PathType.FILE,
            sort_order=1,
        )
        second_file = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/root/b/test.txt",
            path_type=BackupConfigDirectory.PathType.FILE,
            sort_order=2,
        )
        first_snapshot_file = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=first_file.id,
            source_path="/root/a/test.txt",
            path_type=BackupSourceSnapshotDirectory.PathType.FILE,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-test-a",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        second_snapshot_file = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=second_file.id,
            source_path="/root/b/test.txt",
            path_type=BackupSourceSnapshotDirectory.PathType.FILE,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-test-b",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        for sort_order, directory in enumerate([first_file, second_file], start=1):
            RestorePlan.objects.create(
                organization_id=self.org.id,
                backup_config_id=self.config.id,
                backup_config_dir_id=directory.id,
                source_type="agent",
                source_ref_id=self.agent.id,
                source_path=directory.path,
                target_type="agent",
                target_ref_id=self.target.id,
                restore_dir="/restore",
                conflict_mode="overwrite",
                sort_order=sort_order,
            )

        run = self.client.post(
            "/api/v1/restore/plans/run-source/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(
            id=run.data["records"][0]["restore_record_id"]
        )
        target_paths = {
            item.source_snapshot_directory_id: item.target_path
            for item in record.items.all()
        }
        self.assertEqual(
            target_paths[first_snapshot_file.id], "/restore/test--from-root_a.txt"
        )
        self.assertEqual(
            target_paths[second_snapshot_file.id], "/restore/test--from-root_b.txt"
        )

    def test_run_restore_plans_for_source_suffixes_same_named_selected_files(self):
        for sort_order, source_path in enumerate(
            ["/data/inner_dir1/test.txt", "/data/inner_dir2/test.txt"],
            start=1,
        ):
            RestorePlan.objects.create(
                organization_id=self.org.id,
                backup_config_id=self.config.id,
                backup_config_dir_id=self.config_dir.id,
                source_type="agent",
                source_ref_id=self.agent.id,
                source_path=source_path,
                target_type="agent",
                target_ref_id=self.target.id,
                restore_dir="/restore",
                conflict_mode="overwrite",
                sort_order=sort_order,
            )

        run = self.client.post(
            "/api/v1/restore/plans/run-source/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(
            id=run.data["records"][0]["restore_record_id"]
        )
        target_paths = {
            tuple(item.selected_paths): item.target_path for item in record.items.all()
        }
        self.assertEqual(
            target_paths[("inner_dir1/test.txt",)],
            "/restore/test--from-data_inner_dir1.txt",
        )
        self.assertEqual(
            target_paths[("inner_dir2/test.txt",)],
            "/restore/test--from-data_inner_dir2.txt",
        )
        node_task_targets = set(
            NodeTask.objects.filter(
                correlation_type="restore.record", correlation_id=str(record.task_uuid)
            ).values_list("payload__target_path", flat=True)
        )
        self.assertEqual(
            node_task_targets,
            {
                "/restore/test--from-data_inner_dir1.txt",
                "/restore/test--from-data_inner_dir2.txt",
            },
        )

    def test_run_restore_plans_for_source_numbers_duplicate_sanitized_suffixes(self):
        for sort_order, source_path in enumerate(
            ["/data/a b/test.txt", "/data/a:b/test.txt", "/data/a/b/test.txt"],
            start=1,
        ):
            RestorePlan.objects.create(
                organization_id=self.org.id,
                backup_config_id=self.config.id,
                backup_config_dir_id=self.config_dir.id,
                source_type="agent",
                source_ref_id=self.agent.id,
                source_path=source_path,
                target_type="agent",
                target_ref_id=self.target.id,
                restore_dir="/restore",
                conflict_mode="overwrite",
                sort_order=sort_order,
            )

        run = self.client.post(
            "/api/v1/restore/plans/run-source/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(
            id=run.data["records"][0]["restore_record_id"]
        )
        target_paths = {
            tuple(item.selected_paths): item.target_path for item in record.items.all()
        }
        self.assertEqual(
            target_paths[("a b/test.txt",)], "/restore/test--from-data_a_b.txt"
        )
        self.assertEqual(
            target_paths[("a:b/test.txt",)], "/restore/test--from-data_a_b-2.txt"
        )
        self.assertEqual(
            target_paths[("a/b/test.txt",)], "/restore/test--from-data_a_b-3.txt"
        )

    def test_safe_restore_name_normalizes_cross_platform_source_slug(self):
        name = restore_service._safe_restore_name(
            r"C:\root d\logs\t.log",
            source_path_type=BackupSourceSnapshotDirectory.PathType.FILE,
        )

        self.assertEqual(name, "t--from-root_d_logs.log")

    def test_run_restore_plans_for_source_keeps_distinct_selected_file_names_unsuffixed(
        self,
    ):
        for sort_order, source_path in enumerate(
            ["/data/inner_dir1/a.txt", "/data/inner_dir2/b.txt"],
            start=1,
        ):
            RestorePlan.objects.create(
                organization_id=self.org.id,
                backup_config_id=self.config.id,
                backup_config_dir_id=self.config_dir.id,
                source_type="agent",
                source_ref_id=self.agent.id,
                source_path=source_path,
                target_type="agent",
                target_ref_id=self.target.id,
                restore_dir="/restore",
                conflict_mode="overwrite",
                sort_order=sort_order,
            )

        run = self.client.post(
            "/api/v1/restore/plans/run-source/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(
            id=run.data["records"][0]["restore_record_id"]
        )
        target_paths = {
            tuple(item.selected_paths): item.target_path for item in record.items.all()
        }
        self.assertEqual(target_paths[("inner_dir1/a.txt",)], "/restore/a.txt")
        self.assertEqual(target_paths[("inner_dir2/b.txt",)], "/restore/b.txt")

    def test_run_restore_plan_batch_uses_latest_snapshot_that_satisfies_all_plans(self):
        self.snapshot.finished_at = timezone.now()
        self.snapshot.save(update_fields=["finished_at"])
        file_dir = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/readme.txt",
            path_type=BackupConfigDirectory.PathType.FILE,
            sort_order=1,
        )
        file_snapshot_dir = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=file_dir.id,
            source_path="/data/readme.txt",
            path_type=BackupSourceSnapshotDirectory.PathType.FILE,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-file-older",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        newer_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="snap-restore-newer-partial",
            idempotency_key="snap-restore-newer-partial",
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=3,
            status=BackupSourceSnapshot.Status.PARTIAL,
            directory_count=2,
            successful_directory_count=1,
            failed_directory_count=1,
            finished_at=timezone.now(),
        )
        BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=newer_snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=self.config_dir.id,
            source_path="/data",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-data-newer",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        RestorePlan.objects.create(organization_id=self.org.id, **self._plan_payload())
        RestorePlan.objects.create(
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=file_dir.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            source_path="/data/readme.txt",
            target_type="agent",
            target_ref_id=self.target.id,
            restore_dir="/restore/data",
            conflict_mode="overwrite",
            sort_order=1,
        )

        run = self.client.post(
            "/api/v1/restore/plans/run-batch/",
            {
                "backup_config_id": self.config.id,
                "target_type": "agent",
                "target_ref_id": self.target.id,
                "restore_dir": "/restore/data",
                "conflict_mode": "overwrite",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        self.assertEqual(record.source_snapshot_id, self.snapshot.id)
        self.assertEqual(
            set(record.items.values_list("source_snapshot_directory_id", flat=True)),
            {self.snapshot_dir.id, file_snapshot_dir.id},
        )

    def test_run_restore_plan_batch_skips_disabled_plans(self):
        RestorePlan.objects.create(organization_id=self.org.id, **self._plan_payload())
        disabled_payload = self._plan_payload()
        disabled_payload["source_path"] = "/data/disabled"
        disabled_payload["sort_order"] = 1
        disabled_payload["enabled"] = False
        RestorePlan.objects.create(organization_id=self.org.id, **disabled_payload)

        run = self.client.post(
            "/api/v1/restore/plans/run-batch/",
            {
                "backup_config_id": self.config.id,
                "target_type": "agent",
                "target_ref_id": self.target.id,
                "restore_dir": "/restore/data",
                "conflict_mode": "overwrite",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        self.assertEqual(record.items.count(), 1)
        self.assertEqual(len(record.request_payload["plan_ids"]), 1)

    def test_run_restore_plan_batch_uses_selected_source_snapshot(self):
        newer_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="snap-newer-selected-test",
            idempotency_key="snap-newer-selected-test",
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=2,
            status=BackupSourceSnapshot.Status.AVAILABLE,
            finished_at=timezone.now(),
        )
        BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=newer_snapshot,
            backup_config_id=self.config.id,
            backup_config_dir_id=self.config_dir.id,
            source_path="/data",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-newer-selected-test",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        RestorePlan.objects.create(organization_id=self.org.id, **self._plan_payload())

        run = self.client.post(
            "/api/v1/restore/plans/run-batch/",
            {
                "backup_config_id": self.config.id,
                "target_type": "agent",
                "target_ref_id": self.target.id,
                "restore_dir": "/restore/data",
                "conflict_mode": "overwrite",
                "source_snapshot_id": self.snapshot.id,
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_201_CREATED, run.content)
        record = RestoreRecord.objects.get(id=run.data["restore_record_id"])
        self.assertEqual(record.source_snapshot_id, self.snapshot.id)

    def test_run_restore_plan_batch_rejects_selected_snapshot_that_does_not_cover_plan(
        self,
    ):
        uncovered_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="snap-uncovered",
            idempotency_key="snap-uncovered",
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=2,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        RestorePlan.objects.create(organization_id=self.org.id, **self._plan_payload())

        run = self.client.post(
            "/api/v1/restore/plans/run-batch/",
            {
                "backup_config_id": self.config.id,
                "target_type": "agent",
                "target_ref_id": self.target.id,
                "restore_dir": "/restore/data",
                "conflict_mode": "overwrite",
                "source_snapshot_id": uncovered_snapshot.id,
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RestoreRecord.objects.exists())

    def test_run_restore_plan_batch_rejects_no_matching_group(self):
        RestorePlan.objects.create(organization_id=self.org.id, **self._plan_payload())

        run = self.client.post(
            "/api/v1/restore/plans/run-batch/",
            {
                "backup_config_id": self.config.id,
                "target_type": "agent",
                "target_ref_id": self.target.id,
                "restore_dir": "/restore/other",
                "conflict_mode": "overwrite",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RestoreRecord.objects.exists())

    def test_create_manual_restore_record_creates_task_and_item(self):
        payload = {
            "source_snapshot_id": self.snapshot.id,
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "target_type": "agent",
            "target_ref_id": self.target.id,
            "target_path": "/restore/manual",
            "scope": "paths",
            "conflict_mode": "skip",
            "items": [
                {
                    "source_snapshot_directory_id": self.snapshot_dir.id,
                    "selected_paths": ["subdir/file.txt"],
                }
            ],
        }

        create = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        self.assertEqual(record.source_mode, RestoreRecord.SourceMode.MANUAL)
        self.assertEqual(record.target_path, "/restore/manual")
        item = record.items.get()
        self.assertEqual(item.selected_paths, ["subdir/file.txt"])
        self.assertEqual(item.source_path, "/data")
        task = Task.objects.get(id=record.task_id)
        self.assertEqual(task.task_type, Task.Type.RESTORE)
        self.assertEqual(task.status, Task.Status.RUNNING)
        self.assertEqual(
            task.request_payload[RESTORE_EVENT_SCHEMA_KEY],
            RESTORE_EVENT_SCHEMA_VERSION,
        )
        start_event = TaskEvent.objects.get(
            task=task,
            step__step_name="restore",
            message="Restore execution started",
        )
        self.assertEqual(start_event.metadata["item_count"], 1)
        self.assertEqual(start_event.metadata["object_names"], ["/data"])
        source_resource = task.resources.get(
            resource_type=TaskResource.Type.BACKUP_SOURCE
        )
        self.assertEqual(source_resource.resource_subtype, "agent")
        self.assertEqual(source_resource.resource_id, self.agent.id)
        node_task = NodeTask.objects.get(
            correlation_type="restore.record", correlation_id=str(record.task_uuid)
        )
        self.assertEqual(node_task.payload["repository"]["type"], Repository.Type.S3)
        self.assertEqual(node_task.payload["selected_paths"], ["subdir/file.txt"])
        node_task.status = NodeTask.Status.SUCCESS
        node_task.result = {"restored": True}
        node_task.save(update_fields=["status", "result", "updated_at"])
        item.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(item.status, item.Status.SUCCESS)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(task.steps.get(step_name="restore").status, "success")
        completed_event = TaskEvent.objects.get(
            task=task,
            step__step_name="restore",
            message="Restore item completed",
        )
        self.assertEqual(completed_event.level, TaskEvent.Level.INFO)
        self.assertEqual(completed_event.metadata["object_id"], item.kopia_snapshot_id)
        self.assertEqual(completed_event.metadata["object_name"], "/data")
        self.assertEqual(completed_event.metadata["source_path"], "/data")
        self.assertEqual(completed_event.metadata["target_path"], item.target_path)
        self.assertEqual(completed_event.metadata["node_task_id"], str(node_task.id))
        dispatch_event = TaskEvent.objects.get(
            task=task,
            step__step_name="dispatch_agent",
            message="Restore item dispatched to agent",
        )
        self.assertEqual(dispatch_event.metadata["object_id"], item.kopia_snapshot_id)
        self.assertEqual(dispatch_event.metadata["object_name"], "/data")
        self.assertEqual(dispatch_event.metadata["source_path"], "/data")
        self.assertEqual(dispatch_event.metadata["target_path"], item.target_path)
        finalize_event = TaskEvent.objects.get(
            task=task,
            step__step_name="finalize",
            message="Restore finished successfully",
        )
        self.assertEqual(finalize_event.metadata["object_name"], record.restore_uid)

        node_task.save(update_fields=["updated_at"])
        self.assertEqual(
            TaskEvent.objects.filter(
                task=task,
                step__step_name="restore",
                message="Restore item completed",
            ).count(),
            1,
        )

    def test_restore_record_list_and_detail_include_task_summary(self):
        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        task = Task.objects.get(id=record.task_id)
        started_at = timezone.now()
        task.progress = 42.5
        task.started_at = started_at
        task.save(update_fields=["progress", "started_at", "updated_at"])

        response = self.client.get(
            "/api/v1/restore/records/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        row = response.data["results"][0]
        self.assertEqual(row["source_snapshot_uid"], self.snapshot.snapshot_uid)
        summary = row["task_summary"]
        self.assertEqual(summary["status"], Task.Status.RUNNING)
        self.assertEqual(float(summary["progress"]), 42.5)
        self.assertIsNotNone(summary["started_at"])
        self.assertIsNone(summary["finished_at"])

        detail = self.client.get(
            f"/api/v1/restore/records/{record.id}/",
            **self._headers(),
        )
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.content)
        self.assertEqual(detail.data["source_snapshot_uid"], self.snapshot.snapshot_uid)
        self.assertEqual(detail.data["task_summary"], summary)

    def test_restore_record_returns_empty_snapshot_uid_when_snapshot_is_missing(self):
        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        BackupSourceSnapshot.objects.filter(id=record.source_snapshot_id).delete()

        detail = self.client.get(
            f"/api/v1/restore/records/{record.id}/",
            **self._headers(),
        )

        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.content)
        self.assertEqual(detail.data["source_snapshot_uid"], "")

    def test_restore_record_detail_returns_null_task_summary_when_task_is_missing(self):
        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        Task.objects.filter(id=record.task_id).delete()

        detail = self.client.get(
            f"/api/v1/restore/records/{record.id}/",
            **self._headers(),
        )

        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.content)
        self.assertIsNone(detail.data["task_summary"])

    def test_create_manual_restore_record_rejects_same_source_running_restore(self):
        active_task = self._active_restore_task(status_value=Task.Status.RUNNING)

        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )

        self._assert_restore_already_running(create, active_task)
        self.assertFalse(RestoreRecord.objects.exists())

    def test_create_manual_restore_record_is_blocked_by_source_unregister(self):
        unregister_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Unregister backup source",
            status=Task.Status.RUNNING,
        )
        TaskResource.objects.create(
            task=unregister_task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=self.agent.id,
            is_primary=True,
        )

        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "active Reset or Deregistration operation",
            str(create.data),
        )
        self.assertFalse(RestoreRecord.objects.exists())

    def test_create_manual_restore_record_rejects_same_source_pending_restore(self):
        active_task = self._active_restore_task(status_value=Task.Status.PENDING)

        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )

        self._assert_restore_already_running(create, active_task)
        self.assertFalse(RestoreRecord.objects.exists())

    def test_create_manual_restore_record_ignores_terminal_same_source_restore(self):
        self._active_restore_task(status_value=Task.Status.CANCELLED)

        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(RestoreRecord.objects.count(), 1)

    def test_create_manual_restore_record_allows_different_source_restore(self):
        self._active_restore_task(
            source_ref_id=self.agent.id, status_value=Task.Status.RUNNING
        )
        other_agent = Node.objects.create(
            organization=self.org,
            name="restore-agent-2",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.53",
        )
        other_config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Restore config 2",
            source_type="agent",
            source_ref_id=other_agent.id,
            repository_id=self.repository.id,
            compression_level=BackupConfig.CompressionLevel.BALANCED,
            recovery_plan_enabled=True,
        )
        other_config_dir = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=other_config,
            path="/data",
            path_type=BackupConfigDirectory.PathType.DIRECTORY,
            sort_order=0,
        )
        other_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="snap-restore-2",
            idempotency_key="snap-restore-2",
            source_type="agent",
            source_ref_id=other_agent.id,
            backup_config_id=other_config.id,
            repository_id=self.repository.id,
            task_id=2,
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=1,
            successful_directory_count=1,
        )
        other_snapshot_dir = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=other_snapshot,
            backup_config_id=other_config.id,
            backup_config_dir_id=other_config_dir.id,
            source_path="/data",
            path_type=BackupSourceSnapshotDirectory.PathType.DIRECTORY,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-2",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        payload = self._manual_restore_payload()
        payload.update(
            {
                "source_snapshot_id": other_snapshot.id,
                "source_ref_id": other_agent.id,
                "items": [{"source_snapshot_directory_id": other_snapshot_dir.id}],
            }
        )

        create = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(RestoreRecord.objects.count(), 1)

    def test_run_restore_plan_batch_rejects_same_source_running_restore(self):
        active_task = self._active_restore_task(status_value=Task.Status.RUNNING)
        RestorePlan.objects.create(
            organization_id=self.org.id,
            **self._plan_payload(),
        )

        run = self.client.post(
            "/api/v1/restore/plans/run-batch/",
            {
                "backup_config_id": self.config.id,
                "target_type": "agent",
                "target_ref_id": self.target.id,
                "restore_dir": "/restore/data",
                "conflict_mode": "overwrite",
                "source_snapshot_id": self.snapshot.id,
            },
            format="json",
            **self._headers(),
        )

        self._assert_restore_already_running(run, active_task)
        self.assertFalse(RestoreRecord.objects.exists())

    def test_create_manual_restore_record_accepts_partial_snapshot(self):
        self.snapshot.status = BackupSourceSnapshot.Status.PARTIAL
        self.snapshot.save(update_fields=["status"])
        payload = {
            "source_snapshot_id": self.snapshot.id,
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "target_type": "agent",
            "target_ref_id": self.target.id,
            "target_path": "/restore/manual",
            "scope": "paths",
            "conflict_mode": "skip",
            "items": [{"source_snapshot_directory_id": self.snapshot_dir.id}],
        }

        create = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        self.assertEqual(record.source_snapshot_id, self.snapshot.id)

    def test_create_manual_restore_record_rejects_failed_snapshot(self):
        self.snapshot.status = BackupSourceSnapshot.Status.FAILED
        self.snapshot.save(update_fields=["status"])
        payload = {
            "source_snapshot_id": self.snapshot.id,
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "target_type": "agent",
            "target_ref_id": self.target.id,
            "target_path": "/restore/manual",
            "scope": "paths",
            "conflict_mode": "skip",
            "items": [{"source_snapshot_directory_id": self.snapshot_dir.id}],
        }

        create = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RestoreRecord.objects.exists())

    def test_create_manual_restore_record_dispatches_source_path_type(self):
        self.snapshot_dir.path_type = BackupSourceSnapshotDirectory.PathType.FILE
        self.snapshot_dir.source_path = "/data/readme.txt"
        self.snapshot_dir.save(update_fields=["path_type", "source_path"])
        payload = {
            "source_snapshot_id": self.snapshot.id,
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "target_type": "agent",
            "target_ref_id": self.target.id,
            "target_path": "/restore/manual",
            "scope": "snapshot",
            "conflict_mode": "overwrite",
            "items": [{"source_snapshot_directory_id": self.snapshot_dir.id}],
        }

        create = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        self.assertEqual(
            record.expanded_payload["items"][0]["source_path_type"], "file"
        )
        node_task = NodeTask.objects.get(
            correlation_type="restore.record", correlation_id=str(record.task_uuid)
        )
        self.assertEqual(node_task.payload["source_path_type"], "file")
        self.assertEqual(node_task.payload["path_type"], "file")

    def test_restore_agent_failure_updates_task_error_and_steps(self):
        payload = {
            "source_snapshot_id": self.snapshot.id,
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "target_type": "agent",
            "target_ref_id": self.target.id,
            "target_path": "/restore/manual",
            "scope": "paths",
            "conflict_mode": "skip",
            "items": [{"source_snapshot_directory_id": self.snapshot_dir.id}],
        }
        create = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        node_task = NodeTask.objects.get(
            correlation_type="restore.record", correlation_id=str(record.task_uuid)
        )

        node_task.status = NodeTask.Status.FAILED
        node_task.last_error = "open repository: repository is not connected"
        node_task.result = {"stderr": node_task.last_error}
        task = Task.objects.get(id=record.task_id)
        task.progress = 37
        task.save(update_fields=["progress", "updated_at"])
        node_task.save(update_fields=["status", "last_error", "result", "updated_at"])

        task.refresh_from_db()
        item = record.items.get()
        self.assertEqual(item.status, item.Status.FAILED)
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(task.task_type, Task.Type.RESTORE)
        self.assertEqual(float(task.progress), 37)
        self.assertIn("repository is not connected", task.error_message)
        self.assertEqual(task.steps.get(step_name="restore").status, "failed")
        self.assertEqual(task.steps.get(step_name="finalize").status, "failed")
        failed_event = TaskEvent.objects.get(
            task=task,
            step__step_name="restore",
            message="Restore item failed",
        )
        self.assertEqual(failed_event.level, TaskEvent.Level.ERROR)
        self.assertEqual(failed_event.metadata["object_id"], item.kopia_snapshot_id)
        self.assertEqual(failed_event.metadata["object_name"], item.source_path)
        self.assertEqual(failed_event.metadata["error_code"], "RESTORE_AGENT_FAILED")
        self.assertIn(
            "repository is not connected", failed_event.metadata["error_message"]
        )
        self.assertTrue(
            TaskEvent.objects.filter(
                task=task,
                step__step_name="finalize",
                message="Restore finished with failed items",
            ).exists()
        )

    def test_agent_cancellation_without_product_cancellation_fails_restore(self):
        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        node_task = NodeTask.objects.get(
            correlation_type="restore.record",
            correlation_id=str(record.task_uuid),
        )

        node_task.status = NodeTask.Status.CANCELED
        node_task.last_error = "Agent restore was cancelled unexpectedly"
        node_task.save(update_fields=["status", "last_error", "updated_at"])

        task = Task.objects.get(id=record.task_id)
        item = record.items.get()
        self.assertEqual(item.status, RestoreRecordItem.Status.CANCELLED)
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(task.error_code, "RESTORE_FAILED")
        self.assertEqual(task.result_payload["failed_item_count"], 1)

    def test_restore_agent_restart_reaches_clear_terminal_state(self):
        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        node_task = NodeTask.objects.get(
            correlation_type="restore.record",
            correlation_id=str(record.task_uuid),
        )

        node_task.status = NodeTask.Status.FAILED
        node_task.last_error = "agent restarted before task completed"
        node_task.save(update_fields=["status", "last_error", "updated_at"])

        task = Task.objects.get(id=record.task_id)
        item = record.items.get()
        self.assertEqual(item.status, item.Status.FAILED)
        self.assertEqual(item.error_code, "AGENT_RESTARTED")
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertIn("agent restarted", task.error_message.lower())

    def test_run_restore_plan_rejects_missing_kopia_snapshot_id(self):
        self.snapshot_dir.kopia_snapshot_id = ""
        self.snapshot_dir.save(update_fields=["kopia_snapshot_id"])
        plan = RestorePlan.objects.create(
            organization_id=self.org.id,
            **self._plan_payload(),
        )

        run = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(run.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RestoreRecord.objects.exists())

    def test_cross_tenant_snapshot_is_rejected(self):
        other_org = Organization.objects.create(
            key="restore-other-org", name="Restore Other Org"
        )
        other_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=other_org.id,
            snapshot_uid="snap-other",
            idempotency_key="snap-other",
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=2,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        payload = {
            "source_snapshot_id": other_snapshot.id,
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "target_type": "agent",
            "target_ref_id": self.target.id,
            "target_path": "/restore/manual",
            "scope": "snapshot",
            "conflict_mode": "skip",
            "items": [{"source_snapshot_directory_id": self.snapshot_dir.id}],
        }

        create = self.client.post(
            "/api/v1/restore/records/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RestoreRecord.objects.exists())

    @patch("apps.restore.services.snapshot_browser.run_agent_task_sync")
    def test_browse_restore_snapshot_directory_uses_target_node(
        self, mock_run_agent_task_sync
    ):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "entries": [
                    {
                        "name": "docs",
                        "path": "docs",
                        "type": "dir",
                        "is_dir": True,
                        "size_bytes": 0,
                    },
                    {
                        "name": "readme.txt",
                        "path": "docs/readme.txt",
                        "type": "file",
                        "is_dir": False,
                        "size_bytes": 12,
                    },
                ]
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/restore/snapshot-directories/{self.snapshot_dir.id}/browse/"
            f"?target_node_id={self.target.id}&path=docs&limit=50",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["directory_id"], self.snapshot_dir.id)
        self.assertEqual(response.data["entries"][0]["type"], "dir")
        self.assertEqual(response.data["entries"][1]["type"], "file")
        self.assertTrue(response.data["entries"][1]["downloadable"])
        mock_run_agent_task_sync.assert_called_once()
        self.assertEqual(
            mock_run_agent_task_sync.call_args.kwargs["node_id"], self.target.id
        )
        self.assertNotEqual(
            mock_run_agent_task_sync.call_args.kwargs["node_id"], self.agent.id
        )
        payload = mock_run_agent_task_sync.call_args.kwargs["payload"]
        self.assertEqual(payload["snapshot_id"], "kopia-snapshot-1")
        self.assertEqual(payload["path"], "docs")

    @patch("apps.restore.services.snapshot_browser.run_agent_task_sync")
    def test_browse_direct_nas_snapshot_uses_target_node_and_backup_shard(
        self, mock_run_agent_task_sync
    ):
        backup_node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id="browse-direct-nas-backup",
            status=NodeTask.Status.SUCCESS,
            payload={},
            result={},
            watchdog_deadline_at=timezone.now(),
        )
        self.snapshot_dir.node_task_id = backup_node_task.id
        self.snapshot_dir.save(update_fields=["node_task_id", "updated_at"])
        self.repository.repo_type = Repository.Type.NAS
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.s3_bucket = ""
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/volume1/backup",
            "kopia_password": "repo-pass",
        }
        self.repository.save(
            update_fields=["repo_type", "nas_protocol", "s3_bucket", "config"]
        )
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={"entries": []},
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/restore/snapshot-directories/{self.snapshot_dir.id}/browse/"
            f"?target_node_id={self.target.id}",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(
            mock_run_agent_task_sync.call_args.kwargs["node_id"],
            self.target.id,
        )
        repository_payload = mock_run_agent_task_sync.call_args.kwargs["payload"][
            "repository"
        ]
        self.assertEqual(
            repository_payload["subdir"],
            f"hp-repos/agent-{self.agent.id}",
        )

    @patch("apps.restore.services.snapshot_browser.run_agent_task_sync")
    def test_browse_restore_snapshot_directory_rejects_offline_target(
        self, mock_run_agent_task_sync
    ):
        self.target.availability = Node.Availability.OFFLINE
        self.target.save(update_fields=["availability"])

        response = self.client.get(
            f"/api/v1/restore/snapshot-directories/{self.snapshot_dir.id}/browse/"
            f"?target_node_id={self.target.id}",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        mock_run_agent_task_sync.assert_not_called()

    @patch("apps.restore.services.snapshot_browser.run_agent_task_sync")
    def test_restore_snapshot_directory_path_info_uses_target_node(
        self, mock_run_agent_task_sync
    ):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "entries": [
                    {
                        "name": "readme.txt",
                        "path": "docs/readme.txt",
                        "type": "file",
                        "is_dir": False,
                        "size_bytes": 12,
                    },
                ],
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/restore/snapshot-directories/{self.snapshot_dir.id}/path-info/"
            f"?target_node_id={self.target.id}&path=docs/readme.txt",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["path"], "docs/readme.txt")
        self.assertEqual(response.data["type"], "file")
        self.assertFalse(response.data["is_dir"])
        mock_run_agent_task_sync.assert_called_once()
        self.assertEqual(
            mock_run_agent_task_sync.call_args.kwargs["node_id"], self.target.id
        )
        payload = mock_run_agent_task_sync.call_args.kwargs["payload"]
        self.assertEqual(payload["path"], "docs")

    def test_cancel_restore_task_marks_items_cancelled(self):
        from apps.task.services.interface import create_task

        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Running restore",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "restore_uid": "rst-cancel-test",
                RESTORE_EVENT_SCHEMA_KEY: RESTORE_EVENT_SCHEMA_VERSION,
            },
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                }
            ],
            steps=["prepare_restore", "dispatch_agent", "restore", "finalize"],
        )
        task.status = Task.Status.RUNNING
        task.progress = 34
        task.save(update_fields=["status", "progress", "updated_at"])
        record = RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=self.org.id,
            target_execution_node_id=self.target.id,
            restore_uid="rst-cancel-test",
            source_mode=RestoreRecord.SourceMode.PLAN,
            plan_id=None,
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            source_snapshot_id=self.snapshot.id,
            target_type="agent",
            target_ref_id=self.target.id,
            target_path="/restore/data",
            scope=RestoreRecord.Scope.SNAPSHOT,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )
        RestoreRecordItem.objects.create(
            organization_id=self.org.id,
            restore_record=record,
            source_snapshot_directory_id=self.snapshot_dir.id,
            backup_config_dir_id=self.config_dir.id,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-1",
            source_path="/data",
            target_path="/restore/data",
            conflict_mode=RestoreRecordItem.ConflictMode.OVERWRITE,
            status=RestoreRecordItem.Status.RUNNING,
        )

        response = self.client.post(
            f"/api/v1/restore/tasks/{record.task_uuid}/cancel/",
            {},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        task.refresh_from_db()
        item = record.items.first()
        self.assertIsNotNone(item)
        item.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)
        self.assertEqual(float(task.progress), 34)
        self.assertEqual(item.status, RestoreRecordItem.Status.CANCELLED)
        cancelled_event = TaskEvent.objects.get(
            task=task,
            step__step_name="restore",
            message="Restore item cancelled",
        )
        self.assertEqual(cancelled_event.level, TaskEvent.Level.WARN)
        self.assertEqual(cancelled_event.metadata["object_id"], "kopia-snapshot-1")
        self.assertEqual(cancelled_event.metadata["object_name"], "/data")
        self.assertEqual(cancelled_event.metadata["error_code"], "TASK_CANCELLED")
        self.assertEqual(
            cancelled_event.metadata["error_message"], "Stopped from console"
        )

    def test_cancel_pending_agent_task_cannot_finalize_restore_as_success(self):
        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        task = Task.objects.get(id=record.task_id)
        node_task = NodeTask.objects.get(
            correlation_type="restore.record",
            correlation_id=str(record.task_uuid),
        )
        node_task.status = NodeTask.Status.PENDING
        node_task.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            f"/api/v1/restore/tasks/{record.task_uuid}/cancel/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        task.refresh_from_db()
        node_task.refresh_from_db()
        item = record.items.get()
        self.assertEqual(task.status, Task.Status.CANCELLED)
        self.assertEqual(node_task.status, NodeTask.Status.CANCELED)
        self.assertEqual(item.status, RestoreRecordItem.Status.CANCELLED)
        self.assertFalse(
            TaskEvent.objects.filter(
                task=task,
                message="Restore finished successfully",
            ).exists()
        )

    def test_cancel_restore_preserves_already_failed_item(self):
        from apps.task.services.interface import create_task

        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Partially failed restore",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "restore_uid": "rst-cancel-preserve-failure",
                RESTORE_EVENT_SCHEMA_KEY: RESTORE_EVENT_SCHEMA_VERSION,
            },
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                }
            ],
            steps=["prepare_restore", "dispatch_agent", "restore", "finalize"],
        )
        task.status = Task.Status.RUNNING
        task.save(update_fields=["status", "updated_at"])
        record = RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=self.org.id,
            target_execution_node_id=self.target.id,
            restore_uid="rst-cancel-preserve-failure",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=self.agent.id,
            source_snapshot_id=self.snapshot.id,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=self.target.id,
            target_path="/restore/data",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )
        failed_item = RestoreRecordItem.objects.create(
            organization_id=self.org.id,
            restore_record=record,
            source_snapshot_directory_id=self.snapshot_dir.id,
            backup_config_dir_id=self.config_dir.id,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-failed",
            source_path="/failed",
            target_path="/restore/data/failed",
            conflict_mode=RestoreRecordItem.ConflictMode.OVERWRITE,
            status=RestoreRecordItem.Status.FAILED,
            error_code="RESTORE_AGENT_FAILED",
            error_message="Original restore failure",
            terminal_projection_at=timezone.now(),
        )
        running_item = RestoreRecordItem.objects.create(
            organization_id=self.org.id,
            restore_record=record,
            source_snapshot_directory_id=self.snapshot_dir.id + 1,
            backup_config_dir_id=self.config_dir.id + 1,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-running",
            source_path="/running",
            target_path="/restore/data/running",
            conflict_mode=RestoreRecordItem.ConflictMode.OVERWRITE,
            status=RestoreRecordItem.Status.RUNNING,
        )

        response = self.client.post(
            f"/api/v1/restore/tasks/{record.task_uuid}/cancel/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        failed_item.refresh_from_db()
        running_item.refresh_from_db()
        self.assertEqual(failed_item.status, RestoreRecordItem.Status.FAILED)
        self.assertEqual(failed_item.error_code, "RESTORE_AGENT_FAILED")
        self.assertEqual(failed_item.error_message, "Original restore failure")
        self.assertEqual(running_item.status, RestoreRecordItem.Status.CANCELLED)

    @patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_cancel_pending_repository_server_does_not_record_restore_failure(
        self, _ready
    ):
        record, server_task, _repository_proxy = self._bound_repository_lens_restore(
            repository_type=Repository.Type.PROXY_FS,
        )
        task = Task.objects.get(id=record.task_id)
        self.assertEqual(server_task.status, NodeTask.Status.PENDING)

        response = self.client.post(
            f"/api/v1/restore/tasks/{record.task_uuid}/cancel/",
            {},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        task.refresh_from_db()
        server_task.refresh_from_db()
        item = record.items.get()
        self.assertEqual(task.status, Task.Status.CANCELLED)
        self.assertEqual(server_task.status, NodeTask.Status.CANCELED)
        self.assertEqual(item.status, RestoreRecordItem.Status.CANCELLED)
        self.assertFalse(
            TaskEvent.objects.filter(
                task=task,
                message="Restore repository server failed",
            ).exists()
        )

    def test_legacy_restore_task_does_not_receive_new_restore_step_events(self):
        create = self.client.post(
            "/api/v1/restore/records/",
            self._manual_restore_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        record = RestoreRecord.objects.get(id=create.data["restore_record_id"])
        task = Task.objects.get(id=record.task_id)
        item = record.items.get()
        node_task = NodeTask.objects.get(
            correlation_type="restore.record",
            correlation_id=str(record.task_uuid),
        )
        request_payload = dict(task.request_payload)
        request_payload.pop(RESTORE_EVENT_SCHEMA_KEY)
        task.request_payload = request_payload
        task.save(update_fields=["request_payload", "updated_at"])

        new_messages = [
            "Restore execution started",
            "Restore item completed",
            "Restore item failed",
            "Restore item cancelled",
        ]
        TaskEvent.objects.filter(task=task, message__in=new_messages).delete()

        self.assertIsNone(
            append_restore_execution_started_event(
                task=task,
                record=record,
                items=[item],
            )
        )
        previous_status = item.status
        item.status = RestoreRecordItem.Status.CANCELLED
        item.error_code = "TASK_CANCELLED"
        item.error_message = "Legacy task stopped"
        item.save(update_fields=["status", "error_code", "error_message", "updated_at"])
        self.assertIsNone(
            append_restore_item_terminal_event(
                task=task,
                item=item,
                node_task_id=node_task.id,
                previous_status=previous_status,
            )
        )
        self.assertFalse(
            TaskEvent.objects.filter(task=task, message__in=new_messages).exists()
        )

    def test_run_restore_plan_rejects_when_restore_already_running(self):
        plan = RestorePlan.objects.create(
            organization_id=self.org.id, **self._plan_payload()
        )
        first = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.content)
        record = RestoreRecord.objects.get(id=first.data["restore_record_id"])
        task = Task.objects.get(task_uuid=record.task_uuid)
        task.status = Task.Status.RUNNING
        task.save(update_fields=["status", "updated_at"])

        second = self.client.post(
            f"/api/v1/restore/plans/{plan.id}/run/",
            {},
            format="json",
            **self._headers(),
        )
        self._assert_restore_already_running(second, task)
