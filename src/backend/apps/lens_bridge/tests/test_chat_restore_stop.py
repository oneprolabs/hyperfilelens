import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services.knowledge_source_teardown import (
    assess_chat_restore_stop,
)
from apps.node.models import Node, NodeTask
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.task.models import Task


class ChatRestoreStopAssessmentTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(
            key="chat-restore-stop-tenant",
            name="Chat Restore Stop Tenant",
        )
        self.execution_org = Organization.objects.create(
            key="chat-restore-stop-execution",
            name="Chat Restore Stop Execution",
        )
        self.user = get_user_model().objects.create_user(
            username="chat-restore-stop@example.test",
            email="chat-restore-stop@example.test",
        )
        self.gateway = Node.objects.create(
            organization=self.execution_org,
            name="chat-restore-stop-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.gateway_link = LensGatewayLink.objects.create(
            organization=self.execution_org,
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
            source_path="/data",
            status=LensKnowledgeSource.Status.SYNCING,
            created_by=self.user,
        )
        self.binding = LensWorkspaceBinding.objects.create(
            organization=self.tenant,
            knowledge_source=self.knowledge_source,
            gateway_link=self.gateway_link,
            execution_organization_id=self.execution_org.id,
            execution_node_id=self.gateway.id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
            workspace_root="/workspace/platform/data",
            relative_path="tenants/1/knowledge-sources/1",
            state=LensWorkspaceBinding.State.PREPARING,
            identity_status=LensWorkspaceBinding.IdentityStatus.READY,
        )

    def _create_restore(self, *, node_status: str, node_result=None):
        product_task = Task.objects.create(
            organization_id=self.tenant.id,
            task_type=Task.Type.INSIGHT_WORKSPACE_RESTORE,
            display_name="Insight workspace restore",
            status=Task.Status.CANCELLED,
        )
        record = RestoreRecord.objects.create(
            organization_id=self.tenant.id,
            requesting_organization_id=self.tenant.id,
            target_execution_organization_id=self.execution_org.id,
            target_execution_node_id=self.gateway.id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key=f"chat-restore-stop-{uuid.uuid4()}",
            workspace_binding_id=self.binding.id,
            restore_uid=f"rst-{uuid.uuid4().hex[:16]}",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=product_task.id,
            task_uuid=product_task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=1,
            source_snapshot_id=1,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=self.gateway.id,
            target_path=self.binding.resolved_path(),
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )
        node_task = NodeTask.objects.create(
            organization=self.execution_org,
            requesting_organization_id=self.tenant.id,
            node=self.gateway,
            correlation_type="restore.record",
            correlation_id=str(record.task_uuid),
            kind="restore.run",
            payload={
                "workspace_kind": "managed_restore",
                "workspace_uid": str(self.binding.workspace_uid),
                "managed_workspace_path": self.binding.resolved_path(),
                "path": self.binding.resolved_path(),
            },
            result=dict(node_result or {}),
            status=node_status,
            accepted_at=timezone.now(),
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
        )
        RestoreRecordItem.objects.create(
            organization_id=self.tenant.id,
            restore_record=record,
            source_snapshot_directory_id=1,
            backup_config_dir_id=1,
            repository_id=1,
            kopia_snapshot_id="snapshot-1",
            source_path="/data",
            selected_paths=["/data"],
            target_path=self.binding.resolved_path(),
            conflict_mode=RestoreRecordItem.ConflictMode.OVERWRITE,
            status=RestoreRecordItem.Status.CANCELLED,
            node_task_id=node_task.id,
        )
        self.knowledge_source.last_restore_record_id = record.id
        self.knowledge_source.save(
            update_fields=["last_restore_record_id", "updated_at"]
        )
        return record, node_task

    def test_no_dispatched_restore_is_safe_without_gateway_global_checks(self):
        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertTrue(assessment.confirmed)
        self.assertEqual(assessment.reason, "not_dispatched")

    def test_binding_restore_is_checked_when_canonical_pointer_is_missing(self):
        _, node_task = self._create_restore(
            node_status=NodeTask.Status.CANCELED,
            node_result={"cancel_finalized_by": "grace_timeout"},
        )
        self.knowledge_source.last_restore_record_id = None
        self.knowledge_source.save(
            update_fields=["last_restore_record_id", "updated_at"]
        )

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        self.assertEqual(assessment.reason, "restore_executor_still_stopping")
        self.assertEqual(assessment.node_task_ids, (str(node_task.id),))

    def test_newer_binding_restore_is_checked_when_pointer_is_stale(self):
        old_record, _ = self._create_restore(
            node_status=NodeTask.Status.SUCCESS,
        )
        newer_record, newer_node_task = self._create_restore(
            node_status=NodeTask.Status.CANCELED,
            node_result={"cancel_finalized_by": "grace_timeout"},
        )
        self.knowledge_source.last_restore_record_id = old_record.id
        self.knowledge_source.save(
            update_fields=["last_restore_record_id", "updated_at"]
        )

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        self.assertEqual(assessment.reason, "restore_executor_still_stopping")
        self.assertEqual(assessment.task_id, str(newer_record.task_uuid))
        self.assertEqual(assessment.node_task_ids, (str(newer_node_task.id),))

    @patch("apps.node.services.interface.cancel_agent_task")
    def test_older_active_restore_is_checked_when_pointer_is_newer(
        self,
        cancel_agent_task,
    ):
        old_record, old_node_task = self._create_restore(
            node_status=NodeTask.Status.RUNNING,
        )
        newer_record, _ = self._create_restore(
            node_status=NodeTask.Status.SUCCESS,
        )
        self.assertEqual(self.knowledge_source.last_restore_record_id, newer_record.id)

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        self.assertEqual(assessment.reason, "restore_executor_still_stopping")
        self.assertEqual(assessment.task_id, str(old_record.task_uuid))
        self.assertEqual(assessment.node_task_ids, (str(old_node_task.id),))
        cancel_agent_task.assert_called_once_with(
            task_id=old_node_task.id,
            reason="Chat deletion requested",
        )

    def test_manual_confirmations_cover_multiple_legacy_restore_records(self):
        old_record, _ = self._create_restore(
            node_status=NodeTask.Status.CANCELED,
            node_result={"cancel_finalized_by": "grace_timeout"},
        )
        newer_record, _ = self._create_restore(
            node_status=NodeTask.Status.CANCELED,
            node_result={"cancel_finalized_by": "grace_timeout"},
        )
        self.knowledge_source.last_restore_record_id = old_record.id
        self.knowledge_source.teardown_state_json = {
            "manual_restore_stop_confirmations": {
                str(old_record.task_uuid): {
                    "confirmed": True,
                    "task_id": str(old_record.task_uuid),
                },
                str(newer_record.task_uuid): {
                    "confirmed": True,
                    "task_id": str(newer_record.task_uuid),
                },
            }
        }
        self.knowledge_source.save(
            update_fields=[
                "last_restore_record_id",
                "teardown_state_json",
                "updated_at",
            ]
        )

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertTrue(assessment.confirmed)
        self.assertEqual(assessment.reason, "executor_stopped")

    def test_control_plane_cancel_does_not_prove_executor_exit(self):
        _, node_task = self._create_restore(
            node_status=NodeTask.Status.CANCELED,
            node_result={"cancel_finalized_by": "grace_timeout"},
        )

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        self.assertEqual(assessment.reason, "restore_executor_still_stopping")
        self.assertEqual(assessment.node_task_ids, (str(node_task.id),))

    @patch(
        "apps.restore.services.interface.cancel_restore",
        return_value={"status": "cancelled"},
    )
    def test_active_canonical_restore_receives_existing_cancel_request(
        self,
        cancel_restore,
    ):
        record, _ = self._create_restore(
            node_status=NodeTask.Status.RUNNING,
        )
        Task.objects.filter(pk=record.task_id).update(status=Task.Status.RUNNING)

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        cancel_restore.assert_called_once_with(
            organization_id=self.tenant.id,
            task_uuid=str(record.task_uuid),
            reason="Chat deletion requested",
        )

    @patch("apps.node.services.interface.cancel_agent_task")
    def test_terminal_product_restore_retries_active_executor_cancel(
        self,
        cancel_agent_task,
    ):
        _, node_task = self._create_restore(
            node_status=NodeTask.Status.RUNNING,
        )

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        self.assertEqual(assessment.reason, "restore_executor_still_stopping")
        cancel_agent_task.assert_called_once_with(
            task_id=node_task.id,
            reason="Chat deletion requested",
        )

    def test_agent_executor_stop_evidence_satisfies_restore_barrier(self):
        _, node_task = self._create_restore(
            node_status=NodeTask.Status.CANCELED,
            node_result={
                "executor_finished": True,
                "completion_source": "agent_executor",
            },
        )

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertTrue(assessment.confirmed)
        self.assertEqual(assessment.reason, "executor_stopped")
        self.assertEqual(assessment.node_task_ids, (str(node_task.id),))

    def test_node_task_workspace_identity_mismatch_fails_closed(self):
        _, node_task = self._create_restore(
            node_status=NodeTask.Status.SUCCESS,
        )
        node_task.payload = {
            **node_task.payload,
            "workspace_uid": str(uuid.uuid4()),
        }
        node_task.save(update_fields=["payload", "updated_at"])

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        self.assertEqual(
            assessment.reason,
            "restore_node_task_identity_mismatch",
        )

    def test_node_task_restore_identity_mismatch_fails_closed(self):
        _, node_task = self._create_restore(
            node_status=NodeTask.Status.SUCCESS,
        )
        node_task.correlation_id = str(uuid.uuid4())
        node_task.save(update_fields=["correlation_id", "updated_at"])

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        self.assertEqual(
            assessment.reason,
            "restore_node_task_identity_mismatch",
        )

    def test_canonical_restore_identity_mismatch_fails_closed(self):
        record, _ = self._create_restore(
            node_status=NodeTask.Status.SUCCESS,
        )
        record.workspace_binding_id = self.binding.id + 1000
        record.save(update_fields=["workspace_binding_id", "updated_at"])

        assessment = assess_chat_restore_stop(self.knowledge_source)

        self.assertFalse(assessment.confirmed)
        self.assertEqual(assessment.reason, "canonical_restore_mismatch")
