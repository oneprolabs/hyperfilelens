from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.iam.models import Organization
from apps.lens_bridge.management.commands.resume_blocked_chat_cleanup import Command
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
)
from apps.node.models import Node


class ResumeBlockedChatCleanupCommandTests(SimpleTestCase):
    def _options(self, **overrides):
        options = {
            "session_id": 42,
            "source_lens_task_id": "convert-1",
            "reason": "LensNode executor stop confirmed by the operator.",
            "confirm_executor_stopped": True,
        }
        options.update(overrides)
        return options

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id"
    )
    def test_requires_explicit_executor_stop_confirmation(self, get_task):
        with self.assertRaisesRegex(CommandError, "confirm-executor-stopped"):
            Command().handle(
                **self._options(confirm_executor_stopped=False),
            )

        get_task.assert_not_called()

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value=None,
    )
    def test_rejects_a_missing_source_lens_task(self, get_task):
        with self.assertRaisesRegex(CommandError, "was not found"):
            Command().handle(**self._options())

        get_task.assert_called_once_with("convert-1")

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"status": "REVOKED"},
    )
    def test_requires_source_lens_to_return_the_exact_task_identity(self, get_task):
        with self.assertRaisesRegex(CommandError, "exact requested task identity"):
            Command().handle(**self._options())

        get_task.assert_called_once_with("convert-1")

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"task_id": "convert-1", "status": "STARTED"},
    )
    def test_rejects_a_nonterminal_source_lens_task(self, get_task):
        with self.assertRaisesRegex(CommandError, "not terminal"):
            Command().handle(**self._options())

        get_task.assert_called_once_with("convert-1")


class ResumeBlockedChatCleanupDatabaseTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(
            key="cleanup-recovery-tenant",
            name="Cleanup recovery tenant",
        )
        self.platform_org = Organization.objects.create(
            key="cleanup-recovery-platform",
            name="Cleanup recovery platform",
        )
        self.user = get_user_model().objects.create_user(
            username="cleanup-recovery@example.test",
            email="cleanup-recovery@example.test",
        )
        self.gateway = Node.objects.create(
            organization=self.platform_org,
            name="cleanup-recovery-gateway",
            role=Node.Role.GATEWAY,
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
            name="Blocked workspace",
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            backup_source_snapshot_id=11,
            source_path="/data",
            workspace_path_on_lensnode="/workspace/platform/data/blocked",
            created_by=self.user,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
            sync_state_json={
                "conversion": {"task_id": "convert-1", "status": "REVOKED"}
            },
            teardown_state_json={
                "blocking": {
                    "intervention_required": True,
                    "task_id": "convert-1",
                }
            },
            teardown_attempts=99,
        )
        self.session = LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
            cleanup_intent=LensSessionLink.CleanupIntent.DELETE_SESSION,
            cleanup_status=LensSessionLink.CleanupStatus.BLOCKED,
            teardown_state_json={
                "intent": "delete_session",
                "blocking": {
                    "intervention_required": True,
                    "task_id": "convert-1",
                },
            },
            teardown_attempts=99,
        )

    @patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"task_id": "convert-1", "status": "REVOKED"},
    )
    def test_resume_locks_nullable_relationships_separately_and_requeues(
        self,
        _get_task,
        queue_teardown,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            Command().handle(
                session_id=self.session.id,
                source_lens_task_id="convert-1",
                reason="Executor stop verified from the gateway.",
                confirm_executor_stopped=True,
            )

        self.session.refresh_from_db()
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.session.cleanup_status,
            LensSessionLink.CleanupStatus.PENDING,
        )
        self.assertEqual(self.session.teardown_attempts, 0)
        self.assertNotIn("blocking", self.session.teardown_state_json)
        self.assertEqual(self.knowledge_source.teardown_attempts, 0)
        self.assertNotIn(
            "blocking",
            self.knowledge_source.teardown_state_json,
        )
        self.assertTrue(
            self.knowledge_source.sync_state_json["conversion"][
                "manual_stop_confirmation"
            ]["confirmed"]
        )
        queue_teardown.assert_called_once_with(self.session.id)

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"task_id": "different-task", "status": "REVOKED"},
    )
    def test_resume_rejects_a_different_remote_task(self, _get_task):
        with self.assertRaisesRegex(CommandError, "exact requested task identity"):
            Command().handle(
                session_id=self.session.id,
                source_lens_task_id="convert-1",
                reason="Executor stop verified from the gateway.",
                confirm_executor_stopped=True,
            )

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"task_id": "convert-1", "status": "REVOKED"},
    )
    def test_resume_rejects_task_mismatched_with_blocking_record(self, _get_task):
        self.session.teardown_state_json = {
            "intent": "delete_session",
            "blocking": {
                "reason": "conversion_stop_unconfirmed",
                "task_id": "different-task",
                "intervention_required": True,
            },
        }
        self.session.save(
            update_fields=["teardown_state_json", "updated_at"]
        )

        with self.assertRaisesRegex(CommandError, "recorded blocking condition"):
            Command().handle(
                session_id=self.session.id,
                source_lens_task_id="convert-1",
                reason="Executor stop verified from the gateway.",
                confirm_executor_stopped=True,
            )

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"task_id": "convert-1", "status": "REVOKED"},
    )
    def test_nonconversion_block_rejects_an_unrelated_task(self, _get_task):
        nonconversion_blocking = {
            "reason": "cleanup_workspace",
            "intervention_required": True,
        }
        self.session.teardown_state_json = {
            "intent": "delete_session",
            "blocking": nonconversion_blocking,
        }
        self.session.save(
            update_fields=["teardown_state_json", "updated_at"]
        )
        self.knowledge_source.teardown_state_json = {
            "blocking": nonconversion_blocking,
        }
        self.knowledge_source.save(
            update_fields=["teardown_state_json", "updated_at"]
        )

        with self.assertRaisesRegex(CommandError, "not blocked"):
            Command().handle(
                session_id=self.session.id,
                source_lens_task_id="convert-1",
                reason="Retrying a non-conversion cleanup.",
                confirm_executor_stopped=True,
            )

    @patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id"
    )
    def test_resume_nonconversion_cleanup_without_a_knowledge_source(
        self,
        get_task,
        queue_teardown,
    ):
        session = LensSessionLink.objects.create(
            organization=self.tenant,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
            cleanup_intent=LensSessionLink.CleanupIntent.DELETE_SESSION,
            cleanup_status=LensSessionLink.CleanupStatus.BLOCKED,
            teardown_state_json={
                "intent": "delete_session",
                "blocking": {
                    "reason": "revoke_shares: temporary failure",
                    "intervention_required": True,
                },
            },
            teardown_attempts=99,
        )

        with self.captureOnCommitCallbacks(execute=True):
            Command().handle(
                session_id=session.id,
                source_lens_task_id="",
                reason="The sharing service has recovered.",
                confirm_executor_stopped=False,
                confirm_retry=True,
            )

        session.refresh_from_db()
        self.assertEqual(
            session.cleanup_status,
            LensSessionLink.CleanupStatus.PENDING,
        )
        self.assertEqual(session.teardown_attempts, 0)
        self.assertNotIn("blocking", session.teardown_state_json)
        self.assertIn(
            "manual_cleanup_confirmation",
            session.teardown_state_json,
        )
        get_task.assert_not_called()
        queue_teardown.assert_called_once_with(session.id)

    def test_nonconversion_confirmation_cannot_bypass_conversion_fence(self):
        with self.assertRaisesRegex(CommandError, "Conversion cleanup requires"):
            Command().handle(
                session_id=self.session.id,
                source_lens_task_id="",
                reason="Retry requested without conversion proof.",
                confirm_executor_stopped=False,
                confirm_retry=True,
            )

    def test_chat_recovery_does_not_clear_knowledge_source_conversion_fence(self):
        self.session.teardown_state_json = {
            "intent": "delete_session",
            "blocking": {
                "reason": "delete_session",
                "intervention_required": True,
            },
        }
        self.session.save(
            update_fields=["teardown_state_json", "updated_at"]
        )

        with self.assertRaisesRegex(CommandError, "Conversion cleanup requires"):
            Command().handle(
                session_id=self.session.id,
                source_lens_task_id="",
                reason="Retry requested for the session cleanup.",
                confirm_executor_stopped=False,
                confirm_retry=True,
            )

        self.knowledge_source.refresh_from_db()
        self.assertIn("blocking", self.knowledge_source.teardown_state_json)

    @patch(
        "apps.lens_bridge.services.knowledge_source_teardown._queue_teardown"
    )
    def test_resume_orphan_knowledge_source_cleanup(self, queue_teardown):
        orphan = LensKnowledgeSource.objects.create(
            organization=self.tenant,
            name="Orphaned blocked workspace",
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            source_path="/data/orphaned",
            created_by=self.user,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
            teardown_state_json={
                "blocking": {
                    "reason": "cleanup_workspace",
                    "intervention_required": True,
                }
            },
            teardown_attempts=99,
        )

        with self.captureOnCommitCallbacks(execute=True):
            Command().handle(
                knowledge_source_id=orphan.id,
                source_lens_task_id="",
                reason="The Data Gateway filesystem was repaired.",
                confirm_executor_stopped=False,
                confirm_retry=True,
            )

        orphan.refresh_from_db()
        self.assertEqual(orphan.teardown_attempts, 0)
        self.assertNotIn("blocking", orphan.teardown_state_json)
        self.assertIn(
            "manual_cleanup_confirmation",
            orphan.teardown_state_json,
        )
        queue_teardown.assert_called_once_with(orphan.id)

    def test_knowledge_source_target_rejects_an_attached_chat(self):
        self.knowledge_source.teardown_state_json = {
            "blocking": {
                "reason": "cleanup_workspace",
                "intervention_required": True,
            }
        }
        self.knowledge_source.save(
            update_fields=["teardown_state_json", "updated_at"]
        )

        with self.assertRaisesRegex(CommandError, "still belongs to a Chat"):
            Command().handle(
                knowledge_source_id=self.knowledge_source.id,
                source_lens_task_id="",
                reason="Retry requested for the attached workspace.",
                confirm_executor_stopped=False,
                confirm_retry=True,
            )

    def test_orphan_knowledge_source_cannot_bypass_conversion_fence(self):
        orphan = LensKnowledgeSource.objects.create(
            organization=self.tenant,
            name="Orphaned conversion workspace",
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            source_path="/data/orphaned-conversion",
            created_by=self.user,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
            sync_state_json={
                "conversion": {"task_id": "orphan-convert-1"}
            },
            teardown_state_json={
                "blocking": {
                    "reason": "conversion_stop_unconfirmed",
                    "task_id": "orphan-convert-1",
                    "intervention_required": True,
                }
            },
        )

        with self.assertRaisesRegex(CommandError, "Conversion cleanup requires"):
            Command().handle(
                knowledge_source_id=orphan.id,
                source_lens_task_id="",
                reason="Retry requested without conversion proof.",
                confirm_executor_stopped=False,
                confirm_retry=True,
            )

    @patch(
        "apps.lens_bridge.services.knowledge_source_teardown._queue_teardown"
    )
    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"task_id": "orphan-convert-1", "status": "REVOKED"},
    )
    def test_resume_orphan_conversion_cleanup(
        self,
        _get_task,
        queue_teardown,
    ):
        orphan = LensKnowledgeSource.objects.create(
            organization=self.tenant,
            name="Orphaned conversion recovery",
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            source_path="/data/orphaned-conversion-recovery",
            created_by=self.user,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
            sync_state_json={
                "conversion": {"task_id": "orphan-convert-1"}
            },
            teardown_state_json={
                "blocking": {
                    "reason": "conversion_stop_unconfirmed",
                    "task_id": "orphan-convert-1",
                    "intervention_required": True,
                }
            },
            teardown_attempts=99,
        )

        with self.captureOnCommitCallbacks(execute=True):
            Command().handle(
                knowledge_source_id=orphan.id,
                source_lens_task_id="orphan-convert-1",
                reason="Executor stop verified from the gateway.",
                confirm_executor_stopped=True,
                confirm_retry=False,
            )

        orphan.refresh_from_db()
        self.assertEqual(orphan.teardown_attempts, 0)
        self.assertNotIn("blocking", orphan.teardown_state_json)
        self.assertTrue(
            orphan.sync_state_json["conversion"][
                "manual_stop_confirmation"
            ]["confirmed"]
        )
        queue_teardown.assert_called_once_with(orphan.id)
