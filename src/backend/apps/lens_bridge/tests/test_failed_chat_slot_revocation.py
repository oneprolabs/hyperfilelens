"""Admission-only recovery for failed Chats with immediate SL revocations."""

from copy import deepcopy
from datetime import timedelta
from unittest import mock
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayChatSlot,
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
)
from apps.lens_bridge.services import (
    chat_lifecycle,
    gateway_chat_queue,
    managed_datasource,
    sl_client,
)
from apps.node.models import Node


class FailedChatSlotRevocationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="revoked-slot", name="Tenant")
        self.user = get_user_model().objects.create_user(
            username="revoked-slot@example.test",
            email="revoked-slot@example.test",
        )
        node = Node.objects.create(
            organization=self.org,
            name="gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.gateway = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=node,
            scope=LensGatewayLink.GatewayScope.ORGANIZATION,
            sl_lensnode_uuid=uuid.uuid4(),
            workspace_root="/workspace",
        )
        self.ks = LensKnowledgeSource.objects.create(
            organization=self.org,
            name="Retained Chat workspace",
            gateway=node,
            gateway_link=self.gateway,
            backup_source_snapshot_id=11,
            sl_datasource_uuid=uuid.uuid4(),
            sl_lensnode_uuid=self.gateway.sl_lensnode_uuid,
            sl_assistant_uuid=uuid.uuid4(),
            workspace_path_on_lensnode="/workspace/retained",
            status=LensKnowledgeSource.Status.ERROR,
            sync_state_json={
                "completed_phases": ["prepare_workspace", "restore_snapshot"],
                "conversion": {
                    "task_id": "revoked-1",
                    "status": "FAILURE",
                    "checkpoint": {"converted": 2},
                },
            },
            created_by=self.user,
        )
        self.session = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            gateway_link=self.gateway,
            knowledge_source=self.ks,
            sl_assistant_uuid=self.ks.sl_assistant_uuid,
            backup_source_snapshot_id=11,
            lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.RESERVED
            ),
        )
        self.slot = LensGatewayChatSlot.objects.create(
            gateway_link=self.gateway,
            session_link=self.session,
            slot_number=1,
            session_generation=self.session.provision_generation,
            acquired_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )
        self.task = {
            "task_id": "revoked-1",
            "status": "REVOKED",
            "metadata": {
                "stop_confirmation_source": "manual_immediate",
                "datasource_uuid": str(self.ks.sl_datasource_uuid),
                "lensnode_uuid": str(self.ks.sl_lensnode_uuid),
            },
        }
        patcher = mock.patch(
            "apps.lens_bridge.services.sl_client.get_task_by_id",
            side_effect=lambda _task_id: deepcopy(self.task),
        )
        self.get_task = patcher.start()
        self.addCleanup(patcher.stop)

    def _make_probe_due(self):
        self.session.refresh_from_db()
        journal = dict(self.session.provision_state_json)
        journal["failed_slot_stop_probe_after"] = (
            timezone.now() - timedelta(seconds=1)
        ).isoformat()
        self.session.provision_state_json = journal
        self.session.save(update_fields=["provision_state_json"])

    def _assert_retained(self):
        self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 0)
        self.assertTrue(LensGatewayChatSlot.objects.filter(pk=self.slot.pk).exists())

    def test_releases_only_admission_and_preserves_stop_barriers_and_resources(self):
        sync_state = deepcopy(self.ks.sync_state_json)
        teardown_state = deepcopy(self.session.teardown_state_json)
        with (
            mock.patch.object(sl_client, "cancel_managed_datasource_conversion") as cancel,
            mock.patch.object(sl_client, "delete_managed_datasource") as delete,
        ):
            self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 1)
        self.get_task.assert_called_once_with("revoked-1")
        cancel.assert_not_called()
        delete.assert_not_called()
        self.assertFalse(LensGatewayChatSlot.objects.filter(pk=self.slot.pk).exists())
        self.session.refresh_from_db()
        self.ks.refresh_from_db()
        self.assertEqual(self.session.lifecycle_status, LensSessionLink.LifecycleStatus.FAILED)
        self.assertEqual(self.session.knowledge_source_id, self.ks.id)
        self.assertEqual(self.session.teardown_state_json, teardown_state)
        self.assertEqual(self.ks.sync_state_json, sync_state)
        self.assertEqual(self.ks.workspace_path_on_lensnode, "/workspace/retained")
        record = self.session.provision_state_json["failed_prepare_slot_release"]
        self.assertEqual(record["task_id"], "revoked-1")
        self.assertEqual(record["session_generation"], self.slot.session_generation)
        self.assertEqual(record["reason"], "sl_manual_immediate_revocation")
        self.assertIs(record["executor_stop_confirmed"], False)
        self.assertNotIn("manual_stop_confirmation", self.ks.sync_state_json["conversion"])
        self.assertNotIn("prepare_slot_release_barrier", self.session.teardown_state_json)
        self.assertFalse(managed_datasource.conversion_stop_confirmed(self.ks))

    def test_release_wakes_waiter_that_can_acquire_slot(self):
        waiter = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            gateway_link=self.gateway,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.RESERVED
            ),
        )
        with (
            mock.patch.object(chat_lifecycle, "_queue_provision_or_mark_failed") as queue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 1)
        queue.assert_called_once_with(waiter.id)
        self.assertTrue(
            gateway_chat_queue.try_acquire_chat_prepare_slot(
                session_link_id=waiter.id,
                expected_generation=waiter.provision_generation,
            ).acquired
        )

    def test_repeated_scan_is_idempotent(self):
        self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 1)
        self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 0)
        self.assertEqual(self.get_task.call_count, 1)

    def test_other_remote_statuses_and_sources_do_not_use_exception(self):
        for field, values in (
            ("status", ["PENDING", "STARTED", "FAILURE", "CANCELLING"]),
            ("stop_confirmation_source", [
                None, "", "cancel_grace_expired", "lensnode_active_operations_absent",
            ]),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    original = deepcopy(self.task)
                    if field == "status":
                        self.task[field] = value
                    else:
                        self.task["metadata"][field] = value
                    self._make_probe_due()
                    self._assert_retained()
                    self.task = original

    def test_missing_or_mismatched_remote_identity_does_not_release(self):
        for field in ("task_id", "datasource_uuid", "lensnode_uuid"):
            for value in (None, "unrelated"):
                with self.subTest(field=field, value=value):
                    original = deepcopy(self.task)
                    target = self.task if field == "task_id" else self.task["metadata"]
                    target[field] = value
                    self._make_probe_due()
                    self._assert_retained()
                    self.task = original
        self.task["metadata"] = None
        self._make_probe_due()
        self._assert_retained()

    def test_missing_local_identity_does_not_release(self):
        for field in ("sl_datasource_uuid", "sl_lensnode_uuid"):
            with self.subTest(field=field):
                original = getattr(self.ks, field)
                setattr(self.ks, field, None)
                self.ks.save(update_fields=[field])
                self._make_probe_due()
                self._assert_retained()
                setattr(self.ks, field, original)
                self.ks.save(update_fields=[field])

    def test_missing_task_or_unavailable_sl_does_not_release(self):
        self.get_task.side_effect = None
        for response in (None, [], {}):
            with self.subTest(response=response):
                self.get_task.return_value = response
                self._make_probe_due()
                self._assert_retained()
        self.get_task.side_effect = sl_client.LensBridgeUnavailable("Unavailable")
        self._make_probe_due()
        self._assert_retained()

    def test_deleting_and_other_nonfailed_chats_do_not_release(self):
        for status in (
            LensSessionLink.LifecycleStatus.DELETING,
            LensSessionLink.LifecycleStatus.PROVISIONING,
            LensSessionLink.LifecycleStatus.READY,
        ):
            with self.subTest(status=status):
                LensSessionLink.objects.filter(pk=self.session.pk).update(
                    lifecycle_status=status,
                )
                self._assert_retained()
        self.get_task.assert_not_called()

    def test_cleanup_states_and_intents_do_not_use_exception(self):
        for status in (
            LensSessionLink.CleanupStatus.PENDING,
            LensSessionLink.CleanupStatus.RUNNING,
            LensSessionLink.CleanupStatus.BLOCKED,
        ):
            with self.subTest(status=status):
                LensSessionLink.objects.filter(pk=self.session.pk).update(
                    cleanup_status=status,
                )
                self._make_probe_due()
                self._assert_retained()
        for intent in (
            LensSessionLink.CleanupIntent.DELETE_SESSION,
            LensSessionLink.CleanupIntent.RESET_FOR_RETRY,
        ):
            with self.subTest(intent=intent):
                LensSessionLink.objects.filter(pk=self.session.pk).update(
                    cleanup_status=LensSessionLink.CleanupStatus.NONE,
                    cleanup_intent=intent,
                )
                self._make_probe_due()
                self._assert_retained()

    def test_deleting_knowledge_source_and_reused_resources_do_not_release(self):
        self.ks.lifecycle_status = LensKnowledgeSource.LifecycleStatus.DELETING
        self.ks.save(update_fields=["lifecycle_status"])
        self._assert_retained()
        self.ks.lifecycle_status = LensKnowledgeSource.LifecycleStatus.READY
        self.ks.save(update_fields=["lifecycle_status"])
        self.session.refresh_from_db()
        self.session.provision_state_json["reuse_existing_resources"] = True
        self.session.save(update_fields=["provision_state_json"])
        self._make_probe_due()
        self._assert_retained()

    def test_cleanup_started_during_query_fences_release(self):
        for target in ("session_cleanup", "knowledge_source_cleanup"):
            with self.subTest(target=target):
                def start_cleanup(_task_id):
                    if target == "session_cleanup":
                        LensSessionLink.objects.filter(pk=self.session.pk).update(
                            cleanup_status=LensSessionLink.CleanupStatus.PENDING,
                        )
                    else:
                        LensKnowledgeSource.objects.filter(pk=self.ks.pk).update(
                            lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
                        )
                    return deepcopy(self.task)

                self.get_task.side_effect = start_cleanup
                self._assert_retained()
                LensSessionLink.objects.filter(pk=self.session.pk).update(
                    cleanup_status=LensSessionLink.CleanupStatus.NONE,
                )
                LensKnowledgeSource.objects.filter(pk=self.ks.pk).update(
                    lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY,
                )
                self._make_probe_due()

    def test_workspace_owner_mismatch_does_not_release(self):
        other_org = Organization.objects.create(key="unrelated-tenant", name="Other")
        self.ks.organization = other_org
        self.ks.save(update_fields=["organization"])
        self._assert_retained()

    def test_retry_or_delete_during_remote_query_fences_release(self):
        for status in (
            LensSessionLink.LifecycleStatus.PROVISIONING,
            LensSessionLink.LifecycleStatus.DELETING,
        ):
            with self.subTest(status=status):
                def change_lifecycle(_task_id):
                    LensSessionLink.objects.filter(pk=self.session.pk).update(
                        lifecycle_status=status,
                    )
                    return deepcopy(self.task)

                self.get_task.side_effect = change_lifecycle
                self._assert_retained()
                LensSessionLink.objects.filter(pk=self.session.pk).update(
                    lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
                )
                self._make_probe_due()

    def test_retry_then_failure_during_query_fences_by_both_tokens(self):
        for field in ("provision_generation", "provision_poll_sequence"):
            with self.subTest(field=field):
                def change_token(_task_id):
                    self.session.refresh_from_db()
                    value = getattr(self.session, field) + 1
                    LensSessionLink.objects.filter(pk=self.session.pk).update(
                        **{field: value},
                    )
                    if field == "provision_generation":
                        LensGatewayChatSlot.objects.filter(pk=self.slot.pk).update(
                            session_generation=value,
                        )
                    return deepcopy(self.task)

                self.get_task.side_effect = change_token
                self._assert_retained()
                self._make_probe_due()

    def test_task_or_datasource_changes_during_query_fence_release(self):
        for field in ("task_id", "sl_datasource_uuid", "sl_lensnode_uuid"):
            with self.subTest(field=field):
                def change_identity(_task_id):
                    if field == "task_id":
                        state = deepcopy(self.ks.sync_state_json)
                        state["conversion"]["task_id"] = "new-conversion"
                        LensKnowledgeSource.objects.filter(pk=self.ks.pk).update(
                            sync_state_json=state,
                        )
                    else:
                        LensKnowledgeSource.objects.filter(pk=self.ks.pk).update(
                            **{field: uuid.uuid4()},
                        )
                    return deepcopy(self.task)

                self.get_task.side_effect = change_identity
                self._assert_retained()
                self.ks.save(update_fields=[
                    "sync_state_json", "sl_datasource_uuid", "sl_lensnode_uuid",
                ])
                self._make_probe_due()

    def test_replacement_slot_in_same_generation_is_not_released(self):
        def replace_slot(_task_id):
            LensGatewayChatSlot.objects.filter(pk=self.slot.pk).delete()
            self.replacement = LensGatewayChatSlot.objects.create(
                gateway_link=self.gateway,
                session_link=self.session,
                slot_number=1,
                session_generation=self.session.provision_generation,
                acquired_at=timezone.now(),
                heartbeat_at=timezone.now(),
            )
            return deepcopy(self.task)

        self.get_task.side_effect = replace_slot
        self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 0)
        self.assertTrue(
            LensGatewayChatSlot.objects.filter(pk=self.replacement.pk).exists()
        )

    def test_retry_after_release_retains_checkpoint_and_requeues(self):
        self.assertEqual(chat_lifecycle.release_stopped_failed_chat_slots(), 1)
        with (
            mock.patch.object(chat_lifecycle, "_acquire_chat_snapshot_usage"),
            mock.patch.object(chat_lifecycle, "_queue_provision_or_mark_failed") as queue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            updated = chat_lifecycle.retry_copilot_chat_provision(self.session)
        queue.assert_called_once_with(self.session.pk)
        self.assertEqual(updated.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING)
        self.assertEqual(updated.provision_generation, self.slot.session_generation + 1)
        self.assertEqual(updated.knowledge_source_id, self.ks.pk)
        self.ks.refresh_from_db()
        conversion = self.ks.sync_state_json["conversion"]
        self.assertEqual(conversion["task_id"], "revoked-1")
        self.assertEqual(conversion["checkpoint"], {"converted": 2})
        self.assertIs(conversion["manual_retry_pending"], True)
        self.assertFalse(managed_datasource.conversion_stop_confirmed(self.ks))
