import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from common.errors import AppError
from apps.iam.models import Organization
from apps.lens_bridge.api.serializers import LensSessionLinkSerializer
from apps.lens_bridge.models import (
    LensAssistantLink,
    LensGatewayChatSlot,
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import (
    assistant_access,
    chat_data_update,
    chat_lifecycle,
    knowledge_source_sync,
    knowledge_source_teardown,
    managed_datasource,
    sl_client,
)
from apps.lens_bridge.tasks.chat_lifecycle import (
    reconcile_copilot_chat_provisions_task,
    reconcile_lens_resource_teardowns_task,
)
from apps.lens_bridge.tasks.chat_data_update import reconcile_chat_data_updates_task
from apps.node.models import Node, NodeTask
from apps.protection.models import (
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
    SnapshotUsageLease,
)
from apps.protection.services.snapshot_usage import (
    acquire_snapshot_usage,
    reconcile_snapshot_usage_leases,
)
from apps.restore.models import RestoreRecord, RestoreRecordItem


class CopilotChatTeardownTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(key="teardown-tenant", name="Tenant")
        self.platform_org = Organization.objects.create(
            key="__platform_lens__",
            name="Platform Lens",
        )
        self.user = get_user_model().objects.create_user(
            username="teardown@example.test",
            email="teardown@example.test",
        )
        self.gateway = Node.objects.create(
            organization=self.platform_org,
            name="platform-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.source_agent = Node.objects.create(
            organization=self.tenant,
            name="chat-teardown-source",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.gateway_link = LensGatewayLink.objects.create(
            organization=self.platform_org,
            gateway=self.gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            workspace_root="/workspace/platform/data",
        )
        self.knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.tenant,
            name="Chat workspace",
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            backup_source_snapshot_id=11,
            backup_snapshot_directory_id=12,
            source_path="/data",
            workspace_path_on_lensnode="/workspace/platform/data/tenants/1/ks/workspace",
            sl_assistant_uuid=uuid.uuid4(),
            status=LensKnowledgeSource.Status.READY,
            created_by=self.user,
        )
        self.workspace_binding = LensWorkspaceBinding.objects.create(
            organization=self.tenant,
            knowledge_source=self.knowledge_source,
            gateway_link=self.gateway_link,
            execution_organization_id=self.platform_org.id,
            execution_node_id=self.gateway.id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
            workspace_root="/workspace/platform/data",
            relative_path=f"tenants/{self.tenant.id}/knowledge-sources/workspace",
            state=LensWorkspaceBinding.State.READY,
            identity_status=LensWorkspaceBinding.IdentityStatus.READY,
        )
        self.session = LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            sl_session_uuid=uuid.uuid4(),
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )

    def test_ready_chat_releases_snapshot_usage_lease(self):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-ready-snapshot",
            idempotency_key="chat-ready-snapshot",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        claim_token = uuid.uuid4()
        assistant_uuid = self.knowledge_source.sl_assistant_uuid
        session_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.backup_source_snapshot_id = snapshot.id
        self.session.provision_claim_token = claim_token
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "backup_source_snapshot_id",
                "provision_claim_token",
                "updated_at",
            ]
        )
        acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
            consumer_id=self.session.id,
        )

        chat_lifecycle._complete_copilot_chat_provision(
            link_id=self.session.id,
            claim_token=str(claim_token),
            knowledge_source_id=self.knowledge_source.id,
            assistant_uuid=assistant_uuid,
            session_uuid=session_uuid,
        )

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.READY,
        )
        self.assertFalse(
            SnapshotUsageLease.objects.filter(snapshot_id=snapshot.id).exists()
        )

    def test_snapshot_scope_identity_keeps_drive_and_network_share(self):
        self.assertNotEqual(
            chat_data_update._scope_drive_and_parts("C:\\data\\reports")[0],
            chat_data_update._scope_drive_and_parts("D:\\data\\reports")[0],
        )
        self.assertNotEqual(
            chat_data_update._scope_drive_and_parts("//host/share-a/reports")[0],
            chat_data_update._scope_drive_and_parts("//host/share-b/reports")[0],
        )
        self.assertEqual(
            chat_data_update._scope_drive_and_parts("//host/share-a/reports")[1],
            ["reports"],
        )

    def test_chat_data_update_rebinds_directory_in_same_source_snapshot(self):
        self.gateway.metadata = {
            "inventory": {"capabilities": ["chat_workspace_reconcile_v1"]}
        }
        self.gateway.save(update_fields=["metadata", "updated_at"])
        old = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-old",
            idempotency_key="chat-update-old",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        new = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-new",
            idempotency_key="chat-update-new",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        directory = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.tenant.id,
            source_snapshot=new,
            backup_config_id=1,
            backup_config_dir_id=1,
            source_path="/data",
            repository_id=1,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        self.knowledge_source.backup_source_snapshot_id = old.id
        self.knowledge_source.linked_version_mode = (
            LensKnowledgeSource.LinkedVersionMode.PINNED
        )
        self.knowledge_source.sl_datasource_uuid = uuid.uuid4()
        self.knowledge_source.ingest_policy_json = {"document": True}
        self.knowledge_source.source_scopes_json = [
            {"source_path": "/data/reports", "backup_snapshot_directory_id": 9876}
        ]
        self.knowledge_source.save()
        self.session.backup_config_id = 1
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.READY
        self.session.save()

        snapshot, scopes = chat_data_update.resolve_update_scopes(
            chat=self.session, snapshot_id=new.id
        )
        self.assertEqual(snapshot.id, new.id)
        self.assertEqual(scopes[0].snapshot_directory_id, directory.id)
        self.assertEqual(scopes[0].selected_path, "reports")
        self.assertNotEqual(scopes[0].snapshot_directory_id, 9876)

        initial = chat_data_update.prepare_chat_data_update(
            chat=self.session, snapshot_id=new.id
        )
        duplicate = chat_data_update.prepare_chat_data_update(
            chat=self.session, snapshot_id=new.id
        )
        self.assertEqual(initial, duplicate)
        self.assertEqual(initial["scopes"][0]["snapshot_directory_id"], directory.id)
        self.assertTrue(
            SnapshotUsageLease.objects.filter(
                snapshot_id=new.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
                consumer_id=str(self.knowledge_source.id),
            ).exists()
        )
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status, LensSessionLink.LifecycleStatus.READY
        )

        competing_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-competing",
            idempotency_key="chat-update-competing",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.tenant.id,
            source_snapshot=competing_snapshot,
            backup_config_id=1,
            backup_config_dir_id=1,
            source_path="/data",
            repository_id=1,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        with self.assertRaisesMessage(ValidationError, "Another Chat data update"):
            chat_data_update.prepare_chat_data_update(
                chat=self.session, snapshot_id=competing_snapshot.id
            )
        self.assertFalse(
            SnapshotUsageLease.objects.filter(
                snapshot_id=competing_snapshot.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
                consumer_id=str(self.knowledge_source.id),
            ).exists()
        )

        BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.tenant.id,
            source_snapshot=new,
            backup_config_id=1,
            backup_config_dir_id=2,
            source_path="/DATA",
            repository_id=1,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.tenant.id,
            source_snapshot=new,
            backup_config_id=1,
            backup_config_dir_id=3,
            source_path="/DATA/reports",
            repository_id=1,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        exact_snapshot, exact_scopes = chat_data_update.resolve_update_scopes(
            chat=self.session, snapshot_id=new.id
        )
        self.assertEqual(exact_snapshot.id, new.id)
        self.assertEqual(exact_scopes[0].snapshot_directory_id, directory.id)
        self.knowledge_source.source_scopes_json[0]["source_path"] = "/DaTa/reports"
        self.knowledge_source.save(update_fields=["source_scopes_json", "updated_at"])
        self.session.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "ambiguous"):
            chat_data_update.resolve_update_scopes(
                chat=self.session, snapshot_id=new.id
            )

        self.knowledge_source.source_scopes_json[0]["source_path"] = "C:\\data\\reports"
        self.knowledge_source.save(update_fields=["source_scopes_json", "updated_at"])
        self.session.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "absent"):
            chat_data_update.resolve_update_scopes(
                chat=self.session, snapshot_id=new.id
            )

        new.source_ref_id += 1
        new.save(update_fields=["source_ref_id"])
        with self.assertRaises(ValidationError):
            chat_data_update.resolve_update_scopes(
                chat=self.session, snapshot_id=new.id
            )

    def test_chat_update_rejects_a_changed_workspace_target_before_restore(self):
        workspace = self.workspace_binding.resolved_path()
        previous = RestoreRecord.objects.create(
            organization_id=self.tenant.id,
            requesting_organization_id=self.tenant.id,
            target_execution_organization_id=self.platform_org.id,
            target_execution_node_id=self.gateway.id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key="previous-chat-layout",
            workspace_binding_id=self.workspace_binding.id,
            restore_uid="previous-chat-layout",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=1,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=self.source_agent.id,
            source_snapshot_id=11,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=self.gateway.id,
            target_path=workspace,
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )
        RestoreRecordItem.objects.create(
            organization_id=self.tenant.id,
            restore_record=previous,
            source_snapshot_directory_id=12,
            backup_config_dir_id=1,
            repository_id=1,
            kopia_snapshot_id="old-snapshot",
            source_path="/data",
            target_path=f"{workspace}/reports",
            conflict_mode=RestoreRecordItem.ConflictMode.OVERWRITE,
            status=RestoreRecordItem.Status.SUCCESS,
        )
        common = {
            "source_snapshot_directory_id": 21,
            "selected_paths": [],
            "source_path_type": "dir",
            "restore_dir": workspace,
            "conflict_mode": "overwrite",
        }
        knowledge_source_sync._require_unchanged_chat_workspace_layout(
            org=self.tenant,
            ks=self.knowledge_source,
            update={"applied_snapshot_id": 11},
            restore_dir=workspace,
            candidate_items=[{**common, "target_source_path": "/data/reports"}],
        )
        with self.assertRaisesMessage(
            knowledge_source_sync.KnowledgeSourceSyncError,
            "would change this Chat's workspace layout",
        ):
            knowledge_source_sync._require_unchanged_chat_workspace_layout(
                org=self.tenant,
                ks=self.knowledge_source,
                update={"applied_snapshot_id": 11},
                restore_dir=workspace,
                candidate_items=[{**common, "target_source_path": "/data/renamed"}],
            )

    def test_failed_chat_data_update_keeps_target_snapshot_for_retry(self):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-retry-target",
            idempotency_key="chat-update-retry-target",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "failed",
                "target_snapshot_id": snapshot.id,
            }
        }
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])
        lease = acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
            consumer_id=self.knowledge_source.id,
        )

        reconcile_snapshot_usage_leases()

        self.assertTrue(SnapshotUsageLease.objects.filter(pk=lease.id).exists())
        state = dict(self.knowledge_source.sync_state_json)
        state["chat_data_update"]["status"] = "complete"
        self.knowledge_source.sync_state_json = state
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])
        reconcile_snapshot_usage_leases()
        self.assertFalse(SnapshotUsageLease.objects.filter(pk=lease.id).exists())

    def test_deleted_chat_workspace_releases_failed_update_snapshot_lease(self):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-deleted-lease",
            idempotency_key="chat-update-deleted-lease",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.knowledge_source.lifecycle_status = LensKnowledgeSource.LifecycleStatus.DELETED
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "failed",
                "target_snapshot_id": snapshot.id,
            }
        }
        self.knowledge_source.save(
            update_fields=["lifecycle_status", "sync_state_json", "updated_at"]
        )
        lease = acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
            consumer_id=self.knowledge_source.id,
        )

        reconcile_snapshot_usage_leases()

        self.assertFalse(SnapshotUsageLease.objects.filter(pk=lease.pk).exists())

    @mock.patch(
        "apps.lens_bridge.tasks.chat_data_update.execute_chat_data_update_task.delay"
    )
    def test_pending_chat_data_update_is_requeued_after_dispatch_loss(self, delay):
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {"status": "pending", "target_snapshot_id": 27}
        }
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])

        result = reconcile_chat_data_updates_task(limit=10)

        self.assertEqual(result["queued"], 1)
        delay.assert_called_once_with(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )

    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown.assess_chat_restore_stop",
        return_value=mock.Mock(confirmed=True),
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.managed_datasource.conversion_stop_confirmed",
        return_value=True,
    )
    def test_abandon_failed_update_preserves_chat_but_releases_target(
        self, _conversion_stop, _restore_stop
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-abandon-target",
            idempotency_key="chat-update-abandon-target",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.knowledge_source.status = LensKnowledgeSource.Status.DEGRADED
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "failed",
                "phase": "restore",
                "target_snapshot_id": snapshot.id,
            }
        }
        self.knowledge_source.save(update_fields=["status", "sync_state_json", "updated_at"])
        lease = acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
            consumer_id=self.knowledge_source.id,
        )

        result = chat_data_update.abandon_chat_data_update(chat=self.session)

        self.assertEqual(result["status"], "abandoned")
        self.assertIs(_restore_stop.call_args.kwargs["request_cancel"], False)
        self.session.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.READY)
        self.assertFalse(SnapshotUsageLease.objects.filter(pk=lease.pk).exists())

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.resolve_update_scopes"
    )
    def test_new_update_after_abandon_restores_original_health_baseline(
        self, resolve_scopes
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-after-abandon",
            idempotency_key="chat-update-after-abandon",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        resolve_scopes.return_value = (snapshot, ())
        self.knowledge_source.status = LensKnowledgeSource.Status.DEGRADED
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "abandoned",
                "previous_status": LensKnowledgeSource.Status.READY,
                "target_snapshot_id": 77,
            }
        }
        self.knowledge_source.save(update_fields=["status", "sync_state_json", "updated_at"])

        update = chat_data_update.prepare_chat_data_update(
            chat=self.session, snapshot_id=snapshot.id
        )

        self.assertEqual(update["previous_status"], LensKnowledgeSource.Status.READY)

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.resolve_update_scopes"
    )
    def test_completed_update_replay_does_not_retain_another_snapshot_lease(
        self, resolve_scopes
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-completed-replay",
            idempotency_key="chat-update-completed-replay",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        resolve_scopes.return_value = (snapshot, ())
        self.knowledge_source.pinned_snapshot_id = snapshot.id
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "complete",
                "target_snapshot_id": snapshot.id,
            }
        }
        self.knowledge_source.save(
            update_fields=["pinned_snapshot_id", "sync_state_json", "updated_at"]
        )

        replay = chat_data_update.prepare_chat_data_update(
            chat=self.session, snapshot_id=snapshot.id
        )

        self.assertEqual(replay["status"], "complete")
        self.assertFalse(
            SnapshotUsageLease.objects.filter(
                snapshot_id=snapshot.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
                consumer_id=str(self.knowledge_source.id),
            ).exists()
        )

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.managed_datasource.convert_documents"
    )
    def test_chat_data_conversion_completes_without_replacing_chat_session(
        self, convert_documents
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-conversion-target",
            idempotency_key="chat-update-conversion-target",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        original_session_uuid = self.session.sl_session_uuid
        original_assistant_uuid = self.session.sl_assistant_uuid
        self.workspace_binding.capacity_accounted_bytes = 1100
        self.workspace_binding.capacity_accounting_status = (
            LensWorkspaceBinding.CapacityAccountingStatus.CONSERVATIVE
        )
        self.workspace_binding.save(
            update_fields=[
                "capacity_accounted_bytes", "capacity_accounting_status", "updated_at",
            ]
        )
        self.knowledge_source.pinned_snapshot_id = 17
        self.knowledge_source.ingest_policy_json = {"document": True}
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "pending",
                "phase": "convert",
                "target_snapshot_id": snapshot.id,
                "applied_snapshot_id": 17,
                "validated_bytes": 900,
                "scopes": [
                    {
                        "source_path": "/data",
                        "snapshot_directory_id": 77,
                        "snapshot_directory_source_path": "/data",
                        "snapshot_directory_path_type": "dir",
                        "selected_path": "",
                        "size_bytes": 900,
                        "file_count": 3,
                        "path_type": "dir",
                    }
                ],
            },
        }
        self.knowledge_source.save(
            update_fields=[
                "pinned_snapshot_id", "ingest_policy_json", "sync_state_json", "updated_at"
            ]
        )
        lease = acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
            consumer_id=self.knowledge_source.id,
        )
        convert_documents.return_value = {"success": 2, "skipped": 8, "failed": 0}

        result = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.assertEqual(result["status"], "complete")
        self.knowledge_source.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.knowledge_source.pinned_snapshot_id, snapshot.id)
        self.assertEqual(self.knowledge_source.backup_source_snapshot_id, snapshot.id)
        self.assertEqual(self.knowledge_source.backup_snapshot_directory_id, 77)
        self.assertEqual(
            self.knowledge_source.source_scopes_json[0]["backup_snapshot_directory_id"],
            77,
        )
        self.assertEqual(self.knowledge_source.source_scopes_json[0]["size_bytes"], 900)
        self.workspace_binding.refresh_from_db()
        self.assertEqual(self.workspace_binding.capacity_accounted_bytes, 900)
        self.assertEqual(
            self.workspace_binding.capacity_accounting_status,
            LensWorkspaceBinding.CapacityAccountingStatus.EXACT,
        )
        self.assertEqual(
            self.knowledge_source.sync_state_json["chat_data_update"]["status"],
            "complete",
        )
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.READY)
        self.assertEqual(self.session.sl_session_uuid, original_session_uuid)
        self.assertEqual(self.session.sl_assistant_uuid, original_assistant_uuid)
        self.assertFalse(SnapshotUsageLease.objects.filter(pk=lease.pk).exists())
        self.assertFalse(convert_documents.call_args.kwargs["force"])
        self.assertEqual(
            LensSessionLinkSerializer(self.session).data["data_context"][
                "backup_source_snapshot_id"
            ],
            snapshot.id,
        )

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.managed_datasource.convert_documents"
    )
    def test_chat_data_update_does_not_claim_success_when_conversion_is_disabled(
        self, convert_documents
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-without-conversion",
            idempotency_key="chat-update-without-conversion",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.knowledge_source.ingest_policy_json = {"document": False}
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "pending",
                "phase": "convert",
                "target_snapshot_id": snapshot.id,
            }
        }
        self.knowledge_source.save(
            update_fields=["ingest_policy_json", "sync_state_json", "updated_at"]
        )

        result = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.assertEqual(result["status"], "failed")
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.sync_state_json["chat_data_update"]["status"],
            "failed",
        )
        self.assertNotEqual(self.knowledge_source.pinned_snapshot_id, snapshot.id)
        convert_documents.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.managed_datasource.convert_documents",
        return_value={"failed": 0, "skipped": 10},
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.knowledge_source_sync._run_phase_restore_snapshot"
    )
    def test_chat_data_update_resumes_restore_then_converts_without_disabling_chat(
        self, restore, convert_documents
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-resume-target",
            idempotency_key="chat-update-resume-target",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.knowledge_source.ingest_policy_json = {"document": True}
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "pending",
                "phase": "restore",
                "target_snapshot_id": snapshot.id,
                "new_request": True,
                "generation": 1,
                "scopes": [],
            }
        }
        self.knowledge_source.save(
            update_fields=["ingest_policy_json", "sync_state_json", "updated_at"]
        )
        acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
            consumer_id=self.knowledge_source.id,
        )
        restore.side_effect = [
            knowledge_source_sync.KnowledgeSourceSyncPending("Restore in progress."),
            None,
        ]

        first = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )
        self.assertEqual(first["status"], "waiting")
        self.knowledge_source.sync_next_poll_at = timezone.now() - timedelta(seconds=1)
        self.knowledge_source.save(update_fields=["sync_next_poll_at", "updated_at"])
        second = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )
        self.assertEqual(second["status"], "waiting")
        self.knowledge_source.sync_next_poll_at = timezone.now() - timedelta(seconds=1)
        self.knowledge_source.save(update_fields=["sync_next_poll_at", "updated_at"])
        third = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )
        self.assertEqual(third["status"], "complete")
        self.session.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.READY)
        self.assertEqual(restore.call_count, 2)
        convert_documents.assert_called_once()

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.knowledge_source_sync._run_phase_restore_snapshot",
        side_effect=knowledge_source_sync.KnowledgeSourceSyncError(
            "Data Gateway workspace has insufficient free space for this snapshot"
        ),
    )
    def test_chat_update_reports_disk_full_without_disabling_chat(self, _restore):
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "pending",
                "phase": "restore",
                "new_request": True,
                "target_snapshot_id": 44,
                "generation": 1,
            }
        }
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])

        result = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("Free space and retry", result["error"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.READY)

    @mock.patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.dispatch_scope_resolution"
    )
    @mock.patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.scope_task_for_correlation",
        return_value=None,
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.knowledge_source_sync._run_phase_restore_snapshot"
    )
    def test_chat_update_checks_target_scope_before_restoring(
        self, restore, _existing_task, dispatch
    ):
        dispatch.return_value = mock.Mock(
            id=uuid.uuid4(),
            status=NodeTask.Status.SUCCESS,
            result={
                "path_type": "dir",
                "size_bytes": 100,
                "file_count": 2,
                "skipped_special_count": 0,
            },
        )
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "pending",
                "phase": "validate",
                "generation": 1,
                "target_snapshot_id": 44,
                "requested_by_user_id": self.user.id,
                "scopes": [{
                    "snapshot_directory_id": 22,
                    "snapshot_directory_source_path": "/data",
                    "snapshot_directory_path_type": "dir",
                    "selected_path": "reports",
                    "path_type": "dir",
                }],
            }
        }
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])

        result = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.assertEqual(result["status"], "waiting")
        restore.assert_not_called()
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.sync_state_json["chat_data_update"]["validation_index"],
            1,
        )

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.knowledge_source_sync._require_unchanged_chat_workspace_layout"
    )
    @mock.patch(
        "apps.lens_bridge.services.public_gateway_capacity.assert_public_gateway_capacity"
    )
    def test_chat_update_reserves_only_projected_growth_before_restore(
        self, assert_capacity, _layout
    ):
        token = uuid.uuid4()
        self.workspace_binding.capacity_accounted_bytes = 400
        self.workspace_binding.capacity_accounting_status = (
            LensWorkspaceBinding.CapacityAccountingStatus.EXACT
        )
        self.workspace_binding.save(
            update_fields=[
                "capacity_accounted_bytes", "capacity_accounting_status", "updated_at",
            ]
        )
        self.knowledge_source.sync_claim_token = token
        self.knowledge_source.sync_claimed_at = timezone.now()
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "running",
                "phase": "validate",
                "target_snapshot_id": 47,
                "validation_index": 1,
                "validated_bytes": 1000,
                "validated_files": 2,
                "scopes": [{
                    "source_path": "/data",
                    "snapshot_directory_id": 17,
                    "snapshot_directory_source_path": "/data",
                    "snapshot_directory_path_type": "dir",
                    "selected_path": "",
                    "size_bytes": 1000,
                    "file_count": 2,
                    "path_type": "dir",
                }],
            }
        }
        self.knowledge_source.save(
            update_fields=[
                "sync_claim_token", "sync_claimed_at", "sync_state_json", "updated_at",
            ]
        )

        chat_data_update._reserve_capacity_and_start_restore(
            ks=self.knowledge_source, token=str(token)
        )

        self.workspace_binding.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        self.assertEqual(self.workspace_binding.capacity_accounted_bytes, 1000)
        self.assertEqual(
            self.workspace_binding.capacity_accounting_status,
            LensWorkspaceBinding.CapacityAccountingStatus.CONSERVATIVE,
        )
        self.assertEqual(
            self.knowledge_source.sync_state_json["chat_data_update"]["phase"],
            "restore",
        )
        self.assertIsNone(self.knowledge_source.sync_claim_token)
        self.assertEqual(
            assert_capacity.call_args.kwargs["additional_bytes"], 600
        )
        from apps.lens_bridge.services.public_gateway_capacity import (
            public_gateway_used_bytes,
        )

        used_bytes, unknown = public_gateway_used_bytes(
            gateway_link_id=self.gateway_link.id
        )
        self.assertGreaterEqual(used_bytes, 1000)
        self.assertFalse(unknown)

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.knowledge_source_sync._require_unchanged_chat_workspace_layout"
    )
    @mock.patch(
        "apps.lens_bridge.services.public_gateway_capacity.assert_public_gateway_capacity",
        side_effect=AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            diagnostic="gateway full",
        ),
    )
    def test_insufficient_gateway_capacity_does_not_start_restore(
        self, _assert_capacity, _layout
    ):
        token = uuid.uuid4()
        self.workspace_binding.capacity_accounted_bytes = 400
        self.workspace_binding.capacity_accounting_status = (
            LensWorkspaceBinding.CapacityAccountingStatus.EXACT
        )
        self.workspace_binding.save(
            update_fields=["capacity_accounted_bytes", "capacity_accounting_status", "updated_at"]
        )
        self.knowledge_source.sync_claim_token = token
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "running",
                "phase": "validate",
                "validation_index": 1,
                "validated_bytes": 1000,
                "validated_files": 2,
                "scopes": [{
                    "source_path": "/data",
                    "snapshot_directory_id": 17,
                    "snapshot_directory_source_path": "/data",
                    "snapshot_directory_path_type": "dir",
                    "selected_path": "",
                    "size_bytes": 1000,
                    "file_count": 2,
                }],
            }
        }
        self.knowledge_source.save(
            update_fields=["sync_claim_token", "sync_state_json", "updated_at"]
        )

        with self.assertRaises(AppError):
            chat_data_update._reserve_capacity_and_start_restore(
                ks=self.knowledge_source, token=str(token)
            )

        self.workspace_binding.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        self.assertEqual(self.workspace_binding.capacity_accounted_bytes, 400)
        self.assertEqual(
            self.knowledge_source.sync_state_json["chat_data_update"]["phase"],
            "validate",
        )
        result = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("capacity", result["error"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.READY)

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.managed_datasource.convert_documents",
        side_effect=RuntimeError("conversion temporarily failed"),
    )
    def test_failed_chat_data_conversion_keeps_chat_ready_and_retryable(
        self, _convert_documents
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-failed-conversion",
            idempotency_key="chat-update-failed-conversion",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.knowledge_source.ingest_policy_json = {"document": True}
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "pending",
                "phase": "convert",
                "target_snapshot_id": snapshot.id,
            }
        }
        self.knowledge_source.save(
            update_fields=["ingest_policy_json", "sync_state_json", "updated_at"]
        )
        lease = acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
            consumer_id=self.knowledge_source.id,
        )

        result = chat_data_update.run_chat_data_update(
            organization_id=self.tenant.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.assertEqual(result["status"], "failed")
        self.knowledge_source.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.sync_state_json["chat_data_update"]["status"],
            "failed",
        )
        self.assertEqual(
            self.knowledge_source.status, LensKnowledgeSource.Status.DEGRADED
        )
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.READY)
        self.assertTrue(SnapshotUsageLease.objects.filter(pk=lease.pk).exists())

    @mock.patch(
        "apps.lens_bridge.services.chat_data_update.managed_datasource.convert_documents",
        return_value={"failed": 0, "success": 1, "skipped": 9},
    )
    def test_partial_conversion_retries_non_forced_task_without_restoring_again(
        self, convert_documents
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-update-partial-retry",
            idempotency_key="chat-update-partial-retry",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.knowledge_source.status = LensKnowledgeSource.Status.DEGRADED
        self.knowledge_source.ingest_policy_json = {"document": True}
        self.knowledge_source.sync_state_json = {
            "chat_data_update": {
                "status": "failed",
                "phase": "convert",
                "target_snapshot_id": snapshot.id,
            },
            "conversion": {"status": "SUCCESS", "summary": {"failed": 1}},
        }
        self.knowledge_source.save(
            update_fields=["status", "ingest_policy_json", "sync_state_json", "updated_at"]
        )
        with (
            mock.patch(
                "apps.lens_bridge.services.chat_data_update.resolve_update_scopes",
                return_value=(snapshot, ()),
            ),
            mock.patch(
                "apps.lens_bridge.services.chat_data_update.knowledge_source_sync._run_phase_restore_snapshot"
            ) as restore,
        ):
            chat_data_update.prepare_chat_data_update(
                chat=self.session, snapshot_id=snapshot.id
            )
            result = chat_data_update.run_chat_data_update(
                organization_id=self.tenant.id,
                knowledge_source_id=self.knowledge_source.id,
            )
        self.assertEqual(result["status"], "complete")
        restore.assert_not_called()
        self.assertNotIn("conversion", convert_documents.call_args.kwargs["sync_state"])
        self.assertIs(convert_documents.call_args.kwargs["force"], False)

    def test_reused_chat_does_not_mark_degraded_shared_data_ready(self):
        token = uuid.uuid4()
        self.knowledge_source.status = LensKnowledgeSource.Status.DEGRADED
        self.knowledge_source.status_detail = "Some files are unavailable."
        self.knowledge_source.save(update_fields=["status", "status_detail"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = token
        self.session.save(
            update_fields=["lifecycle_status", "provision_claim_token", "updated_at"]
        )

        chat_lifecycle._complete_copilot_chat_provision(
            link_id=self.session.id,
            claim_token=str(token),
            knowledge_source_id=self.knowledge_source.id,
            assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            session_uuid=self.session.sl_session_uuid,
            reuse_prepared_resources=True,
        )

        self.knowledge_source.refresh_from_db()
        self.assertEqual(self.knowledge_source.status, LensKnowledgeSource.Status.DEGRADED)
        self.assertEqual(
            self.knowledge_source.status_detail, "Some files are unavailable."
        )

    def test_shared_resource_cleanup_waits_for_another_ready_chat(self):
        other = LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            sl_session_uuid=uuid.uuid4(),
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )

        claimed = chat_lifecycle._claim_shared_chat_resource_teardown(
            knowledge_source=self.knowledge_source,
            owner_session_link_id=self.session.id,
        )

        self.assertFalse(claimed)
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.lifecycle_status,
            LensKnowledgeSource.LifecycleStatus.READY,
        )
        other.refresh_from_db()
        self.assertEqual(other.lifecycle_status, LensSessionLink.LifecycleStatus.READY)

    def test_shared_resource_cleanup_claims_after_other_chat_is_deleted(self):
        LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            sl_session_uuid=None,
            sl_assistant_uuid=None,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETED,
        )

        claimed = chat_lifecycle._claim_shared_chat_resource_teardown(
            knowledge_source=self.knowledge_source,
            owner_session_link_id=self.session.id,
        )

        self.assertTrue(claimed)
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.lifecycle_status,
            LensKnowledgeSource.LifecycleStatus.DELETING,
        )
        self.assertEqual(
            self.knowledge_source.teardown_state_json[
                "chat_cleanup_owner_session_id"
            ],
            self.session.id,
        )

    def test_legacy_deleting_knowledge_source_can_resume_with_sole_chat(self):
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.save(
            update_fields=["lifecycle_status", "updated_at"]
        )

        claimed = chat_lifecycle._claim_shared_chat_resource_teardown(
            knowledge_source=self.knowledge_source,
            owner_session_link_id=self.session.id,
        )

        self.assertTrue(claimed)
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.teardown_state_json["chat_cleanup_owner_session_id"],
            self.session.id,
        )

    def test_shared_deleting_resource_without_owner_does_not_guess(self):
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {"shared_chat_resources": True}
        self.knowledge_source.save(
            update_fields=[
                "lifecycle_status", "teardown_state_json", "updated_at",
            ]
        )

        with self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError):
            chat_lifecycle._claim_shared_chat_resource_teardown(
                knowledge_source=self.knowledge_source,
                owner_session_link_id=self.session.id,
            )

    def test_simultaneous_session_deletions_elect_one_cleanup_owner(self):
        other = LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            sl_session_uuid=None,
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
            cleanup_status=LensSessionLink.CleanupStatus.RUNNING,
            teardown_claim_token=uuid.uuid4(),
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_status = LensSessionLink.CleanupStatus.RUNNING
        self.session.sl_session_uuid = None
        self.session.teardown_claim_token = uuid.uuid4()
        self.session.save(
            update_fields=[
                "lifecycle_status", "cleanup_status", "sl_session_uuid",
                "teardown_claim_token", "updated_at",
            ]
        )

        first = chat_lifecycle._claim_shared_chat_resource_teardown(
            knowledge_source=self.knowledge_source,
            owner_session_link_id=self.session.id,
            claim_token=str(self.session.teardown_claim_token),
        )
        self.session.refresh_from_db()
        second = chat_lifecycle._claim_shared_chat_resource_teardown(
            knowledge_source=self.knowledge_source,
            owner_session_link_id=other.id,
            claim_token=str(other.teardown_claim_token),
        )

        self.assertFalse(first)
        self.assertIsNone(self.session.knowledge_source_id)
        self.assertIsNone(self.session.sl_assistant_uuid)
        self.assertTrue(second)
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.teardown_state_json["chat_cleanup_owner_session_id"],
            other.id,
        )

    @mock.patch(
        "apps.lens_bridge.services.copilot_sharing.revoke_session_shares",
        return_value=0,
    )
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown.run_knowledge_source_teardown"
    )
    def test_session_only_cleanup_retry_never_retires_the_shared_assistant(
        self, teardown_ks, delete_assistant, _revoke_shares
    ):
        assistant_uuid = self.session.sl_assistant_uuid
        sibling = LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            sl_session_uuid=uuid.uuid4(),
            sl_assistant_uuid=assistant_uuid,
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )
        token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.RUNNING
        self.session.sl_session_uuid = None
        self.session.teardown_claim_token = token
        self.session.provision_state_json = {
            "assistant_create": {
                "status": "bound",
                "remote_uuid": str(assistant_uuid),
            }
        }
        self.session.save(
            update_fields=[
                "lifecycle_status", "cleanup_intent", "cleanup_status",
                "sl_session_uuid", "teardown_claim_token",
                "provision_state_json", "updated_at",
            ]
        )

        # Simulate a worker crash immediately after the durable detach.
        self.assertFalse(
            chat_lifecycle._claim_shared_chat_resource_teardown(
                knowledge_source=self.knowledge_source,
                owner_session_link_id=self.session.id,
                claim_token=str(token),
            )
        )
        self.session.refresh_from_db()
        self.assertIsNone(self.session.knowledge_source_id)
        self.assertEqual(
            self.session.teardown_state_json["shared_session_only"]["assistant_uuid"],
            str(assistant_uuid),
        )
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.teardown_claim_token = None
        self.session.teardown_next_retry_at = None
        self.session.save(
            update_fields=[
                "cleanup_status", "teardown_claim_token",
                "teardown_next_retry_at", "updated_at",
            ]
        )

        result = chat_lifecycle.run_copilot_chat_teardown(
            session_link_id=self.session.id
        )

        self.assertEqual(result["status"], "deleted")
        sibling.refresh_from_db()
        self.assertEqual(sibling.lifecycle_status, LensSessionLink.LifecycleStatus.READY)
        self.assertEqual(sibling.sl_assistant_uuid, assistant_uuid)
        delete_assistant.assert_not_called()
        teardown_ks.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.copilot_sharing.revoke_session_shares",
        return_value=0,
    )
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown.run_knowledge_source_teardown"
    )
    def test_session_only_retry_cleans_late_orphan_not_shared_assistant(
        self, teardown_ks, delete_assistant, _revoke_shares
    ):
        shared_uuid = self.session.sl_assistant_uuid
        orphan_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.sl_session_uuid = None
        self.session.sl_assistant_uuid = None
        self.session.knowledge_source = None
        self.session.teardown_state_json = {
            "shared_session_only": {
                "knowledge_source_id": self.knowledge_source.id,
                "assistant_uuid": str(shared_uuid),
            }
        }
        self.session.provision_state_json = {
            "assistant_create": {
                "status": "bound",
                "remote_uuid": str(shared_uuid),
            },
            "late_resources": [
                {"kind": "assistant", "remote_uuid": str(orphan_uuid)}
            ],
        }
        self.session.save()
        transient = sl_client.LensBridgeError("temporarily unavailable")
        transient.status_code = 503
        delete_assistant.side_effect = transient

        with self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError):
            chat_lifecycle.run_copilot_chat_teardown(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.assertEqual(
            chat_lifecycle._late_remote_uuids(self.session, "assistant"),
            {orphan_uuid},
        )
        self.assertEqual(self.session.sl_assistant_uuid, orphan_uuid)
        delete_assistant.assert_called_once_with(orphan_uuid)
        teardown_ks.assert_not_called()

        delete_assistant.reset_mock(side_effect=True)
        self.session.teardown_next_retry_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=["teardown_next_retry_at", "updated_at"])

        result = chat_lifecycle.run_copilot_chat_teardown(
            session_link_id=self.session.id
        )

        self.assertEqual(result["status"], "deleted")
        self.session.refresh_from_db()
        self.assertEqual(
            chat_lifecycle._late_remote_uuids(self.session, "assistant"),
            set(),
        )
        delete_assistant.assert_called_once_with(orphan_uuid)
        teardown_ks.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed"
    )
    def test_new_chat_reuses_prepared_resources_without_capacity_reservation(
        self,
        queue_provision,
    ):
        assistant_access.ensure_assistant_link(
            org=self.tenant,
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            knowledge_source=self.knowledge_source,
            created_by=self.user,
            owner_user=self.user,
            visibility_scope=LensAssistantLink.VisibilityScope.USER,
            lifecycle_owner=LensAssistantLink.LifecycleOwner.CHAT,
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.READY
        self.session.provision_phase = LensSessionLink.ProvisionPhase.READY
        self.session.sl_session_uuid = uuid.uuid4()
        self.session.backup_source_snapshot_id = 11
        self.session.source_scopes_json = [
            {"source_path": "/data", "backup_snapshot_directory_id": 12}
        ]
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_phase",
                "sl_session_uuid",
                "backup_source_snapshot_id",
                "source_scopes_json",
                "updated_at",
            ]
        )
        self.knowledge_source.backup_source_snapshot_id = 20
        self.knowledge_source.source_scopes_json = [
            {"source_path": "/data", "backup_snapshot_directory_id": 21}
        ]
        self.knowledge_source.save(
            update_fields=["backup_source_snapshot_id", "source_scopes_json", "updated_at"]
        )

        with self.captureOnCommitCallbacks(execute=True):
            reused = chat_lifecycle.create_copilot_chat_from_existing(
                self.tenant,
                user=self.user,
                source_session_id=self.session.id,
                idempotency_key="reuse-chat-test",
            )

        self.assertEqual(reused.knowledge_source_id, self.knowledge_source.id)
        self.assertEqual(reused.backup_source_snapshot_id, 20)
        self.assertEqual(reused.source_scopes_json[0]["backup_snapshot_directory_id"], 21)
        self.knowledge_source.refresh_from_db()
        self.assertTrue(
            self.knowledge_source.teardown_state_json["shared_chat_resources"]
        )
        self.assertEqual(
            reused.sl_assistant_uuid,
            self.knowledge_source.sl_assistant_uuid,
        )
        self.assertIsNone(reused.sl_session_uuid)
        self.assertEqual(
            reused.capacity_reservation_status,
            LensSessionLink.CapacityReservationStatus.RELEASED,
        )
        self.assertEqual(
            reused.provision_phase,
            LensSessionLink.ProvisionPhase.CREATING_SESSION,
        )
        reused.refresh_from_db()
        reused.knowledge_source.sync_state_json = {
            "restore_record_id": 123,
            "snapshot_id_used": 20,
            "conversion": {"status": "SUCCESS"},
        }
        with mock.patch(
            "apps.lens_bridge.services.preparation_progress.RestoreRecord.objects.filter"
        ) as restore_lookup:
            progress = LensSessionLinkSerializer().get_preparation_progress(reused)
        self.assertTrue(progress["reused_data"])
        self.assertIsNone(progress["restore"])
        self.assertEqual(progress["assistant_state"], "waiting")
        restore_lookup.assert_not_called()
        queue_provision.assert_called_once_with(reused.id)

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed"
    )
    def test_reused_chat_retry_does_not_reenter_restore_or_capacity_admission(
        self,
        queue_provision,
    ):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.provision_state_json = {
            "reuse_existing_resources": {"source_session_id": self.session.id}
        }
        self.session.capacity_reservation_status = (
            LensSessionLink.CapacityReservationStatus.RELEASED
        )
        self.session.save(
            update_fields=[
                "lifecycle_status", "provision_state_json",
                "capacity_reservation_status", "updated_at",
            ]
        )

        # Retry holds the Chat row lock; taking a KS lock in the opposite order
        # to deletion would deadlock if both requests overlap.
        with (
            mock.patch.object(
                LensKnowledgeSource.objects,
                "select_for_update",
                side_effect=AssertionError("Retry must not lock KS after Chat"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            retried = chat_lifecycle.retry_copilot_chat_provision(self.session)

        self.assertEqual(
            retried.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING
        )
        self.assertEqual(
            retried.provision_phase, LensSessionLink.ProvisionPhase.CREATING_SESSION
        )
        self.assertEqual(
            retried.capacity_reservation_status,
            LensSessionLink.CapacityReservationStatus.RELEASED,
        )
        self.assertFalse(LensGatewayChatSlot.objects.filter(session_link=retried).exists())
        queue_provision.assert_called_once_with(retried.id)

    def test_new_from_chat_rejects_non_chat_owned_assistant(self):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.READY
        self.session.save(update_fields=["lifecycle_status", "updated_at"])
        assistant_access.ensure_assistant_link(
            org=self.tenant,
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            knowledge_source=self.knowledge_source,
            created_by=self.user,
            visibility_scope=LensAssistantLink.VisibilityScope.ORGANIZATION,
            lifecycle_owner=LensAssistantLink.LifecycleOwner.MANUAL,
        )

        with self.assertRaisesMessage(
            ValidationError, "Chat resources are not owned by this user."
        ):
            chat_lifecycle.create_copilot_chat_from_existing(
                self.tenant,
                user=self.user,
                source_session_id=self.session.id,
                idempotency_key="non-chat-assistant",
            )

    def test_new_from_chat_rejects_revoked_gateway_link(self):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.READY
        self.session.save(update_fields=["lifecycle_status", "updated_at"])
        assistant_access.ensure_assistant_link(
            org=self.tenant,
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            knowledge_source=self.knowledge_source,
            created_by=self.user,
            owner_user=self.user,
            visibility_scope=LensAssistantLink.VisibilityScope.USER,
            lifecycle_owner=LensAssistantLink.LifecycleOwner.CHAT,
        )
        self.gateway_link.is_deleted = True
        self.gateway_link.save(update_fields=["is_deleted", "updated_at"])

        with self.assertRaisesMessage(
            ValidationError, "Chat Gateway is unavailable."
        ):
            chat_lifecycle.create_copilot_chat_from_existing(
                self.tenant,
                user=self.user,
                source_session_id=self.session.id,
                idempotency_key="gateway-revoked",
            )
        self.assertFalse(
            LensSessionLink.objects.filter(
                create_idempotency_key="gateway-revoked"
            ).exists()
        )

    def test_failed_chat_keeps_snapshot_while_cleanup_is_incomplete(self):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="chat-cleanup-snapshot",
            idempotency_key="chat-cleanup-snapshot",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.backup_source_snapshot_id = snapshot.id
        self.session.provision_next_retry_at = None
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_status",
                "backup_source_snapshot_id",
                "provision_next_retry_at",
                "updated_at",
            ]
        )
        acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
            consumer_id=self.session.id,
        )

        reconcile_snapshot_usage_leases()

        self.assertTrue(
            SnapshotUsageLease.objects.filter(
                snapshot_id=snapshot.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
                consumer_id=str(self.session.id),
            ).exists()
        )

    def test_failed_chat_keeps_snapshot_for_retained_workspace(self):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="retained-chat-snapshot",
            idempotency_key="retained-chat-snapshot",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.NONE
        self.session.cleanup_status = LensSessionLink.CleanupStatus.NONE
        self.session.backup_source_snapshot_id = snapshot.id
        self.session.save(
            update_fields=[
                "lifecycle_status", "cleanup_intent", "cleanup_status",
                "backup_source_snapshot_id", "updated_at",
            ]
        )
        acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
            consumer_id=self.session.id,
        )

        reconcile_snapshot_usage_leases()

        self.assertTrue(
            SnapshotUsageLease.objects.filter(
                snapshot_id=snapshot.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
                consumer_id=str(self.session.id),
            ).exists()
        )

    def test_legacy_deleting_chat_with_complete_cleanup_releases_lease(self):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="legacy-deleting-chat-snapshot",
            idempotency_key="legacy-deleting-chat-snapshot",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_status = LensSessionLink.CleanupStatus.COMPLETE
        self.session.backup_source_snapshot_id = snapshot.id
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_status",
                "backup_source_snapshot_id",
                "updated_at",
            ]
        )
        acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
            consumer_id=self.session.id,
        )

        reconcile_snapshot_usage_leases()

        self.assertFalse(
            SnapshotUsageLease.objects.filter(
                snapshot_id=snapshot.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
                consumer_id=str(self.session.id),
            ).exists()
        )

    def test_snapshot_usage_reconciler_rotates_past_retained_lease(self):
        retained_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="retained-reconcile-snapshot",
            idempotency_key="retained-reconcile-snapshot",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        releasable_snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="releasable-reconcile-snapshot",
            idempotency_key="releasable-reconcile-snapshot",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        retained = SnapshotUsageLease.objects.create(
            organization_id=self.tenant.id,
            snapshot_id=retained_snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
            consumer_id="unclassified-safe-retention",
        )
        releasable = SnapshotUsageLease.objects.create(
            organization_id=self.tenant.id,
            snapshot_id=releasable_snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
            consumer_id=str(self.session.id),
        )

        first = reconcile_snapshot_usage_leases(limit=1)
        retained.refresh_from_db()
        second = reconcile_snapshot_usage_leases(limit=1)

        self.assertEqual(first, {"checked": 1, "released": 0, "retained": 1})
        self.assertIsNotNone(retained.last_reconciled_at)
        self.assertEqual(second, {"checked": 1, "released": 1, "retained": 0})
        self.assertFalse(SnapshotUsageLease.objects.filter(pk=releasable.id).exists())

    @staticmethod
    def _not_found() -> sl_client.LensBridgeError:
        error = sl_client.LensBridgeError("not found")
        error.status_code = 404
        return error

    def test_assistant_binding_is_atomic_under_the_provision_claim(self):
        assistant_uuid = uuid.uuid4()
        claim_token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = claim_token
        self.session.provision_state_json = {
            "assistant_create": {
                "operation_id": str(uuid.uuid4()),
                "kind": "assistant_create",
                "lookup_key": "tenant-chat-ks",
                "remote_uuid": "",
                "status": "intent",
            }
        }
        self.session.sl_assistant_uuid = None
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "provision_state_json",
                "sl_assistant_uuid",
                "updated_at",
            ]
        )
        self.knowledge_source.sl_assistant_uuid = None
        self.knowledge_source.save(update_fields=["sl_assistant_uuid", "updated_at"])

        chat_lifecycle._bind_assistant_to_provision_claim(
            self.session,
            str(claim_token),
            knowledge_source=self.knowledge_source,
            assistant_uuid=assistant_uuid,
        )

        self.session.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        assistant_link = LensAssistantLink.objects.get(
            organization=self.tenant,
            sl_assistant_uuid=assistant_uuid,
        )
        self.assertEqual(self.session.sl_assistant_uuid, assistant_uuid)
        self.assertEqual(self.knowledge_source.sl_assistant_uuid, assistant_uuid)
        self.assertEqual(assistant_link.knowledge_source, self.knowledge_source)
        self.assertEqual(assistant_link.owner_user, self.user)
        self.assertEqual(
            self.session.provision_state_json["assistant_create"]["remote_uuid"],
            str(assistant_uuid),
        )

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    def test_lost_provision_claim_cannot_revive_assistant_tombstone(
        self,
        _queue_teardown,
    ):
        assistant_uuid = uuid.uuid4()
        claim_token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = claim_token
        self.session.sl_assistant_uuid = None
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "sl_assistant_uuid",
                "updated_at",
            ]
        )
        assistant_access.soft_delete_assistant_link(self.tenant, assistant_uuid)
        chat_lifecycle.request_copilot_chat_teardown(self.session)

        with self.assertRaises(chat_lifecycle.ChatProvisionLeaseLostError):
            chat_lifecycle._bind_assistant_to_provision_claim(
                self.session,
                str(claim_token),
                knowledge_source=self.knowledge_source,
                assistant_uuid=assistant_uuid,
            )

        tombstone = LensAssistantLink.all_objects.get(
            organization=self.tenant,
            sl_assistant_uuid=assistant_uuid,
        )
        self.assertTrue(tombstone.is_deleted)

    @mock.patch("apps.lens_bridge.services.assistant_access.soft_delete_assistant_link")
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_teardown_treats_missing_session_as_success_and_cleans_workspace(
        self,
        request_json,
        run_agent_task,
        _delete_assistant,
        _soft_delete_assistant,
    ):
        request_json.side_effect = self._not_found()
        run_agent_task.return_value = mock.MagicMock(
            ok=True,
            timed_out=False,
            task=mock.MagicMock(id=uuid.uuid4(), last_error=""),
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.save(update_fields=["lifecycle_status", "updated_at"])
        LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        result = chat_lifecycle.run_copilot_chat_teardown(
            session_link_id=self.session.id
        )

        self.assertEqual(result["status"], "deleted")
        self.session.refresh_from_db()
        self.workspace_binding.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETED,
        )
        self.assertIsNone(self.session.sl_session_uuid)
        self.assertIsNone(self.session.sl_assistant_uuid)
        self.assertIsNone(self.session.knowledge_source_id)
        self.assertEqual(
            self.workspace_binding.state,
            LensWorkspaceBinding.State.DELETED,
        )
        self.assertFalse(
            LensGatewayChatSlot.objects.filter(session_link=self.session).exists()
        )
        self.assertEqual(run_agent_task.call_args.kwargs["kind"], "lens.ks.cleanup")

    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    @mock.patch(
        "apps.lens_bridge.services.copilot_sharing.revoke_session_shares",
        side_effect=sl_client.LensBridgeUnavailable(),
    )
    def test_share_revocation_failure_blocks_session_and_workspace_deletion(
        self,
        revoke_shares,
        request_json,
    ):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.save(update_fields=["lifecycle_status", "updated_at"])

        with self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError):
            chat_lifecycle.run_copilot_chat_teardown(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.workspace_binding.refresh_from_db()
        revoke_shares.assert_called_once()
        request_json.assert_not_called()
        self.assertIsNotNone(self.session.sl_session_uuid)
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertEqual(
            self.session.teardown_state_json["revoke_shares"]["status"],
            "retry",
        )
        self.assertEqual(
            self.session.teardown_state_json["delete_session"]["status"],
            "blocked",
        )
        self.assertEqual(
            self.workspace_binding.state,
            LensWorkspaceBinding.State.READY,
        )

    @mock.patch("apps.lens_bridge.services.assistant_access.soft_delete_assistant_link")
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_failed_provision_cleanup_keeps_chat_retryable(
        self,
        request_json,
        run_agent_task,
        _delete_assistant,
        _soft_delete_assistant,
    ):
        request_json.side_effect = self._not_found()
        run_agent_task.return_value = mock.MagicMock(
            ok=True,
            timed_out=False,
            task=mock.MagicMock(id=uuid.uuid4(), last_error=""),
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.status = LensSessionLink.Status.ACTIVE
        self.session.teardown_state_json = {
            "intent": "reset_for_retry",
            "provision_error": "LensNode was unavailable",
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "status",
                "teardown_state_json",
                "updated_at",
            ]
        )
        LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        result = chat_lifecycle.run_copilot_chat_teardown(
            session_link_id=self.session.id
        )

        self.assertEqual(result["status"], "retryable")
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.FAILED,
        )
        self.assertEqual(self.session.status, LensSessionLink.Status.ACTIVE)
        self.assertIsNone(self.session.knowledge_source_id)
        self.assertIn("LensNode was unavailable", self.session.lifecycle_error)
        self.assertFalse(
            LensGatewayChatSlot.objects.filter(session_link=self.session).exists()
        )

    @mock.patch("apps.lens_bridge.services.assistant_access.soft_delete_assistant_link")
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_unconfirmed_conversion_stop_blocks_cleanup_without_deleting_workspace(
        self,
        request_json,
        _delete_assistant,
        _soft_delete_assistant,
    ):
        request_json.side_effect = self._not_found()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.RESET_FOR_RETRY
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.status = LensSessionLink.Status.ACTIVE
        self.session.teardown_state_json = {
            "intent": "reset_for_retry",
            "provision_error": "conversion failed",
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "status",
                "teardown_state_json",
                "updated_at",
            ]
        )

        def block_knowledge_source_cleanup(**_kwargs):
            LensKnowledgeSource.all_objects.filter(pk=self.knowledge_source.id).update(
                teardown_state_json={
                    "cancel_conversion": {"status": "waiting"},
                    "blocking": {
                        "reason": "conversion_stop_unconfirmed",
                        "task_id": "convert-1",
                        "gateway_link_id": self.gateway_link.id,
                        "remote_status": "CANCELLING",
                        "intervention_required": False,
                    },
                }
            )
            raise knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError(
                "Waiting for LensNode to stop document conversion."
            )

        with (
            mock.patch(
                "apps.lens_bridge.services.knowledge_source_teardown."
                "run_knowledge_source_teardown",
                side_effect=block_knowledge_source_cleanup,
            ),
            self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError),
        ):
            chat_lifecycle.run_copilot_chat_teardown(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.workspace_binding.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.FAILED,
        )
        self.assertEqual(
            self.session.cleanup_status,
            LensSessionLink.CleanupStatus.BLOCKED,
        )
        self.assertEqual(
            self.workspace_binding.state,
            LensWorkspaceBinding.State.READY,
        )
        self.assertEqual(
            self.session.teardown_state_json["blocking"]["reason"],
            "conversion_stop_unconfirmed",
        )
        self.assertEqual(
            self.session.teardown_state_json["blocking"]["task_id"],
            "convert-1",
        )

    @mock.patch("apps.lens_bridge.services.assistant_access.soft_delete_assistant_link")
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_ordinary_knowledge_source_failure_remains_retryable(
        self,
        request_json,
        _delete_assistant,
        _soft_delete_assistant,
    ):
        request_json.side_effect = self._not_found()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "updated_at",
            ]
        )
        LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        blocking = {
            "reason": "cleanup_workspace",
            "fingerprint": "stable-workspace-failure",
            "task_id": "",
            "gateway_link_id": self.gateway_link.id,
            "remote_status": "",
            "stop_confirmation_source": "",
            "first_seen_at": timezone.now().isoformat(),
            "last_seen_at": timezone.now().isoformat(),
            "consecutive_attempts": 12,
            "intervention_required": True,
        }

        def require_intervention(**_kwargs):
            LensKnowledgeSource.all_objects.filter(pk=self.knowledge_source.id).update(
                teardown_state_json={"blocking": blocking}
            )
            raise knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError(
                "Workspace cleanup requires operator intervention."
            )

        with (
            mock.patch(
                "apps.lens_bridge.services.knowledge_source_teardown."
                "run_knowledge_source_teardown",
                side_effect=require_intervention,
            ),
            self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError),
        ):
            chat_lifecycle.run_copilot_chat_teardown(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.cleanup_status,
            LensSessionLink.CleanupStatus.PENDING,
        )
        self.assertIsNotNone(self.session.teardown_next_retry_at)
        self.assertEqual(
            self.session.teardown_state_json["blocking"],
            blocking,
        )
        self.assertTrue(
            LensGatewayChatSlot.objects.filter(session_link=self.session).exists()
        )

    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=False,
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle.gateway_chat_queue.wake_gateway_queue"
    )
    def test_force_delete_private_chat_finishes_control_plane_delete(
        self,
        wake_gateway_queue,
        _agent_ws_routable,
    ):
        self.gateway_link.scope = LensGatewayLink.GatewayScope.ORGANIZATION
        self.gateway_link.save(update_fields=["scope", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.status = LensSessionLink.Status.ARCHIVED
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.sl_session_uuid = None
        self.session.sl_assistant_uuid = None
        self.session.teardown_state_json = {
            "intent": "delete_session",
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            },
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "status",
                "cleanup_intent",
                "cleanup_status",
                "sl_session_uuid",
                "sl_assistant_uuid",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.knowledge_source.sl_assistant_uuid = None
        self.knowledge_source.sl_datasource_uuid = None
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {
            "cancel_chat_restore": {"status": "success"},
            "cancel_conversion": {"status": "success"},
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            }
        }
        self.knowledge_source.save(
            update_fields=[
                "sl_assistant_uuid",
                "sl_datasource_uuid",
                "lifecycle_status",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.workspace_binding.state = LensWorkspaceBinding.State.DELETING
        self.workspace_binding.save(update_fields=["state", "updated_at"])
        LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = chat_lifecycle.force_delete_private_copilot_chat(
                self.session,
                requested_by=self.user,
            )

        result.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        self.workspace_binding.refresh_from_db()
        self.assertEqual(result.lifecycle_status, LensSessionLink.LifecycleStatus.DELETED)
        self.assertTrue(result.is_deleted)
        self.assertEqual(result.cleanup_status, LensSessionLink.CleanupStatus.COMPLETE)
        self.assertFalse(
            LensGatewayChatSlot.objects.filter(session_link=self.session).exists()
        )
        self.assertEqual(
            result.teardown_state_json["forced_remote_cleanup"]["status"],
            "skipped",
        )
        self.assertEqual(
            self.knowledge_source.teardown_state_json["forced_remote_cleanup"][
                "remote_resources"
            ]["workspace_uid"],
            str(self.workspace_binding.workspace_uid),
        )
        self.assertEqual(
            self.knowledge_source.lifecycle_status,
            LensKnowledgeSource.LifecycleStatus.DELETED,
        )
        self.assertTrue(self.knowledge_source.is_deleted)
        self.assertIsNone(result.knowledge_source_id)
        self.assertIsNone(result.sl_session_uuid)
        self.assertIsNone(result.sl_assistant_uuid)
        self.assertIsNone(self.knowledge_source.sl_assistant_uuid)
        self.assertIsNone(self.knowledge_source.sl_datasource_uuid)
        self.assertEqual(
            self.workspace_binding.state,
            LensWorkspaceBinding.State.DELETED,
        )
        self.assertTrue(self.workspace_binding.is_deleted)
        wake_gateway_queue.assert_called_once_with(self.gateway_link.id)

    def _prepare_shared_force_cleanup(self):
        self.gateway_link.scope = LensGatewayLink.GatewayScope.ORGANIZATION
        self.gateway_link.save(update_fields=["scope", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.status = LensSessionLink.Status.ARCHIVED
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.sl_session_uuid = None
        self.session.teardown_state_json = {
            "intent": "delete_session",
            "delete_session": {"status": "success"},
        }
        self.session.save(
            update_fields=[
                "lifecycle_status", "status", "cleanup_intent",
                "cleanup_status", "sl_session_uuid",
                "teardown_state_json", "updated_at",
            ]
        )
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {
            "shared_chat_resources": True,
            "chat_cleanup_owner_session_id": self.session.id,
        }
        self.knowledge_source.save(
            update_fields=["lifecycle_status", "teardown_state_json", "updated_at"]
        )
        self.workspace_binding.state = LensWorkspaceBinding.State.DELETING
        self.workspace_binding.save(update_fields=["state", "updated_at"])
        assistant_access.ensure_assistant_link(
            org=self.tenant,
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            knowledge_source=self.knowledge_source,
            created_by=self.user,
            owner_user=self.user,
            visibility_scope=LensAssistantLink.VisibilityScope.USER,
            lifecycle_owner=LensAssistantLink.LifecycleOwner.CHAT,
        )
        self.session.refresh_from_db()

    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=False,
    )
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_shared_force_cleanup_skips_remote_assistant_archive(
        self, request_json, _agent_ws_routable
    ):
        self._prepare_shared_force_cleanup()

        result = chat_lifecycle.force_delete_private_copilot_chat(
            self.session, requested_by=self.user
        )

        self.assertEqual(result.lifecycle_status, LensSessionLink.LifecycleStatus.DELETED)
        request_json.assert_not_called()

    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=False,
    )
    def test_shared_force_cleanup_requires_own_session_to_be_deleted(
        self, _agent_ws_routable
    ):
        self._prepare_shared_force_cleanup()
        self.session.sl_session_uuid = uuid.uuid4()
        self.session.save(update_fields=["sl_session_uuid", "updated_at"])
        self.session.refresh_from_db()

        self.assertEqual(
            chat_lifecycle.private_chat_force_delete_reason(self.session),
            "shared_session_cleanup_unconfirmed",
        )

    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=False,
    )
    def test_force_delete_offline_private_gateway_respects_live_claims(self, _agent_ws_routable):
        self.gateway_link.scope = LensGatewayLink.GatewayScope.ORGANIZATION
        self.gateway_link.save(update_fields=["scope", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.teardown_state_json = {
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            }
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {
            "cancel_chat_restore": {"status": "success"},
            "cancel_conversion": {"status": "success"},
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            },
        }
        self.knowledge_source.save(
            update_fields=[
                "lifecycle_status",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.workspace_binding.state = LensWorkspaceBinding.State.DELETING
        self.workspace_binding.save(update_fields=["state", "updated_at"])

        self.assertTrue(chat_lifecycle.can_force_delete_private_chat(self.session))
        for cleanup_status in (
            LensSessionLink.CleanupStatus.PENDING,
            LensSessionLink.CleanupStatus.RUNNING,
        ):
            self.session.cleanup_status = cleanup_status
            self.session.save(update_fields=["cleanup_status", "updated_at"])
            self.session.refresh_from_db()
            self.assertTrue(chat_lifecycle.can_force_delete_private_chat(self.session))
        self.knowledge_source.teardown_claim_token = uuid.uuid4()
        self.knowledge_source.teardown_claimed_at = timezone.now()
        self.knowledge_source.save(
            update_fields=[
                "teardown_claim_token",
                "teardown_claimed_at",
                "updated_at",
            ]
        )
        self.session.refresh_from_db()
        self.assertFalse(chat_lifecycle.can_force_delete_private_chat(self.session))

        self.knowledge_source.teardown_claimed_at = None
        self.knowledge_source.save(
            update_fields=["teardown_claimed_at", "updated_at"]
        )
        self.session.refresh_from_db()
        self.assertTrue(chat_lifecycle.can_force_delete_private_chat(self.session))

    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=True,
    )
    def test_online_private_force_cleanup_requires_executor_stop_evidence(
        self, _agent_ws_routable
    ):
        self.gateway_link.scope = LensGatewayLink.GatewayScope.ORGANIZATION
        self.gateway_link.save(update_fields=["scope", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.save(
            update_fields=["lifecycle_status", "cleanup_intent", "cleanup_status"]
        )
        self.knowledge_source.lifecycle_status = LensKnowledgeSource.LifecycleStatus.DELETING
        self.knowledge_source.save(update_fields=["lifecycle_status"])
        self.assertEqual(
            chat_lifecycle.private_chat_force_delete_reason(self.session),
            "restore_still_running",
        )
        self.knowledge_source.teardown_state_json = {
            "cancel_chat_restore": {"status": "success"},
            "cancel_conversion": {"status": "waiting"},
        }
        self.knowledge_source.save(update_fields=["teardown_state_json"])
        self.assertEqual(
            chat_lifecycle.private_chat_force_delete_reason(self.session),
            "conversion_still_running",
        )
        self.knowledge_source.teardown_state_json["cancel_conversion"]["status"] = "success"
        self.knowledge_source.save(update_fields=["teardown_state_json"])
        self.assertTrue(chat_lifecycle.can_force_delete_private_chat(self.session))
        result = chat_lifecycle.force_delete_private_copilot_chat(
            self.session, requested_by=self.user
        )
        self.assertEqual(result.lifecycle_status, LensSessionLink.LifecycleStatus.DELETED)
        self.assertEqual(
            result.teardown_state_json["forced_remote_cleanup"]["reason"],
            "force_cleanup",
        )

    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=True,
    )
    def test_online_force_cleanup_refuses_unconfirmed_remote_task(
        self, _agent_ws_routable
    ):
        self.gateway_link.scope = LensGatewayLink.GatewayScope.ORGANIZATION
        self.gateway_link.save(update_fields=["scope", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.save(
            update_fields=["lifecycle_status", "cleanup_intent", "cleanup_status"]
        )
        self.knowledge_source.lifecycle_status = LensKnowledgeSource.LifecycleStatus.DELETING
        self.knowledge_source.teardown_state_json = {
            "cancel_chat_restore": {"status": "success"},
            "cancel_conversion": {"status": "success"},
            "blocking": {"reason": "conversion_stop_unconfirmed"},
        }
        self.knowledge_source.save(
            update_fields=["lifecycle_status", "teardown_state_json"]
        )
        self.assertEqual(
            chat_lifecycle.private_chat_force_delete_reason(self.session),
            "remote_task_state_unknown",
        )
        self.assertFalse(chat_lifecycle.can_force_delete_private_chat(self.session))

    @mock.patch("apps.node.services.internal.node_registry.agent_ws_routable", return_value=False)
    def test_late_resource_does_not_reopen_force_deleted_chat(self, _agent_ws_routable):
        self.gateway_link.scope = LensGatewayLink.GatewayScope.ORGANIZATION
        self.gateway_link.save(update_fields=["scope", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "updated_at",
            ]
        )
        self.knowledge_source.lifecycle_status = LensKnowledgeSource.LifecycleStatus.DELETING
        self.knowledge_source.save(update_fields=["lifecycle_status", "updated_at"])
        self.workspace_binding.state = LensWorkspaceBinding.State.DELETING
        self.workspace_binding.save(update_fields=["state", "updated_at"])

        chat_lifecycle.force_delete_private_copilot_chat(
            self.session,
            requested_by=self.user,
        )
        late_uuid = uuid.uuid4()
        chat_lifecycle._record_late_source_lens_resource(
            self.session.id,
            field="sl_session_uuid",
            resource_uuid=late_uuid,
            error="late compensation response",
        )

        self.session.refresh_from_db()
        marker = self.session.teardown_state_json["forced_remote_cleanup"]
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.DELETED)
        self.assertTrue(self.session.is_deleted)
        self.assertEqual(marker["status"], "skipped")
        self.assertEqual(marker["late_remote_resources"][0]["remote_uuid"], str(late_uuid))

    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=False,
    )
    def test_legacy_pending_force_cleanup_can_be_finalized(self, _agent_ws_routable):
        """Old deferred-cleanup rows remain recoverable by Force Delete."""

        self.gateway_link.scope = LensGatewayLink.GatewayScope.ORGANIZATION
        self.gateway_link.save(update_fields=["scope", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.status = LensSessionLink.Status.ARCHIVED
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.teardown_state_json = {
            "forced_remote_cleanup": {
                "status": "pending",
                "session_link_id": self.session.id,
            }
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "status",
                "cleanup_intent",
                "cleanup_status",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {
            "forced_remote_cleanup": {
                "status": "pending",
                "session_link_id": self.session.id,
            }
        }
        self.knowledge_source.save(
            update_fields=["lifecycle_status", "teardown_state_json", "updated_at"]
        )
        self.workspace_binding.state = LensWorkspaceBinding.State.DELETING
        self.workspace_binding.save(update_fields=["state", "updated_at"])

        result = chat_lifecycle.force_delete_private_copilot_chat(
            self.session,
            requested_by=self.user,
        )

        self.assertEqual(result.lifecycle_status, LensSessionLink.LifecycleStatus.DELETED)
        self.assertTrue(result.is_deleted)
        self.assertEqual(
            result.teardown_state_json["forced_remote_cleanup"]["status"],
            "skipped",
        )

    @mock.patch("apps.node.services.internal.node_registry.agent_ws_routable", return_value=False)
    def test_completed_forced_cleanup_releases_snapshot_lease(
        self,
        _agent_ws_routable,
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="forced-cleanup-snapshot",
            idempotency_key="forced-cleanup-snapshot",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.gateway.organization = self.tenant
        self.gateway.save(update_fields=["organization", "updated_at"])
        self.gateway_link.organization = self.tenant
        self.gateway_link.scope = LensGatewayLink.GatewayScope.ORGANIZATION
        self.gateway_link.owner_user = self.user
        self.gateway_link.save(
            update_fields=["organization", "scope", "owner_user", "updated_at"]
        )
        self.workspace_binding.execution_organization_id = self.tenant.id
        self.workspace_binding.save(
            update_fields=["execution_organization_id", "updated_at"]
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.status = LensSessionLink.Status.ARCHIVED
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.sl_session_uuid = None
        self.session.sl_assistant_uuid = None
        self.session.backup_source_snapshot_id = snapshot.id
        self.session.teardown_state_json = {
            "intent": "delete_session",
            "revoke_shares": {"status": "success"},
            "delete_session": {"status": "success"},
            "delete_assistant": {"status": "success"},
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            },
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "status",
                "cleanup_intent",
                "cleanup_status",
                "sl_session_uuid",
                "sl_assistant_uuid",
                "backup_source_snapshot_id",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.knowledge_source.sl_assistant_uuid = None
        self.knowledge_source.sl_datasource_uuid = None
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {
            "cancel_chat_restore": {"status": "success"},
            "cancel_conversion": {"status": "success"},
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            },
        }
        self.knowledge_source.save(
            update_fields=[
                "sl_assistant_uuid",
                "sl_datasource_uuid",
                "lifecycle_status",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.workspace_binding.state = LensWorkspaceBinding.State.DELETING
        self.workspace_binding.save(update_fields=["state", "updated_at"])
        acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
            consumer_id=self.session.id,
        )
        chat_lifecycle.force_delete_private_copilot_chat(self.session, requested_by=self.user)
        reconcile_snapshot_usage_leases()
        self.assertFalse(SnapshotUsageLease.objects.filter(snapshot_id=snapshot.id).exists())
        self.session.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        self.workspace_binding.refresh_from_db()
        self.assertEqual(
            self.session.teardown_state_json["forced_remote_cleanup"]["status"],
            "skipped",
        )
        self.assertEqual(
            self.knowledge_source.teardown_state_json["forced_remote_cleanup"][
                "status"
            ],
            "skipped",
        )
        self.assertEqual(
            self.workspace_binding.state,
            LensWorkspaceBinding.State.DELETED,
        )
        self.assertTrue(self.session.is_deleted)

    def test_force_delete_is_not_available_for_public_gateway(self):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.sl_session_uuid = None
        self.session.sl_assistant_uuid = None
        self.session.teardown_state_json = {
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            }
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "sl_session_uuid",
                "sl_assistant_uuid",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.knowledge_source.sl_assistant_uuid = None
        self.knowledge_source.sl_datasource_uuid = None
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {
            "cancel_chat_restore": {"status": "success"},
            "cancel_conversion": {"status": "success"},
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            },
        }
        self.knowledge_source.save(
            update_fields=[
                "sl_assistant_uuid",
                "sl_datasource_uuid",
                "lifecycle_status",
                "teardown_state_json",
                "updated_at",
            ]
        )

        self.assertFalse(chat_lifecycle.can_force_delete_private_chat(self.session))

    @mock.patch(
        "apps.lens_bridge.tasks.knowledge_source_teardown."
        "execute_knowledge_source_teardown_task.delay"
    )
    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_teardown_task.delay"
    )
    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=False,
    )
    def test_forced_cleanup_reconciler_waits_while_private_gateway_is_offline(
        self,
        _agent_ws_routable,
        chat_delay,
        knowledge_source_delay,
    ):
        marker = {
            "status": "pending",
            "session_link_id": self.session.id,
            "workspace_uid": str(self.workspace_binding.workspace_uid),
        }
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.teardown_state_json = {"forced_remote_cleanup": marker}
        self.session.teardown_next_retry_at = timezone.now()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "teardown_state_json",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {
            "forced_remote_cleanup": marker
        }
        self.knowledge_source.teardown_next_retry_at = timezone.now()
        self.knowledge_source.save(
            update_fields=[
                "lifecycle_status",
                "teardown_state_json",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )

        result = reconcile_lens_resource_teardowns_task(limit=10)

        self.assertEqual(result["queued"], 0)
        chat_delay.assert_not_called()
        knowledge_source_delay.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.tasks.knowledge_source_teardown."
        "execute_knowledge_source_teardown_task.delay"
    )
    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_teardown_task.delay"
    )
    @mock.patch(
        "apps.node.services.internal.node_registry.agent_ws_routable",
        return_value=True,
    )
    def test_forced_cleanup_reconciler_does_not_resume_when_private_gateway_is_online(
        self,
        _agent_ws_routable,
        chat_delay,
        knowledge_source_delay,
    ):
        marker = {
            "status": "pending",
            "session_link_id": self.session.id,
            "workspace_uid": str(self.workspace_binding.workspace_uid),
        }
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.teardown_state_json = {"forced_remote_cleanup": marker}
        self.session.teardown_next_retry_at = timezone.now()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "teardown_state_json",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_state_json = {
            "forced_remote_cleanup": marker
        }
        self.knowledge_source.teardown_next_retry_at = timezone.now()
        self.knowledge_source.save(
            update_fields=[
                "lifecycle_status",
                "teardown_state_json",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )

        result = reconcile_lens_resource_teardowns_task(limit=10)

        self.assertEqual(result["queued"], 0)
        chat_delay.assert_not_called()
        knowledge_source_delay.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.tasks.knowledge_source_teardown."
        "execute_knowledge_source_teardown_task.delay"
    )
    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_teardown_task.delay"
    )
    @mock.patch("apps.node.services.internal.node_registry.agent_ws_routable")
    def test_forced_cleanup_records_do_not_enter_any_gateway_queue(
        self,
        agent_ws_routable,
        chat_delay,
        knowledge_source_delay,
    ):
        second_gateway = Node.objects.create(
            organization=self.tenant,
            name="second-private-gateway",
            role=Node.Role.GATEWAY,
        )
        second_gateway_link = LensGatewayLink.objects.create(
            organization=self.tenant,
            gateway=second_gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.ORGANIZATION,
            workspace_root="/workspace/second/data",
        )
        LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=second_gateway_link,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
            cleanup_intent=LensSessionLink.CleanupIntent.DELETE_SESSION,
            cleanup_status=LensSessionLink.CleanupStatus.PENDING,
            teardown_next_retry_at=timezone.now(),
            teardown_state_json={
                "forced_remote_cleanup": {"status": "pending"}
            },
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.teardown_next_retry_at = timezone.now() - timedelta(minutes=1)
        self.session.teardown_state_json = {
            "forced_remote_cleanup": {"status": "pending"}
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "teardown_next_retry_at",
                "teardown_state_json",
                "updated_at",
            ]
        )
        agent_ws_routable.side_effect = lambda *, agent_id: (
            agent_id == second_gateway.id
        )

        result = reconcile_lens_resource_teardowns_task(limit=1)

        self.assertEqual(result["session_ids"], [])
        self.assertEqual(result["queued"], 0)
        chat_delay.assert_not_called()
        knowledge_source_delay.assert_not_called()

    @mock.patch("apps.lens_bridge.services.assistant_access.soft_delete_assistant_link")
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_quarantined_workspace_releases_slot_while_purge_retries(
        self,
        request_json,
        _delete_assistant,
        _soft_delete_assistant,
    ):
        request_json.side_effect = self._not_found()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "updated_at",
            ]
        )
        LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        def fail_after_quarantine(**_kwargs):
            LensKnowledgeSource.all_objects.filter(pk=self.knowledge_source.id).update(
                teardown_state_json={
                    "cancel_chat_restore": {"status": "success"},
                    "cancel_conversion": {"status": "success"},
                    "workspace_cleanup_safety": {
                        "workspace_uid": str(self.workspace_binding.workspace_uid),
                        "workspace_quarantined": True,
                        "purge_complete": False,
                        "tombstone_state": "retiring",
                    },
                }
            )
            raise knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError(
                "Workspace trash purge will be retried."
            )

        with (
            mock.patch(
                "apps.lens_bridge.services.knowledge_source_teardown."
                "run_knowledge_source_teardown",
                side_effect=fail_after_quarantine,
            ),
            self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError),
        ):
            chat_lifecycle.run_copilot_chat_teardown(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertEqual(
            self.session.capacity_reservation_status,
            LensSessionLink.CapacityReservationStatus.RESERVED,
        )
        self.assertEqual(
            self.session.teardown_state_json["prepare_slot_release_barrier"]["status"],
            "satisfied",
        )
        self.assertEqual(
            self.session.teardown_state_json["prepare_slot_release_barrier"][
                "session_generation"
            ],
            self.session.provision_generation,
        )
        self.assertFalse(
            LensGatewayChatSlot.objects.filter(session_link=self.session).exists()
        )

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle.gateway_chat_queue.wake_gateway_queue"
    )
    def test_failed_provision_records_reset_for_retry_intent(
        self,
        wake_gateway_queue,
        queue_teardown,
    ):
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.tenant.id,
            snapshot_uid="failed-provision-snapshot",
            idempotency_key="failed-provision-snapshot",
            source_type="agent",
            source_ref_id=self.source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        claim_token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = claim_token
        self.session.backup_source_snapshot_id = snapshot.id
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "backup_source_snapshot_id",
                "updated_at",
            ]
        )
        acquire_snapshot_usage(
            organization_id=self.tenant.id,
            snapshot_id=snapshot.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
            consumer_id=self.session.id,
        )

        with self.captureOnCommitCallbacks(execute=True):
            changed = chat_lifecycle._transition_failed_provision_to_teardown(
                self.session.id,
                str(claim_token),
                message="workspace cleanup failed",
            )

        self.assertTrue(changed)
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.teardown_state_json["intent"],
            "reset_for_retry",
        )
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.FAILED,
        )
        self.assertEqual(
            self.session.cleanup_intent,
            LensSessionLink.CleanupIntent.RESET_FOR_RETRY,
        )
        self.assertEqual(
            self.session.cleanup_status,
            LensSessionLink.CleanupStatus.PENDING,
        )
        self.assertTrue(
            SnapshotUsageLease.objects.filter(
                snapshot_id=snapshot.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
                consumer_id=str(self.session.id),
            ).exists()
        )
        self.assertEqual(self.session.status, LensSessionLink.Status.ACTIVE)
        queue_teardown.assert_called_once_with(self.session.id)
        wake_gateway_queue.assert_called_once_with(self.gateway_link.id)

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "knowledge_source_sync.run_knowledge_source_sync",
        return_value={"status": "waiting", "retry_after_seconds": 15},
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "knowledge_source_sync.prepare_new_knowledge_source",
        side_effect=lambda *, org, ks: ks,
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "chat_user_provisioning.ensure_sl_chat_user"
    )
    @mock.patch("apps.lens_bridge.services.chat_lifecycle._reserve_chat_capacity")
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._resolve_chat_scopes",
        return_value=None,
    )
    @mock.patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_chat_knowledge_source_always_pins_selected_snapshot(
        self,
        _context,
        _resolve_scopes,
        _reserve_capacity,
        _ensure_user,
        _prepare_knowledge_source,
        _run_sync,
    ):
        claim_token = uuid.uuid4()
        self.session.knowledge_source = None
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = claim_token
        self.session.provision_claimed_at = timezone.now()
        self.session.backup_source_snapshot_id = 11
        self.session.source_scopes_json = [
            {
                "source_path": "/data",
                "backup_snapshot_directory_id": 12,
                "path_type": "dir",
            }
        ]
        self.session.save(
            update_fields=[
                "knowledge_source",
                "lifecycle_status",
                "provision_claim_token",
                "provision_claimed_at",
                "backup_source_snapshot_id",
                "source_scopes_json",
                "updated_at",
            ]
        )

        result = chat_lifecycle._run_copilot_chat_provision(
            session_link_id=self.session.id,
            claim_token=str(claim_token),
        )

        self.assertEqual(result["status"], "waiting")
        self.session.refresh_from_db()
        knowledge_source = self.session.knowledge_source
        self.assertIsNotNone(knowledge_source)
        self.assertEqual(
            knowledge_source.linked_version_mode,
            LensKnowledgeSource.LinkedVersionMode.PINNED,
        )
        self.assertEqual(knowledge_source.pinned_snapshot_id, 11)

    @mock.patch("apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown")
    def test_orphan_ks_cleanup_returns_without_enqueue_when_deleted(
        self,
        queue_ks_teardown,
    ):
        with mock.patch(
            "apps.lens_bridge.services.knowledge_source_teardown."
            "run_knowledge_source_teardown",
            return_value={
                "knowledge_source_id": self.knowledge_source.id,
                "status": "deleted",
            },
        ) as run_teardown:
            chat_lifecycle._cleanup_orphan_knowledge_source(
                self.knowledge_source,
                owner_session_link_id=self.session.id,
            )

        run_teardown.assert_called_once_with(
            knowledge_source_id=self.knowledge_source.id,
            owner_session_link_id=self.session.id,
        )
        queue_ks_teardown.assert_not_called()
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.lifecycle_status,
            LensKnowledgeSource.LifecycleStatus.DELETING,
        )

    @mock.patch("apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown")
    def test_orphan_ks_cleanup_does_not_enqueue_when_busy_or_scheduled(
        self,
        queue_ks_teardown,
    ):
        for status in ("busy", "scheduled"):
            queue_ks_teardown.reset_mock()
            with mock.patch(
                "apps.lens_bridge.services.knowledge_source_teardown."
                "run_knowledge_source_teardown",
                return_value={
                    "knowledge_source_id": self.knowledge_source.id,
                    "status": status,
                },
            ):
                chat_lifecycle._cleanup_orphan_knowledge_source(
                    self.knowledge_source,
                    owner_session_link_id=self.session.id,
                )

            queue_ks_teardown.assert_not_called()

    @mock.patch("apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown")
    def test_orphan_ks_cleanup_enqueues_when_inline_teardown_fails(
        self,
        queue_ks_teardown,
    ):
        with mock.patch(
            "apps.lens_bridge.services.knowledge_source_teardown."
            "run_knowledge_source_teardown",
            side_effect=RuntimeError("SourceLens unavailable"),
        ):
            chat_lifecycle._cleanup_orphan_knowledge_source(
                self.knowledge_source,
                owner_session_link_id=self.session.id,
            )

        queue_ks_teardown.assert_called_once_with(
            knowledge_source_id=self.knowledge_source.id
        )

    @mock.patch("apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown")
    def test_orphan_ks_cleanup_skips_enqueue_when_retry_already_scheduled(
        self,
        queue_ks_teardown,
    ):
        def fail_and_schedule(**_kwargs):
            LensKnowledgeSource.all_objects.filter(pk=self.knowledge_source.id).update(
                teardown_next_retry_at=timezone.now() + timedelta(minutes=5),
                teardown_claimed_at=None,
                teardown_claim_token=None,
                updated_at=timezone.now(),
            )
            raise RuntimeError("SourceLens unavailable")

        with mock.patch(
            "apps.lens_bridge.services.knowledge_source_teardown."
            "run_knowledge_source_teardown",
            side_effect=fail_and_schedule,
        ):
            chat_lifecycle._cleanup_orphan_knowledge_source(
                self.knowledge_source,
                owner_session_link_id=self.session.id,
            )

        queue_ks_teardown.assert_not_called()

    @mock.patch("apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown")
    def test_orphan_ks_cleanup_skips_enqueue_when_teardown_lease_is_live(
        self,
        queue_ks_teardown,
    ):
        def fail_with_live_claim(**_kwargs):
            LensKnowledgeSource.all_objects.filter(pk=self.knowledge_source.id).update(
                teardown_claimed_at=timezone.now(),
                updated_at=timezone.now(),
            )
            raise RuntimeError("lease lost mid-teardown")

        with mock.patch(
            "apps.lens_bridge.services.knowledge_source_teardown."
            "run_knowledge_source_teardown",
            side_effect=fail_with_live_claim,
        ):
            chat_lifecycle._cleanup_orphan_knowledge_source(
                self.knowledge_source,
                owner_session_link_id=self.session.id,
            )

        queue_ks_teardown.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown",
        side_effect=RuntimeError("broker unavailable"),
    )
    def test_orphan_ks_cleanup_records_queue_failure_on_status_detail(
        self,
        _queue_ks_teardown,
    ):
        with mock.patch(
            "apps.lens_bridge.services.knowledge_source_teardown."
            "run_knowledge_source_teardown",
            side_effect=RuntimeError("SourceLens unavailable"),
        ):
            chat_lifecycle._cleanup_orphan_knowledge_source(
                self.knowledge_source,
                owner_session_link_id=self.session.id,
            )

        self.knowledge_source.refresh_from_db()
        self.assertIn(
            "waiting for the worker queue",
            self.knowledge_source.status_detail.lower(),
        )
        self.assertIn("broker unavailable", self.knowledge_source.status_detail)

    @mock.patch("apps.lens_bridge.services.assistant_access.soft_delete_assistant_link")
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_teardown_persists_partial_success_and_retry_converges(
        self,
        request_json,
        run_agent_task,
        _delete_assistant,
        _soft_delete_assistant,
    ):
        transient = sl_client.LensBridgeError("temporarily unavailable")
        transient.status_code = 503
        request_json.side_effect = transient
        run_agent_task.return_value = mock.MagicMock(
            ok=True,
            timed_out=False,
            task=mock.MagicMock(id=uuid.uuid4(), last_error=""),
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.save(update_fields=["lifecycle_status", "updated_at"])

        with self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError):
            chat_lifecycle.run_copilot_chat_teardown(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertIsNotNone(self.session.sl_session_uuid)
        self.assertIsNotNone(self.session.sl_assistant_uuid)
        self.assertEqual(
            self.session.knowledge_source_id,
            self.knowledge_source.id,
        )
        self.assertIsNone(self.session.teardown_claimed_at)
        _delete_assistant.assert_not_called()
        run_agent_task.assert_not_called()

        request_json.side_effect = self._not_found()
        self.session.teardown_next_retry_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=["teardown_next_retry_at", "updated_at"])
        result = chat_lifecycle.run_copilot_chat_teardown(
            session_link_id=self.session.id
        )

        self.assertEqual(result["status"], "deleted")
        self.session.refresh_from_db()
        self.assertIsNone(self.session.sl_session_uuid)
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETED,
        )

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._cleanup_failed_provision",
        return_value=["assistant_create: remote create outcome is unknown"],
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._run_copilot_chat_provision",
        side_effect=RuntimeError("provision failed"),
    )
    def test_failed_provision_retains_chat_resources_for_retry(
        self,
        _run_provision,
        _cleanup,
        queue_teardown,
    ):
        claim_token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = claim_token
        self.session.provision_claimed_at = timezone.now()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "provision_claimed_at",
                "updated_at",
            ]
        )

        with (
            mock.patch(
                "apps.lens_bridge.services.chat_lifecycle."
                "_claim_copilot_chat_provision",
                return_value=(str(claim_token), "claimed"),
            ),
            self.captureOnCommitCallbacks(execute=True),
            self.assertRaisesRegex(
                RuntimeError,
                "provision failed",
            ),
        ):
            chat_lifecycle.run_copilot_chat_provision(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.FAILED,
        )
        self.assertEqual(
            self.session.provision_phase,
            LensSessionLink.ProvisionPhase.QUEUED,
        )
        self.assertIsNone(self.session.provision_claim_token)
        self.assertEqual(
            self.session.cleanup_status,
            LensSessionLink.CleanupStatus.NONE,
        )
        self.assertEqual(self.session.knowledge_source_id, self.knowledge_source.id)
        self.assertTrue(
            LensKnowledgeSource.objects.filter(pk=self.knowledge_source.id).exists()
        )
        _cleanup.assert_not_called()
        queue_teardown.assert_not_called()

        with (
            mock.patch(
                "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
            ) as queue_delete,
            self.captureOnCommitCallbacks(execute=True),
        ):
            chat_lifecycle.request_copilot_chat_teardown(self.session)
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.cleanup_intent,
            LensSessionLink.CleanupIntent.DELETE_SESSION,
        )
        self.assertEqual(self.session.knowledge_source_id, self.knowledge_source.id)
        queue_delete.assert_called_once_with(self.session.id)

    @mock.patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    def test_failed_chat_retry_retains_orphan_task_and_gateway_slot(
        self, queue_provision
    ):
        self.knowledge_source.status = LensKnowledgeSource.Status.ERROR
        self.knowledge_source.sync_state_json = {
            "completed_phases": ["prepare_workspace", "restore_snapshot"],
            "conversion": {
                "task_id": "orphan-1",
                "status": "FAILURE",
                "resume_attempts": 3,
                "error": "DATASOURCE_CONVERSION_RESUME_EXHAUSTED",
            },
        }
        self.knowledge_source.save(
            update_fields=["status", "sync_state_json", "updated_at"]
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.backup_source_snapshot_id = (
            self.knowledge_source.backup_source_snapshot_id
        )
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.NONE
        self.session.cleanup_status = LensSessionLink.CleanupStatus.NONE
        self.session.save(
            update_fields=[
                "lifecycle_status", "backup_source_snapshot_id",
                "cleanup_intent", "cleanup_status", "updated_at"
            ]
        )
        generation = self.session.provision_generation
        LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            updated = chat_lifecycle.retry_copilot_chat_provision(self.session)

        self.assertEqual(updated.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING)
        self.assertEqual(updated.provision_generation, generation)
        self.knowledge_source.refresh_from_db()
        conversion = self.knowledge_source.sync_state_json["conversion"]
        self.assertEqual(self.knowledge_source.status, LensKnowledgeSource.Status.SYNCING)
        self.assertEqual(conversion["task_id"], "orphan-1")
        self.assertEqual(conversion["resume_attempts"], 0)
        self.assertEqual(conversion["manual_retry_count"], 1)
        self.assertTrue(LensGatewayChatSlot.objects.filter(session_link=self.session).exists())
        queue_provision.assert_called_once_with(self.session.id)

    @mock.patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    def test_retry_after_assistant_failure_keeps_successful_conversion(
        self, queue_provision
    ):
        self.knowledge_source.sync_state_json = {
            "completed_phases": [
                "prepare_workspace", "restore_snapshot",
                "ensure_managed_datasource", "convert_documents",
            ],
            "conversion": {
                "task_id": "completed-1",
                "status": "SUCCESS",
                "summary": {"total": 1, "success": 1},
            },
        }
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.backup_source_snapshot_id = (
            self.knowledge_source.backup_source_snapshot_id
        )
        self.session.save(
            update_fields=["lifecycle_status", "backup_source_snapshot_id", "updated_at"]
        )

        with (
            mock.patch("apps.lens_bridge.services.chat_lifecycle._acquire_chat_snapshot_usage"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            updated = chat_lifecycle.retry_copilot_chat_provision(self.session)

        self.assertEqual(updated.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING)
        self.knowledge_source.refresh_from_db()
        conversion = self.knowledge_source.sync_state_json["conversion"]
        self.assertEqual(conversion["status"], "SUCCESS")
        self.assertEqual(conversion["summary"]["success"], 1)
        queue_provision.assert_called_once_with(self.session.id)

    def test_exhausted_conversion_failure_retains_workspace_and_busy_slot(self):
        self.knowledge_source.status = LensKnowledgeSource.Status.ERROR
        self.knowledge_source.sync_state_json = {
            "conversion": {
                "task_id": "orphan-1",
                "status": "FAILURE",
                "error": "DATASOURCE_CONVERSION_REBIND_PAUSED",
            }
        }
        self.knowledge_source.save(
            update_fields=["status", "sync_state_json", "updated_at"]
        )
        token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = token
        self.session.save(
            update_fields=["lifecycle_status", "provision_claim_token", "updated_at"]
        )
        slot = LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        chat_lifecycle._mark_provision_failed_by_id(
            self.session.id,
            str(token),
            "DATASOURCE_CONVERSION_REBIND_PAUSED",
            expected_generation=self.session.provision_generation,
        )

        self.session.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.FAILED)
        self.assertEqual(self.session.cleanup_intent, LensSessionLink.CleanupIntent.NONE)
        self.assertEqual(self.session.knowledge_source_id, self.knowledge_source.id)
        self.assertTrue(LensGatewayChatSlot.objects.filter(pk=slot.pk).exists())

    def test_failed_chat_releases_slot_before_conversion_was_dispatched(self):
        self.knowledge_source.status = LensKnowledgeSource.Status.ERROR
        self.knowledge_source.sync_state_json = {
            "completed_phases": ["prepare_workspace", "restore_snapshot"],
        }
        self.knowledge_source.save(
            update_fields=["status", "sync_state_json", "updated_at"]
        )
        token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = token
        self.session.save(
            update_fields=["lifecycle_status", "provision_claim_token", "updated_at"]
        )
        slot = LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        chat_lifecycle._mark_provision_failed_by_id(
            self.session.id, str(token), "conversion not dispatched",
            expected_generation=self.session.provision_generation,
        )

        self.session.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.FAILED)
        self.assertFalse(LensGatewayChatSlot.objects.filter(pk=slot.pk).exists())
        self.assertEqual(self.session.knowledge_source_id, self.knowledge_source.id)

    @mock.patch(
        "apps.lens_bridge.services.sl_client.get_task_by_id",
    )
    def test_failed_chat_releases_slot_after_remote_stop_is_confirmed(self, get_task):
        self.knowledge_source.status = LensKnowledgeSource.Status.ERROR
        self.knowledge_source.sync_state_json = {
            "conversion": {"task_id": "orphan-1", "status": "FAILURE"}
        }
        self.knowledge_source.save(update_fields=["status", "sync_state_json", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.NONE
        self.session.cleanup_status = LensSessionLink.CleanupStatus.NONE
        self.session.save(
            update_fields=["lifecycle_status", "cleanup_intent", "cleanup_status", "updated_at"]
        )
        slot = LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway_link,
            slot_number=1,
            session_link=self.session,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )

        get_task.return_value = {
            "task_id": "orphan-1", "status": "FAILURE", "metadata": {},
        }
        self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 0)
        self.assertTrue(LensGatewayChatSlot.objects.filter(pk=slot.pk).exists())
        self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 0)
        self.assertEqual(get_task.call_count, 1)
        self.session.refresh_from_db()
        journal = dict(self.session.provision_state_json or {})
        journal["failed_slot_stop_probe_after"] = (
            timezone.now() - timedelta(seconds=1)
        ).isoformat()
        self.session.provision_state_json = journal
        self.session.save(update_fields=["provision_state_json", "updated_at"])
        get_task.return_value["metadata"]["conversion_summary"] = {}
        released = chat_lifecycle.release_stopped_failed_chat_slots()

        self.assertEqual(released, 1)
        self.assertFalse(LensGatewayChatSlot.objects.filter(pk=slot.pk).exists())
        self.assertTrue(
            LensKnowledgeSource.objects.filter(pk=self.knowledge_source.id).exists()
        )

    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown."
        "run_knowledge_source_teardown"
    )
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_failed_provision_cleanup_respects_dependency_order(
        self,
        request_json,
        delete_assistant,
        teardown_knowledge_source,
    ):
        claim_token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = claim_token
        self.session.provision_claimed_at = timezone.now()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "provision_claimed_at",
                "updated_at",
            ]
        )
        transient = sl_client.LensBridgeError("temporarily unavailable")
        transient.status_code = 503
        request_json.side_effect = transient

        errors = chat_lifecycle._cleanup_failed_provision(
            self.session,
            str(claim_token),
        )

        self.assertEqual(
            errors,
            ["delete_session: temporarily unavailable"],
        )
        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.sl_session_uuid)
        self.assertIsNotNone(self.session.sl_assistant_uuid)
        self.assertEqual(
            self.session.knowledge_source_id,
            self.knowledge_source.id,
        )
        delete_assistant.assert_not_called()
        teardown_knowledge_source.assert_not_called()

    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_remote_recovery_scans_paginated_results(self, request_json):
        target_uuid = uuid.uuid4()
        request_json.side_effect = [
            {
                "results": [
                    {
                        "uuid": str(uuid.uuid4()),
                        "slug": f"unrelated-{index}",
                    }
                    for index in range(100)
                ]
            },
            {
                "results": [
                    {
                        "uuid": str(target_uuid),
                        "slug": "target-assistant",
                    }
                ]
            },
        ]

        recovered_uuid = chat_lifecycle._find_remote_uuid(
            path="/api/lens/assistants/",
            field="slug",
            value="target-assistant",
        )

        self.assertEqual(recovered_uuid, target_uuid)
        self.assertEqual(
            request_json.call_args_list,
            [
                mock.call(
                    "GET",
                    "/api/lens/assistants/",
                    params={"page": 1, "page_size": 100},
                    hfl_user=None,
                ),
                mock.call(
                    "GET",
                    "/api/lens/assistants/",
                    params={"page": 2, "page_size": 100},
                    hfl_user=None,
                ),
            ],
        )

    def test_assistant_recovery_slug_preserves_unique_ks_suffix(self):
        long_name = "very-long-knowledge-source-" * 5 + "final-tail-123456789"
        self.knowledge_source.name = long_name
        self.knowledge_source.save(update_fields=["name", "updated_at"])
        second_knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.tenant,
            name=long_name,
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            source_path="/another-path",
            status=LensKnowledgeSource.Status.READY,
            created_by=self.user,
        )

        first_slug = chat_lifecycle.provisioning.assistant_slug_for_ks(
            org=self.tenant,
            ks=self.knowledge_source,
        )
        second_slug = chat_lifecycle.provisioning.assistant_slug_for_ks(
            org=self.tenant,
            ks=second_knowledge_source,
        )

        self.assertLessEqual(len(first_slug), 160)
        self.assertLessEqual(len(second_slug), 160)
        self.assertTrue(first_slug.endswith(f"-ks-{self.knowledge_source.id}"))
        self.assertTrue(second_slug.endswith(f"-ks-{second_knowledge_source.id}"))
        self.assertNotEqual(first_slug, second_slug)

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._grant_assistant_to_chat_user"
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle.assistant_access.ensure_assistant_link"
    )
    @mock.patch(
        "apps.lens_bridge.services.gateway_readiness.agent_ws_routable",
        return_value=True,
    )
    @mock.patch(
        "apps.lens_bridge.services.gateway_readiness.get_agent_session",
        return_value="test-agent-session",
    )
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle._find_remote_uuid")
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle.chat_user_provisioning.ensure_sl_chat_user"
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle.knowledge_source_sync.run_knowledge_source_sync"
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle.provisioning.create_sl_assistant_for_ks"
    )
    def test_journal_recovers_remote_creates_without_reposting(
        self,
        create_assistant,
        run_sync,
        ensure_sl_user,
        find_remote_uuid,
        request_json,
        _get_agent_session,
        _agent_ws_routable,
        _ensure_assistant_link,
        _grant_assistant,
    ):
        claim_token = uuid.uuid4()
        assistant_uuid = uuid.uuid4()
        session_uuid = uuid.uuid4()
        session_operation_id = uuid.uuid4()
        session_marker = f"__hfl_provision_{session_operation_id.hex}__"
        assistant_slug = chat_lifecycle.provisioning.assistant_slug_for_ks(
            org=self.tenant,
            ks=self.knowledge_source,
        )
        self.knowledge_source.sl_assistant_uuid = None
        self.knowledge_source.save(update_fields=["sl_assistant_uuid", "updated_at"])
        self.gateway_link.sl_lensnode_uuid = uuid.uuid4()
        self.gateway_link.sidecar_status = LensGatewayLink.SidecarStatus.ONLINE
        self.gateway_link.save(
            update_fields=["sl_lensnode_uuid", "sidecar_status", "updated_at"]
        )
        self.gateway.metadata = {
            "inventory_session_id": "test-agent-session",
            "inventory_capabilities_session_id": "test-agent-session",
            "inventory": {"capabilities": ["insight_safe_restore_v1"]},
        }
        self.gateway.save(update_fields=["metadata", "updated_at"])
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = claim_token
        self.session.provision_claimed_at = timezone.now()
        self.session.provision_next_retry_at = timezone.now()
        self.session.sl_session_uuid = None
        self.session.sl_assistant_uuid = None
        self.session.backup_source_snapshot_id = 11
        self.session.source_scopes_json = [
            {
                "source_path": "/data",
                "backup_snapshot_directory_id": 12,
            }
        ]
        self.session.agent_model_ref = uuid.uuid4()
        self.session.title = "Recovered Chat"
        self.session.provision_state_json = {
            "assistant_create": {
                "operation_id": str(uuid.uuid4()),
                "kind": "assistant_create",
                "lookup_key": assistant_slug,
                "remote_uuid": "",
                "status": "intent",
            },
            "session_create": {
                "operation_id": str(session_operation_id),
                "kind": "session_create",
                "lookup_key": session_marker,
                "remote_uuid": "",
                "status": "intent",
            },
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "provision_claimed_at",
                "provision_next_retry_at",
                "sl_session_uuid",
                "sl_assistant_uuid",
                "backup_source_snapshot_id",
                "source_scopes_json",
                "agent_model_ref",
                "title",
                "provision_state_json",
                "updated_at",
            ]
        )
        run_sync.return_value = {"status": "ready"}
        ensure_sl_user.return_value = mock.MagicMock(sl_user_id=37)

        def find_resource(**kwargs):
            if kwargs["field"] == "slug":
                self.assertEqual(kwargs["value"], assistant_slug)
                return assistant_uuid
            self.assertEqual(kwargs["field"], "title")
            self.assertEqual(kwargs["value"], session_marker)
            return session_uuid

        find_remote_uuid.side_effect = find_resource
        request_json.return_value = {}

        result = chat_lifecycle._run_copilot_chat_provision(
            session_link_id=self.session.id,
            claim_token=str(claim_token),
        )

        self.assertEqual(result["status"], "ready")
        create_assistant.assert_not_called()
        self.assertFalse(
            any(
                call.args[:2] == ("POST", "/api/lens/sessions/")
                for call in request_json.call_args_list
            )
        )
        request_json.assert_called_once_with(
            "PATCH",
            f"/api/lens/sessions/{session_uuid}/",
            json_body={"title": "Recovered Chat"},
            hfl_user=self.user,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.sl_assistant_uuid, assistant_uuid)
        self.assertEqual(self.session.sl_session_uuid, session_uuid)
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.READY,
        )

    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_teardown_task.delay"
    )
    def test_reconciler_requeues_due_teardown(self, delay):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.teardown_next_retry_at = timezone.now()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )

        result = reconcile_lens_resource_teardowns_task(limit=10)

        self.assertEqual(result["queued"], 1)
        delay.assert_called_once_with(session_link_id=self.session.id)

    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_teardown_task.delay"
    )
    def test_reconciler_does_not_requeue_live_long_running_claim(self, delay):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.teardown_next_retry_at = timezone.now()
        self.session.teardown_claimed_at = timezone.now() - timedelta(minutes=15)
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "teardown_next_retry_at",
                "teardown_claimed_at",
                "updated_at",
            ]
        )

        result = reconcile_lens_resource_teardowns_task(limit=10)

        self.assertEqual(result["queued"], 0)
        delay.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    def test_teardown_request_atomically_fences_provisioning(self, queue_teardown):
        provision_token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = provision_token
        self.session.provision_claimed_at = timezone.now()
        self.session.provision_next_retry_at = timezone.now() + timedelta(minutes=5)
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "provision_claimed_at",
                "provision_next_retry_at",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = chat_lifecycle.request_copilot_chat_teardown(self.session)

        result.refresh_from_db()
        self.assertEqual(
            result.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertIsNone(result.provision_claim_token)
        self.assertIsNone(result.provision_claimed_at)
        self.assertIsNone(result.provision_next_retry_at)
        self.assertEqual(result.teardown_state_json["intent"], "delete_session")
        queue_teardown.assert_called_once_with(self.session.id)

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    def test_repeated_teardown_request_preserves_live_claim(self, queue_teardown):
        teardown_token = uuid.uuid4()
        claimed_at = timezone.now()
        next_retry_at = claimed_at + timedelta(minutes=10)
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.teardown_attempts = 3
        self.session.teardown_claim_token = teardown_token
        self.session.teardown_claimed_at = claimed_at
        self.session.teardown_next_retry_at = next_retry_at
        self.session.teardown_state_json = {"intent": "delete_session"}
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "teardown_attempts",
                "teardown_claim_token",
                "teardown_claimed_at",
                "teardown_next_retry_at",
                "teardown_state_json",
                "updated_at",
            ]
        )
        with self.captureOnCommitCallbacks(execute=True):
            result = chat_lifecycle.request_copilot_chat_teardown(self.session)

        result.refresh_from_db()
        self.assertEqual(result.teardown_attempts, 3)
        self.assertEqual(result.teardown_claim_token, teardown_token)
        self.assertEqual(result.teardown_claimed_at, claimed_at)
        self.assertEqual(result.teardown_next_retry_at, next_retry_at)
        queue_teardown.assert_called_once_with(self.session.id)

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    def test_retry_delete_rechecks_blocked_cleanup_without_bypassing_fences(
        self,
        queue_teardown,
    ):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.teardown_attempts = 12
        self.session.teardown_state_json = {
            "intent": "delete_session",
            "blocking": {
                "reason": "restore_executor_still_stopping",
                "task_id": "restore-task-1",
                "intervention_required": True,
            },
            "cancel_chat_restore": {"status": "waiting"},
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "teardown_attempts",
                "teardown_state_json",
                "updated_at",
            ]
        )
        self.knowledge_source.teardown_attempts = 12
        self.knowledge_source.teardown_state_json = {
            "blocking": {
                "reason": "restore_executor_still_stopping",
                "task_id": "restore-task-1",
                "intervention_required": True,
            },
            "cancel_chat_restore": {"status": "waiting"},
        }
        self.knowledge_source.save(
            update_fields=[
                "teardown_attempts",
                "teardown_state_json",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = chat_lifecycle.request_copilot_chat_teardown(self.session)

        result.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            result.cleanup_status,
            LensSessionLink.CleanupStatus.PENDING,
        )
        self.assertEqual(result.teardown_attempts, 0)
        self.assertEqual(
            result.teardown_state_json["blocking"]["reason"],
            "restore_executor_still_stopping",
        )
        self.assertEqual(
            result.teardown_state_json["cancel_chat_restore"]["status"],
            "waiting",
        )
        self.assertEqual(self.knowledge_source.teardown_attempts, 0)
        self.assertEqual(
            self.knowledge_source.teardown_state_json["blocking"]["reason"],
            "restore_executor_still_stopping",
        )
        queue_teardown.assert_called_once_with(self.session.id)

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    def test_delete_overrides_retry_cleanup_and_fences_live_claim(
        self,
        queue_teardown,
    ):
        teardown_token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.status = LensSessionLink.Status.ACTIVE
        self.session.teardown_attempts = 2
        self.session.teardown_claim_token = teardown_token
        self.session.teardown_claimed_at = timezone.now()
        self.session.teardown_next_retry_at = timezone.now() + timedelta(minutes=5)
        self.session.teardown_state_json = {
            "intent": "reset_for_retry",
            "provision_error": "prepare failed",
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "status",
                "teardown_attempts",
                "teardown_claim_token",
                "teardown_claimed_at",
                "teardown_next_retry_at",
                "teardown_state_json",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = chat_lifecycle.request_copilot_chat_teardown(self.session)

        result.refresh_from_db()
        self.assertEqual(result.status, LensSessionLink.Status.ARCHIVED)
        self.assertEqual(result.teardown_attempts, 0)
        self.assertIsNone(result.teardown_claim_token)
        self.assertIsNone(result.teardown_claimed_at)
        self.assertIsNone(result.teardown_next_retry_at)
        self.assertEqual(result.teardown_state_json, {"intent": "delete_session"})
        with self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError):
            chat_lifecycle._update_chat_claim(
                result,
                str(teardown_token),
                "teardown_state_json",
            )
        queue_teardown.assert_called_once_with(self.session.id)

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_late_session_is_compensated_and_cannot_resurrect_chat(
        self,
        request_json,
        _queue_teardown,
    ):
        provision_token = uuid.uuid4()
        late_session_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.sl_session_uuid = None
        self.session.provision_claim_token = provision_token
        self.session.provision_claimed_at = timezone.now()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "sl_session_uuid",
                "provision_claim_token",
                "provision_claimed_at",
                "updated_at",
            ]
        )
        chat_lifecycle.request_copilot_chat_teardown(self.session)

        chat_lifecycle._compensate_late_session(
            self.session.id,
            late_session_uuid,
            user=self.user,
        )
        with self.assertRaises(chat_lifecycle.ChatProvisionLeaseLostError):
            chat_lifecycle._complete_copilot_chat_provision(
                link_id=self.session.id,
                claim_token=str(provision_token),
                knowledge_source_id=self.knowledge_source.id,
                assistant_uuid=self.knowledge_source.sl_assistant_uuid,
                session_uuid=late_session_uuid,
            )

        request_json.assert_called_once_with(
            "DELETE",
            f"/api/lens/sessions/{late_session_uuid}/",
            hfl_user=self.user,
        )
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertIsNone(self.session.sl_session_uuid)

    @mock.patch("apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error")
    def test_late_adopted_session_does_not_rollback_retried_chat(self, queue_teardown):
        session_uuid = self.session.sl_session_uuid
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.save(update_fields=["lifecycle_status", "updated_at"])

        chat_lifecycle._record_late_source_lens_resource(
            self.session.id,
            field="sl_session_uuid",
            resource_uuid=session_uuid,
            error="stale worker",
        )

        self.session.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING)
        self.assertEqual(self.session.sl_session_uuid, session_uuid)
        queue_teardown.assert_not_called()

    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_stale_worker_keeps_session_adopted_by_retry(self, request_json):
        session_uuid = self.session.sl_session_uuid
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.save(update_fields=["lifecycle_status", "updated_at"])

        chat_lifecycle._compensate_late_session(
            self.session.id, session_uuid, user=self.user
        )

        request_json.assert_not_called()

    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_stale_worker_journals_unadopted_session_without_deleting_chat(
        self, request_json
    ):
        late_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.save(update_fields=["lifecycle_status", "updated_at"])

        chat_lifecycle._compensate_late_session(
            self.session.id, late_uuid, user=self.user
        )

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING
        )
        self.assertIn(
            late_uuid,
            chat_lifecycle._late_remote_uuids(self.session, "session"),
        )
        request_json.assert_not_called()

    def test_retried_chat_adopts_late_session_without_stale_journal(self):
        token = uuid.uuid4()
        late_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.sl_session_uuid = None
        self.session.provision_claim_token = token
        self.session.save(
            update_fields=[
                "lifecycle_status", "sl_session_uuid",
                "provision_claim_token", "updated_at",
            ]
        )
        chat_lifecycle._prepare_remote_operation(
            self.session, str(token), kind=chat_lifecycle._SESSION_CREATE_OPERATION
        )
        chat_lifecycle._record_late_source_lens_resource(
            self.session.id,
            field="sl_session_uuid",
            resource_uuid=late_uuid,
            error="stale worker",
        )

        chat_lifecycle._record_remote_operation_resource(
            self.session,
            str(token),
            kind=chat_lifecycle._SESSION_CREATE_OPERATION,
            field="sl_session_uuid",
            remote_uuid=late_uuid,
        )

        self.session.refresh_from_db()
        self.assertEqual(self.session.sl_session_uuid, late_uuid)
        self.assertEqual(
            chat_lifecycle._late_remote_uuids(self.session, "session"),
            set(),
        )

    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    def test_stale_worker_keeps_assistant_adopted_by_retry(self, delete_assistant):
        assistant_uuid = self.session.sl_assistant_uuid
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.save(update_fields=["lifecycle_status", "updated_at"])

        chat_lifecycle._compensate_late_assistant(self.session.id, assistant_uuid)

        delete_assistant.assert_not_called()

    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    def test_deleted_source_chat_cannot_retire_a_shared_assistant(
        self, queue_teardown, delete_assistant
    ):
        assistant_uuid = self.session.sl_assistant_uuid
        LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            sl_assistant_uuid=assistant_uuid,
            sl_session_uuid=uuid.uuid4(),
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )
        self.knowledge_source.teardown_state_json = {
            "shared_chat_resources": True
        }
        self.knowledge_source.save(
            update_fields=["teardown_state_json", "updated_at"]
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETED
        self.session.knowledge_source = None
        self.session.sl_assistant_uuid = None
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "knowledge_source",
                "sl_assistant_uuid",
                "updated_at",
            ]
        )

        chat_lifecycle._compensate_late_assistant(self.session.id, assistant_uuid)
        chat_lifecycle._record_late_source_lens_resource(
            self.session.id,
            field="sl_assistant_uuid",
            resource_uuid=assistant_uuid,
            error="late worker response",
        )

        self.session.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.DELETED)
        self.assertIsNone(self.session.sl_assistant_uuid)
        delete_assistant.assert_not_called()
        queue_teardown.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    def test_failed_late_compensation_reopens_durable_teardown(
        self,
        request_json,
        queue_teardown,
    ):
        late_session_uuid = uuid.uuid4()
        transient = sl_client.LensBridgeError("temporarily unavailable")
        transient.status_code = 503
        request_json.side_effect = transient
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETED
        self.session.sl_session_uuid = None
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "sl_session_uuid",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            chat_lifecycle._compensate_late_session(
                self.session.id,
                late_session_uuid,
                user=self.user,
            )

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertEqual(self.session.sl_session_uuid, late_session_uuid)
        self.assertIsNone(self.session.provision_claim_token)
        queue_teardown.assert_called_once_with(self.session.id)

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed"
    )
    def test_manual_retry_recovers_stale_provision_claim(self, queue_provision):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = uuid.uuid4()
        self.session.provision_claimed_at = timezone.now() - timedelta(hours=3)
        self.session.provision_next_retry_at = timezone.now() + timedelta(hours=1)
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "provision_claimed_at",
                "provision_next_retry_at",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = chat_lifecycle.retry_copilot_chat_provision(self.session)

        result.refresh_from_db()
        self.assertEqual(
            result.lifecycle_status,
            LensSessionLink.LifecycleStatus.PROVISIONING,
        )
        self.assertIsNone(result.provision_claim_token)
        self.assertIsNone(result.provision_claimed_at)
        self.assertIsNone(result.provision_next_retry_at)
        queue_provision.assert_called_once_with(self.session.id)

    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_provision_task.delay"
    )
    def test_provision_reconciler_requeues_unclaimed_work(self, delay):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = None
        self.session.provision_claimed_at = None
        self.session.provision_next_retry_at = None
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "provision_claimed_at",
                "provision_next_retry_at",
                "updated_at",
            ]
        )

        result = reconcile_copilot_chat_provisions_task(limit=10)

        self.assertEqual(result["session_ids"], [self.session.id])
        delay.assert_called_once_with(
            session_link_id=self.session.id,
            expected_generation=self.session.provision_generation,
            expected_poll_sequence=self.session.provision_poll_sequence,
        )

    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_provision_task.delay"
    )
    def test_provision_reconciler_preserves_live_claim(self, delay):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = uuid.uuid4()
        self.session.provision_claimed_at = timezone.now()
        self.session.provision_next_retry_at = timezone.now()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "provision_claimed_at",
                "provision_next_retry_at",
                "updated_at",
            ]
        )

        result = reconcile_copilot_chat_provisions_task(limit=10)

        self.assertEqual(result["queued"], 0)
        delay.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_teardown_task.delay"
    )
    def test_teardown_reconciler_isolates_dispatch_failures(self, delay):
        second_session = LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
        )
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.teardown_next_retry_at = timezone.now()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )

        def enqueue(*, session_link_id):
            if session_link_id == self.session.id:
                raise ConnectionError("broker unavailable")

        delay.side_effect = enqueue

        result = reconcile_lens_resource_teardowns_task(limit=10)

        self.assertEqual(result["session_ids"], [second_session.id])
        self.assertEqual(
            result["failed"],
            [
                {
                    "resource": "session",
                    "id": self.session.id,
                    "error": "broker unavailable",
                }
            ],
        )
        self.assertEqual(delay.call_count, 2)

    @mock.patch(
        "apps.lens_bridge.tasks.knowledge_source_teardown."
        "execute_knowledge_source_teardown_task.delay"
    )
    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_teardown_task.delay"
    )
    def test_teardown_reconciler_requeues_legacy_intervention_state(
        self,
        chat_delay,
        ks_delay,
    ):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        self.session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        self.session.teardown_next_retry_at = None
        self.session.teardown_state_json = {
            "intent": "delete_session",
            "blocking": {"intervention_required": True},
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "teardown_next_retry_at",
                "teardown_state_json",
                "updated_at",
            ]
        )

        result = reconcile_lens_resource_teardowns_task(limit=10)

        self.assertEqual(result["queued"], 1)
        chat_delay.assert_called_once_with(session_link_id=self.session.id)
        ks_delay.assert_not_called()

    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    @mock.patch("apps.lens_bridge.services.assistant_access.soft_delete_assistant_link")
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    def test_failed_late_assistant_blocks_workspace_until_retry(
        self,
        delete_assistant,
        _soft_delete_assistant,
        run_agent_task,
    ):
        primary_uuid = self.session.sl_assistant_uuid
        late_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.sl_session_uuid = None
        self.session.provision_state_json = {
            "late_resources": [
                {
                    "kind": "assistant",
                    "remote_uuid": str(late_uuid),
                }
            ]
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "sl_session_uuid",
                "provision_state_json",
                "updated_at",
            ]
        )
        transient = sl_client.LensBridgeError("temporarily unavailable")
        transient.status_code = 503

        def delete_with_failure(assistant_uuid):
            if assistant_uuid == late_uuid:
                raise transient

        delete_assistant.side_effect = delete_with_failure

        with self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError):
            chat_lifecycle.run_copilot_chat_teardown(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        self.workspace_binding.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertEqual(self.session.sl_assistant_uuid, late_uuid)
        self.assertEqual(
            chat_lifecycle._late_remote_uuids(self.session, "assistant"),
            {late_uuid},
        )
        self.assertEqual(
            self.session.knowledge_source_id,
            self.knowledge_source.id,
        )
        self.assertIsNone(self.knowledge_source.sl_assistant_uuid)
        self.assertEqual(
            self.workspace_binding.state,
            LensWorkspaceBinding.State.READY,
        )
        self.assertIn(mock.call(primary_uuid), delete_assistant.call_args_list)
        self.assertIn(mock.call(late_uuid), delete_assistant.call_args_list)
        run_agent_task.assert_not_called()

        events: list[tuple[str, uuid.UUID | None]] = []
        delete_assistant.side_effect = lambda assistant_uuid: events.append(
            ("assistant", assistant_uuid)
        )
        run_agent_task.side_effect = lambda **_kwargs: (
            events.append(("workspace", None))
            or mock.MagicMock(
                ok=True,
                timed_out=False,
                task=mock.MagicMock(id=uuid.uuid4(), last_error=""),
            )
        )
        self.session.teardown_next_retry_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=["teardown_next_retry_at", "updated_at"])

        result = chat_lifecycle.run_copilot_chat_teardown(
            session_link_id=self.session.id
        )

        self.assertEqual(result["status"], "deleted")
        self.assertEqual(events[0], ("assistant", late_uuid))
        self.assertEqual(events[-1], ("workspace", None))

    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle._find_remote_uuid")
    def test_unknown_assistant_intent_blocks_workspace_cleanup(
        self,
        find_remote_uuid,
        delete_assistant,
        run_agent_task,
    ):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        self.session.sl_session_uuid = None
        self.session.sl_assistant_uuid = None
        self.session.provision_state_json = {
            "assistant_create": {
                "operation_id": str(uuid.uuid4()),
                "kind": "assistant_create",
                "lookup_key": "tenant-chat-ks-1",
                "remote_uuid": "",
                "status": "intent",
            }
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "sl_session_uuid",
                "sl_assistant_uuid",
                "provision_state_json",
                "updated_at",
            ]
        )
        find_remote_uuid.side_effect = sl_client.LensBridgeError(
            "SourceLens unavailable"
        )

        with self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError):
            chat_lifecycle.run_copilot_chat_teardown(session_link_id=self.session.id)

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertEqual(
            self.session.knowledge_source_id,
            self.knowledge_source.id,
        )
        self.assertIn(
            "recover_assistant_operation",
            self.session.lifecycle_error,
        )
        delete_assistant.assert_not_called()
        run_agent_task.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    def test_conflicting_late_uuid_is_preserved_in_journal(self, queue_teardown):
        original_uuid = self.session.sl_assistant_uuid
        late_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETED
        self.session.lifecycle_error_state_json = {
            "code": "SUBSCRIPTION.QUOTA_EXCEEDED",
            "meta": {"scope": "gateway"},
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "lifecycle_error_state_json",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            chat_lifecycle._record_late_source_lens_resource(
                self.session.id,
                field="sl_assistant_uuid",
                resource_uuid=late_uuid,
                error="late delete failed",
            )

        self.session.refresh_from_db()
        self.assertEqual(self.session.sl_assistant_uuid, original_uuid)
        self.assertEqual(
            chat_lifecycle._late_remote_uuids(self.session, "assistant"),
            {late_uuid},
        )
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertEqual(
            self.session.lifecycle_error_state_json["code"],
            "INSIGHT.CHAT_PREPARATION_FAILED",
        )
        queue_teardown.assert_called_once_with(self.session.id)

    @mock.patch(
        "apps.node.services.internal.node_workload.get_node_workload_blockers",
        return_value=[object()],
    )
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    def test_knowledge_source_teardown_deletes_assistant_before_workspace(
        self,
        run_agent_task,
        delete_assistant,
        gateway_workload_blockers,
    ):
        assistant_link = LensAssistantLink.objects.create(
            organization=self.tenant,
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            knowledge_source=self.knowledge_source,
            owner_user=self.user,
            created_by=self.user,
            visibility_scope=LensAssistantLink.VisibilityScope.USER,
        )
        outcome = mock.MagicMock(
            ok=True,
            timed_out=False,
            task=mock.MagicMock(id=uuid.uuid4(), last_error=""),
        )

        def agent_cleanup(**_kwargs):
            self.assertTrue(delete_assistant.called)
            return outcome

        run_agent_task.side_effect = agent_cleanup

        result = knowledge_source_teardown.run_knowledge_source_teardown(
            knowledge_source_id=self.knowledge_source.id,
            owner_session_link_id=self.session.id,
        )

        self.assertEqual(result["status"], "deleted")
        delete_assistant.assert_called_once_with(assistant_link.sl_assistant_uuid)
        assistant_link.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        self.workspace_binding.refresh_from_db()
        self.assertTrue(assistant_link.is_deleted)
        self.assertTrue(self.knowledge_source.is_deleted)
        self.assertEqual(
            self.workspace_binding.state,
            LensWorkspaceBinding.State.DELETED,
        )
        gateway_workload_blockers.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown."
        "sl_client.delete_managed_datasource"
    )
    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown."
        "sl_client.cancel_managed_datasource_conversion"
    )
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    def test_teardown_retries_clear_bindings_via_tombstoned_assistant_links(
        self,
        run_agent_task,
        delete_assistant,
        cancel_conversion,
        delete_datasource,
    ):
        """Archive-only first attempts must still unbind on later retries.

        SourceLens 0.57 keeps AssistantDataSourceBinding after archive. HFL soft
        deletes the assistant link when delete_assistants succeeds, so retries
        have to rediscover that UUID from tombstones before DELETE datasource.
        """

        datasource_uuid = uuid.uuid4()
        assistant_uuid = self.knowledge_source.sl_assistant_uuid
        self.knowledge_source.sl_datasource_uuid = datasource_uuid
        self.knowledge_source.sl_assistant_uuid = None
        self.knowledge_source.save(
            update_fields=["sl_datasource_uuid", "sl_assistant_uuid", "updated_at"]
        )
        link = LensAssistantLink.objects.create(
            organization=self.tenant,
            sl_assistant_uuid=assistant_uuid,
            knowledge_source=self.knowledge_source,
            owner_user=self.user,
            created_by=self.user,
            visibility_scope=LensAssistantLink.VisibilityScope.USER,
        )
        link.soft_delete()
        run_agent_task.return_value = mock.MagicMock(
            ok=True,
            timed_out=False,
            task=mock.MagicMock(id=uuid.uuid4(), last_error=""),
        )

        result = knowledge_source_teardown.run_knowledge_source_teardown(
            knowledge_source_id=self.knowledge_source.id,
            owner_session_link_id=self.session.id,
        )

        self.assertEqual(result["status"], "deleted")
        delete_assistant.assert_called_once_with(assistant_uuid)
        delete_datasource.assert_called_once_with(str(datasource_uuid))
        cancel_conversion.assert_called_once_with(str(datasource_uuid))

    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown."
        "sl_client.delete_managed_datasource"
    )
    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown."
        "sl_client.cancel_managed_datasource_conversion"
    )
    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    def test_teardown_cancels_conversion_before_deleting_remote_resources(
        self,
        run_agent_task,
        delete_assistant,
        cancel_conversion,
        delete_datasource,
    ):
        datasource_uuid = uuid.uuid4()
        self.knowledge_source.sl_datasource_uuid = datasource_uuid
        self.knowledge_source.save(update_fields=["sl_datasource_uuid", "updated_at"])
        LensAssistantLink.objects.create(
            organization=self.tenant,
            sl_assistant_uuid=self.knowledge_source.sl_assistant_uuid,
            knowledge_source=self.knowledge_source,
            owner_user=self.user,
            created_by=self.user,
            visibility_scope=LensAssistantLink.VisibilityScope.USER,
        )
        events = []
        cancel_conversion.side_effect = lambda *_args: events.append(
            "cancel_conversion"
        )
        delete_assistant.side_effect = lambda *_args: events.append("delete_assistant")
        delete_datasource.side_effect = lambda *_args: events.append(
            "delete_datasource"
        )
        run_agent_task.side_effect = lambda **_kwargs: (
            events.append("cleanup_workspace")
            or mock.MagicMock(
                ok=True,
                timed_out=False,
                task=mock.MagicMock(id=uuid.uuid4(), last_error=""),
            )
        )

        result = knowledge_source_teardown.run_knowledge_source_teardown(
            knowledge_source_id=self.knowledge_source.id,
            owner_session_link_id=self.session.id,
        )

        self.assertEqual(result["status"], "deleted")
        self.assertEqual(
            events,
            [
                "cancel_conversion",
                "delete_assistant",
                "delete_datasource",
                "cleanup_workspace",
            ],
        )

    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown."
        "managed_datasource.assess_conversion_stop",
        return_value=managed_datasource.ConversionStopAssessment(
            False,
            task_id="convert-1",
            remote_status="CANCELLING",
            reason="conversion_still_running",
        ),
    )
    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown."
        "sl_client.delete_managed_datasource"
    )
    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_teardown."
        "sl_client.cancel_managed_datasource_conversion"
    )
    def test_teardown_waits_for_lensnode_conversion_acknowledgement(
        self,
        cancel_conversion,
        delete_datasource,
        _conversion_stopped,
    ):
        self.knowledge_source.sl_datasource_uuid = uuid.uuid4()
        self.knowledge_source.sync_state_json = {"conversion": {"task_id": "convert-1"}}
        self.knowledge_source.save(
            update_fields=[
                "sl_datasource_uuid",
                "sync_state_json",
                "updated_at",
            ]
        )

        with self.assertRaises(
            knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError
        ):
            knowledge_source_teardown.run_knowledge_source_teardown(
                knowledge_source_id=self.knowledge_source.id,
                owner_session_link_id=self.session.id,
            )

        cancel_conversion.assert_called_once()
        delete_datasource.assert_not_called()
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.teardown_state_json["cancel_conversion"]["status"],
            "waiting",
        )

    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    def test_gateway_local_workspace_cleanup_never_deletes_source_path(
        self,
        run_agent_task,
    ):
        private_gateway = Node.objects.create(
            organization=self.tenant,
            name="private-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        private_link = LensGatewayLink.objects.create(
            organization=self.tenant,
            gateway=private_gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.tenant,
            name="Local directory",
            gateway=private_gateway,
            gateway_link=private_link,
            source_path="/workspace/user-data",
            status=LensKnowledgeSource.Status.READY,
            created_by=self.user,
        )
        binding = LensWorkspaceBinding.objects.create(
            organization=self.tenant,
            knowledge_source=knowledge_source,
            gateway_link=private_link,
            execution_organization_id=self.tenant.id,
            execution_node_id=private_gateway.id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.GATEWAY_LOCAL,
            workspace_root="/workspace",
            state=LensWorkspaceBinding.State.READY,
            identity_status=LensWorkspaceBinding.IdentityStatus.NOT_APPLICABLE,
        )

        result = knowledge_source_teardown.run_knowledge_source_teardown(
            knowledge_source_id=knowledge_source.id,
        )

        self.assertEqual(result["status"], "deleted")
        binding.refresh_from_db()
        self.assertEqual(binding.state, LensWorkspaceBinding.State.DELETED)
        run_agent_task.assert_not_called()


class CopilotChatTeardownConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_new_from_chat_and_source_delete_share_one_resource_fence(self):
        org = Organization.objects.create(
            key="shared-create-delete-concurrency",
            name="Shared Chat create/delete",
        )
        user = get_user_model().objects.create_user(
            username="shared-create-delete@example.test",
            email="shared-create-delete@example.test",
        )
        gateway = Node.objects.create(
            organization=org,
            name="shared-create-delete-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.USER,
            owner_user=user,
        )
        assistant_uuid = uuid.uuid4()
        ks = LensKnowledgeSource.objects.create(
            organization=org,
            gateway=gateway,
            gateway_link=gateway_link,
            name="Shared Chat data",
            source_path="/data",
            status=LensKnowledgeSource.Status.READY,
            sl_assistant_uuid=assistant_uuid,
            created_by=user,
        )
        assistant_access.ensure_assistant_link(
            org=org,
            sl_assistant_uuid=assistant_uuid,
            knowledge_source=ks,
            owner_user=user,
            created_by=user,
            visibility_scope=LensAssistantLink.VisibilityScope.USER,
            lifecycle_owner=LensAssistantLink.LifecycleOwner.CHAT,
        )
        source = LensSessionLink.objects.create(
            organization=org,
            hfl_user=user,
            gateway_link=gateway_link,
            knowledge_source=ks,
            sl_assistant_uuid=assistant_uuid,
            sl_session_uuid=uuid.uuid4(),
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )
        barrier = threading.Barrier(2)

        def run_create():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                try:
                    return chat_lifecycle.create_copilot_chat_from_existing(
                        org,
                        user=user,
                        source_session_id=source.id,
                        idempotency_key="raced-new-chat",
                    )
                except ValidationError:
                    return None
            finally:
                close_old_connections()

        def run_delete():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return chat_lifecycle.request_copilot_chat_teardown(source)
            finally:
                close_old_connections()

        with (
            mock.patch(
                "apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed"
            ),
            mock.patch(
                "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
            ),
            mock.patch(
                "apps.lens_bridge.services.chat_lifecycle.gateway_chat_queue.wake_gateway_queue"
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            created = executor.submit(run_create)
            deleted = executor.submit(run_delete)
            child = created.result(timeout=10)
            deleted.result(timeout=10)

        source.refresh_from_db()
        ks.refresh_from_db()
        self.assertEqual(
            source.lifecycle_status, LensSessionLink.LifecycleStatus.DELETING
        )
        if child is not None:
            self.assertEqual(child.knowledge_source_id, ks.id)
            self.assertEqual(
                child.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING
            )
            self.assertTrue(ks.teardown_state_json["shared_chat_resources"])
        else:
            self.assertFalse(
                LensSessionLink.objects.filter(
                    create_idempotency_key="raced-new-chat"
                ).exists()
            )

    def test_two_chat_workers_elect_exactly_one_resource_cleanup_owner(self):
        org = Organization.objects.create(
            key="shared-chat-concurrency", name="Shared Chat"
        )
        user = get_user_model().objects.create_user(
            username="shared-chat-concurrency@example.test",
            email="shared-chat-concurrency@example.test",
        )
        gateway = Node.objects.create(
            organization=org,
            name="shared-chat-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.USER,
            owner_user=user,
        )
        ks = LensKnowledgeSource.objects.create(
            organization=org,
            gateway=gateway,
            gateway_link=gateway_link,
            name="Shared Chat data",
            source_path="/data",
            status=LensKnowledgeSource.Status.READY,
            created_by=user,
        )
        sessions = [
            LensSessionLink.objects.create(
                organization=org,
                hfl_user=user,
                gateway_link=gateway_link,
                knowledge_source=ks,
                lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
                cleanup_status=LensSessionLink.CleanupStatus.RUNNING,
                teardown_claim_token=uuid.uuid4(),
            )
            for _ in range(2)
        ]
        barrier = threading.Barrier(2)

        def claim(session):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return chat_lifecycle._claim_shared_chat_resource_teardown(
                    knowledge_source=ks,
                    owner_session_link_id=session.id,
                    claim_token=str(session.teardown_claim_token),
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, sessions))

        self.assertEqual(sorted(results), [False, True])
        ks.refresh_from_db()
        self.assertIn(
            ks.teardown_state_json["chat_cleanup_owner_session_id"],
            [session.id for session in sessions],
        )
        self.assertEqual(
            LensSessionLink.objects.filter(
                knowledge_source=ks,
                lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
            ).count(),
            1,
        )

    def test_only_one_worker_claims_the_same_provision(self):
        organization = Organization.objects.create(
            key="provision-concurrency",
            name="Provision concurrency",
        )
        user = get_user_model().objects.create_user(
            username="provision-concurrency@example.test",
            email="provision-concurrency@example.test",
        )
        session = LensSessionLink.objects.create(
            organization=organization,
            hfl_user=user,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
        )
        barrier = threading.Barrier(2)

        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return chat_lifecycle._claim_copilot_chat_provision(session.id)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: claim(), range(2)))

        self.assertEqual(sum(1 for claimed, _status in results if claimed), 1)
        self.assertEqual(
            {status for claimed, status in results if not claimed},
            {"busy"},
        )

    def test_only_one_worker_claims_the_same_teardown(self):
        organization = Organization.objects.create(
            key="teardown-concurrency",
            name="Teardown concurrency",
        )
        user = get_user_model().objects.create_user(
            username="teardown-concurrency@example.test",
            email="teardown-concurrency@example.test",
        )
        session = LensSessionLink.objects.create(
            organization=organization,
            hfl_user=user,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
        )
        barrier = threading.Barrier(2)

        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return chat_lifecycle._claim_copilot_chat_teardown(session.id)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: claim(), range(2)))

        self.assertEqual(sum(1 for claimed, _status in results if claimed), 1)
        self.assertEqual(
            {status for claimed, status in results if not claimed},
            {"busy"},
        )
