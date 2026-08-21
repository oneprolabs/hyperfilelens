import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayChatSlot,
    LensGatewayLink,
    LensSessionLink,
)
from apps.lens_bridge.services import chat_lifecycle, gateway_chat_queue
from apps.node.models import Node
from common.errors import AppError


class GatewayChatQueueTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="gateway-chat-queue",
            name="Gateway Chat Queue",
        )
        self.user = get_user_model().objects.create_user(
            username="gateway-chat-queue@example.test",
            email="gateway-chat-queue@example.test",
        )
        gateway = Node.objects.create(
            organization=self.organization,
            name="queue-gateway",
            role=Node.Role.GATEWAY,
        )
        self.gateway_link = LensGatewayLink.objects.create(
            organization=self.organization,
            gateway=gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
            chat_prepare_concurrency=1,
            chat_queue_capacity=1,
        )

    def _session(self, *, gateway_link=None):
        return LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            gateway_link=gateway_link or self.gateway_link,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_phase=LensSessionLink.ProvisionPhase.QUEUED,
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.RESERVED
            ),
            gateway_queue_entered_at=timezone.now(),
        )

    def test_same_gateway_is_fifo_and_reports_running_chat_ahead(self):
        first = self._session()
        second = self._session()

        acquired = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=first.id,
            expected_generation=first.provision_generation,
        )
        waiting = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=second.id,
            expected_generation=second.provision_generation,
        )

        self.assertTrue(acquired.acquired)
        self.assertFalse(waiting.acquired)
        self.assertEqual(waiting.position, 1)
        self.assertEqual(gateway_chat_queue.chat_queue_ahead(session=second), 1)

    def test_queue_capacity_rejects_without_creating_more_work(self):
        first = self._session()
        self._session()
        gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=first.id,
            expected_generation=first.provision_generation,
        )

        with self.assertRaises(AppError) as raised:
            gateway_chat_queue.assert_chat_queue_admission(
                gateway_link=self.gateway_link,
            )

        self.assertEqual(
            raised.exception.code,
            "INSIGHT.GATEWAY_CHAT_QUEUE_FULL",
        )
        self.assertEqual(LensGatewayChatSlot.objects.count(), 1)

    def test_different_gateways_acquire_independent_slots(self):
        other_gateway = Node.objects.create(
            organization=self.organization,
            name="queue-gateway-2",
            role=Node.Role.GATEWAY,
        )
        other_link = LensGatewayLink.objects.create(
            organization=self.organization,
            gateway=other_gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
        )
        first = self._session()
        second = self._session(gateway_link=other_link)

        first_result = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=first.id,
            expected_generation=first.provision_generation,
        )
        second_result = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=second.id,
            expected_generation=second.provision_generation,
        )

        self.assertTrue(first_result.acquired)
        self.assertTrue(second_result.acquired)
        self.assertEqual(LensGatewayChatSlot.objects.count(), 2)

    def test_higher_concurrency_allows_fifo_window_to_fill_slots(self):
        self.gateway_link.chat_prepare_concurrency = 2
        self.gateway_link.save(
            update_fields=["chat_prepare_concurrency", "updated_at"]
        )
        first = self._session()
        second = self._session()
        third = self._session()

        second_result = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=second.id,
            expected_generation=second.provision_generation,
        )
        outside_window = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=third.id,
            expected_generation=third.provision_generation,
        )
        first_result = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=first.id,
            expected_generation=first.provision_generation,
        )
        third_result = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=third.id,
            expected_generation=third.provision_generation,
        )

        self.assertTrue(second_result.acquired)
        self.assertFalse(outside_window.acquired)
        self.assertTrue(first_result.acquired)
        self.assertFalse(third_result.acquired)
        self.assertEqual(gateway_chat_queue.chat_queue_ahead(session=third), 2)

    def test_unready_fifo_head_does_not_block_ready_chat(self):
        unready = self._session()
        unready.capacity_reservation_status = (
            LensSessionLink.CapacityReservationStatus.PENDING
        )
        unready.save(
            update_fields=["capacity_reservation_status", "updated_at"]
        )
        ready = self._session()

        acquired = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=ready.id,
            expected_generation=ready.provision_generation,
        )

        self.assertTrue(acquired.acquired)
        self.assertEqual(
            gateway_chat_queue.chat_queue_position(session=unready),
            0,
        )
        self.assertTrue(
            LensGatewayChatSlot.objects.filter(session_link_id=ready.id).exists()
        )

    def test_unready_chat_still_consumes_admission_capacity(self):
        unready = self._session()
        unready.capacity_reservation_status = (
            LensSessionLink.CapacityReservationStatus.PENDING
        )
        unready.save(
            update_fields=["capacity_reservation_status", "updated_at"]
        )
        self.gateway_link.chat_queue_capacity = 0
        self.gateway_link.save(
            update_fields=["chat_queue_capacity", "updated_at"]
        )

        with self.assertRaises(AppError):
            gateway_chat_queue.assert_chat_queue_admission(
                gateway_link=self.gateway_link,
            )

    @patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed"
    )
    def test_wake_skips_unready_chat_and_dispatches_ready_chat(self, queue_provision):
        unready = self._session()
        unready.capacity_reservation_status = (
            LensSessionLink.CapacityReservationStatus.PENDING
        )
        unready.save(
            update_fields=["capacity_reservation_status", "updated_at"]
        )
        ready = self._session()

        gateway_chat_queue.wake_gateway_queue(self.gateway_link.id)

        queue_provision.assert_called_once_with(ready.id)
        ready.refresh_from_db()
        self.assertIsNotNone(ready.provision_next_retry_at)

    def test_zero_queue_capacity_still_allows_an_immediate_slot(self):
        self.gateway_link.chat_queue_capacity = 0
        self.gateway_link.save(update_fields=["chat_queue_capacity", "updated_at"])

        gateway_chat_queue.assert_chat_queue_admission(
            gateway_link=self.gateway_link,
        )
        self._session()

        with self.assertRaises(AppError):
            gateway_chat_queue.assert_chat_queue_admission(
                gateway_link=self.gateway_link,
            )

    def test_cleanup_blocked_slot_is_not_reclaimed(self):
        blocked = self._session()
        gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=blocked.id,
            expected_generation=blocked.provision_generation,
        )
        LensSessionLink.objects.filter(pk=blocked.id).update(
            lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
            cleanup_status=LensSessionLink.CleanupStatus.BLOCKED,
        )
        self.gateway_link.chat_queue_capacity = 0
        self.gateway_link.save(update_fields=["chat_queue_capacity", "updated_at"])

        with self.assertRaises(AppError):
            gateway_chat_queue.assert_chat_queue_admission(
                gateway_link=self.gateway_link,
            )

        self.assertTrue(
            LensGatewayChatSlot.objects.filter(session_link_id=blocked.id).exists()
        )

    def test_completed_failure_slot_is_recovered_after_process_restart(self):
        failed = self._session()
        gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=failed.id,
            expected_generation=failed.provision_generation,
        )
        LensSessionLink.objects.filter(pk=failed.id).update(
            lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
            cleanup_status=LensSessionLink.CleanupStatus.COMPLETE,
        )

        gateway_chat_queue.assert_chat_queue_admission(
            gateway_link=self.gateway_link,
        )

        self.assertFalse(
            LensGatewayChatSlot.objects.filter(session_link_id=failed.id).exists()
        )

    def test_stale_generation_cannot_release_retry_slot(self):
        session = self._session()
        first = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=session.id,
            expected_generation=session.provision_generation,
        )
        self.assertTrue(first.acquired)
        LensGatewayChatSlot.objects.filter(session_link_id=session.id).delete()
        session.provision_generation += 1
        session.save(update_fields=["provision_generation", "updated_at"])
        second = gateway_chat_queue.try_acquire_chat_prepare_slot(
            session_link_id=session.id,
            expected_generation=session.provision_generation,
        )
        self.assertTrue(second.acquired)

        released = gateway_chat_queue.release_chat_prepare_slot(
            session_link_id=session.id,
            expected_generation=session.provision_generation - 1,
        )

        self.assertIsNone(released)
        self.assertTrue(
            LensGatewayChatSlot.objects.filter(
                session_link_id=session.id,
                session_generation=session.provision_generation,
            ).exists()
        )

    @patch.object(gateway_chat_queue, "wake_gateway_queue")
    @patch.object(gateway_chat_queue, "release_chat_prepare_slot", return_value=None)
    def test_failure_before_slot_acquisition_wakes_next_waiter(
        self,
        release_slot,
        wake_queue,
    ):
        session = self._session()
        claim_token = uuid.uuid4()
        session.provision_claim_token = claim_token
        session.save(update_fields=["provision_claim_token", "updated_at"])

        chat_lifecycle._mark_provision_failed_by_id(
            session.id,
            str(claim_token),
            "scope validation failed",
            expected_generation=session.provision_generation,
        )

        release_slot.assert_called_once_with(
            session_link_id=session.id,
            expected_generation=session.provision_generation,
        )
        wake_queue.assert_called_once_with(self.gateway_link.id)
