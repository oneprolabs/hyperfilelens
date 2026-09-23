from datetime import timedelta
from django.test import SimpleTestCase
from django.utils import timezone

from apps.lens_bridge.services import teardown_blocking


class TeardownBlockingTests(SimpleTestCase):
    def test_safety_condition_never_requires_operator_intervention(self):
        started = timezone.now()
        state, first = teardown_blocking.record_blocking(
            {},
            reason="conversion_stop_unconfirmed",
            task_id="convert-1",
            gateway_link_id=7,
            remote_status="CANCELLING",
            now=started,
        )
        state, second = teardown_blocking.record_blocking(
            state,
            reason="conversion_stop_unconfirmed",
            task_id="convert-1",
            gateway_link_id=7,
            remote_status="CANCELLING",
            now=started + timedelta(seconds=30),
        )
        state, third = teardown_blocking.record_blocking(
            state,
            reason="conversion_stop_unconfirmed",
            task_id="convert-1",
            gateway_link_id=7,
            remote_status="CANCELLING",
            now=started + timedelta(seconds=60),
        )

        self.assertFalse(first["intervention_required"])
        self.assertFalse(second["intervention_required"])
        self.assertFalse(third["intervention_required"])
        self.assertFalse(third[teardown_blocking.RETRY_EXHAUSTED_KEY])
        self.assertEqual(third["consecutive_attempts"], 3)

    def test_ordinary_failure_exhausts_bounded_retry_budget(self):
        started = timezone.now()
        state, _ = teardown_blocking.record_blocking(
            {},
            reason="cleanup_workspace",
            gateway_link_id=7,
            now=started,
        )
        for attempt in range(2, 4):
            state, progressed = teardown_blocking.record_blocking(
                state,
                reason="cleanup_workspace",
                gateway_link_id=7,
                now=started + timedelta(minutes=attempt),
            )
        self.assertTrue(progressed[teardown_blocking.RETRY_EXHAUSTED_KEY])
        self.assertFalse(teardown_blocking.intervention_required(progressed))
        self.assertTrue(teardown_blocking.retry_exhausted({"blocking": progressed}))

    def test_gateway_identity_is_part_of_the_blocking_condition(self):
        now = timezone.now()
        state, _ = teardown_blocking.record_blocking(
            {},
            reason="workspace_cleanup",
            gateway_link_id=7,
            now=now,
        )

        _, moved = teardown_blocking.record_blocking(
            state,
            reason="workspace_cleanup",
            gateway_link_id=8,
            now=now + timedelta(minutes=1),
        )

        self.assertEqual(moved["consecutive_attempts"], 1)

    def test_clear_blocking_preserves_other_teardown_state(self):
        state = teardown_blocking.clear_blocking(
            {
                "intent": "delete_session",
                "blocking": {"intervention_required": True},
            }
        )

        self.assertEqual(state, {"intent": "delete_session"})
