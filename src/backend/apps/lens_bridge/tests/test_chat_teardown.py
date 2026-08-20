import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensAssistantLink,
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import (
    assistant_access,
    chat_lifecycle,
    knowledge_source_teardown,
    sl_client,
)
from apps.lens_bridge.tasks.chat_lifecycle import (
    reconcile_copilot_chat_provisions_task,
    reconcile_lens_resource_teardowns_task,
)
from apps.node.models import Node


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
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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
        self.knowledge_source.save(
            update_fields=["sl_assistant_uuid", "updated_at"]
        )

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
            chat_lifecycle.run_copilot_chat_teardown(
                session_link_id=self.session.id
            )

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
            LensKnowledgeSource.all_objects.filter(
                pk=self.knowledge_source.id
            ).update(
                teardown_state_json={
                    "cancel_conversion": {"status": "waiting"}
                }
            )
            raise knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError(
                "Waiting for LensNode to stop document conversion."
            )

        with mock.patch(
            "apps.lens_bridge.services.knowledge_source_teardown."
            "run_knowledge_source_teardown",
            side_effect=block_knowledge_source_cleanup,
        ), self.assertRaises(chat_lifecycle.ChatTeardownIncompleteError):
            chat_lifecycle.run_copilot_chat_teardown(
                session_link_id=self.session.id
            )

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

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_teardown_or_record_error"
    )
    def test_failed_provision_records_reset_for_retry_intent(self, queue_teardown):
        claim_token = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        self.session.provision_claim_token = claim_token
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_claim_token",
                "updated_at",
            ]
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
        self.assertEqual(self.session.status, LensSessionLink.Status.ACTIVE)
        queue_teardown.assert_called_once_with(self.session.id)

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
    @mock.patch(
        "apps.lens_bridge.services.gateway_execution.context_for_gateway_link"
    )
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

    @mock.patch(
        "apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown"
    )
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

    @mock.patch(
        "apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown"
    )
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

    @mock.patch(
        "apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown"
    )
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

    @mock.patch(
        "apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown"
    )
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

    @mock.patch(
        "apps.lens_bridge.services.sync_queue.queue_knowledge_source_teardown"
    )
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
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_teardown_or_record_error"
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._cleanup_failed_provision",
        return_value=["assistant_create: remote create outcome is unknown"],
    )
    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle._run_copilot_chat_provision",
        side_effect=RuntimeError("provision failed"),
    )
    def test_failed_provision_with_incomplete_compensation_starts_recovery(
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

        with mock.patch(
            "apps.lens_bridge.services.chat_lifecycle."
            "_claim_copilot_chat_provision",
            return_value=(str(claim_token), "claimed"),
        ), self.captureOnCommitCallbacks(execute=True), self.assertRaisesRegex(
            RuntimeError,
            "provision failed",
        ):
            chat_lifecycle.run_copilot_chat_provision(
                session_link_id=self.session.id
            )

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.lifecycle_status,
            LensSessionLink.LifecycleStatus.FAILED,
        )
        self.assertEqual(
            self.session.provision_phase,
            LensSessionLink.ProvisionPhase.CLEANING_UP,
        )
        self.assertIsNone(self.session.provision_claim_token)
        self.assertEqual(
            self.session.cleanup_status,
            LensSessionLink.CleanupStatus.PENDING,
        )
        queue_teardown.assert_called_once_with(self.session.id)

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
        self.assertTrue(
            second_slug.endswith(f"-ks-{second_knowledge_source.id}")
        )
        self.assertNotEqual(first_slug, second_slug)

    @mock.patch("apps.lens_bridge.services.chat_lifecycle._grant_assistant_to_chat_user")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.assistant_access.ensure_assistant_link")
    @mock.patch(
        "apps.lens_bridge.services.gateway_readiness.agent_ws_routable",
        return_value=True,
    )
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.sl_client.request_json")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle._find_remote_uuid")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.chat_user_provisioning.ensure_sl_chat_user")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.knowledge_source_sync.run_knowledge_source_sync")
    @mock.patch("apps.lens_bridge.services.chat_lifecycle.provisioning.create_sl_assistant_for_ks")
    def test_journal_recovers_remote_creates_without_reposting(
        self,
        create_assistant,
        run_sync,
        ensure_sl_user,
        find_remote_uuid,
        request_json,
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
        self.knowledge_source.save(
            update_fields=["sl_assistant_uuid", "updated_at"]
        )
        self.gateway_link.sl_lensnode_uuid = uuid.uuid4()
        self.gateway_link.sidecar_status = LensGatewayLink.SidecarStatus.ONLINE
        self.gateway_link.save(
            update_fields=["sl_lensnode_uuid", "sidecar_status", "updated_at"]
        )
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
            any(call.args[:2] == ("POST", "/api/lens/sessions/") for call in request_json.call_args_list)
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
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_teardown_task.delay"
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
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_teardown_task.delay"
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
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_teardown_or_record_error"
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
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_teardown_or_record_error"
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
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_teardown_or_record_error"
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
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_teardown_or_record_error"
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

    @mock.patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_teardown_or_record_error"
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
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_provision_or_mark_failed"
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
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_teardown_task.delay"
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
            chat_lifecycle.run_copilot_chat_teardown(
                session_link_id=self.session.id
            )

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
        self.session.save(
            update_fields=["teardown_next_retry_at", "updated_at"]
        )

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
            chat_lifecycle.run_copilot_chat_teardown(
                session_link_id=self.session.id
            )

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
        "apps.lens_bridge.services.chat_lifecycle."
        "_queue_teardown_or_record_error"
    )
    def test_conflicting_late_uuid_is_preserved_in_journal(self, queue_teardown):
        original_uuid = self.session.sl_assistant_uuid
        late_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.DELETED
        self.session.save(
            update_fields=["lifecycle_status", "updated_at"]
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
        queue_teardown.assert_called_once_with(self.session.id)

    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    def test_knowledge_source_teardown_deletes_assistant_before_workspace(
        self,
        run_agent_task,
        delete_assistant,
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
        self.knowledge_source.save(
            update_fields=["sl_datasource_uuid", "updated_at"]
        )
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
        delete_assistant.side_effect = lambda *_args: events.append(
            "delete_assistant"
        )
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
        "managed_datasource.conversion_stop_confirmed",
        return_value=False,
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
        self.knowledge_source.sync_state_json = {
            "conversion": {"task_id": "convert-1"}
        }
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
            self.knowledge_source.teardown_state_json["cancel_conversion"][
                "status"
            ],
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
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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
