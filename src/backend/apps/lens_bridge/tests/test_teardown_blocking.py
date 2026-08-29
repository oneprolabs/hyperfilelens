from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.lens_bridge.services import teardown_blocking


class TeardownBlockingTests(SimpleTestCase):
    @patch.object(teardown_blocking, "INTERVENTION_ATTEMPT_THRESHOLD", 3)
    @patch.object(teardown_blocking, "INTERVENTION_AGE_SECONDS", 60)
    def test_same_condition_eventually_requires_intervention(self):
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
        self.assertTrue(third["intervention_required"])
        self.assertEqual(third["consecutive_attempts"], 3)

    def test_progress_resets_the_blocking_budget(self):
        started = timezone.now()
        state, _ = teardown_blocking.record_blocking(
            {},
            reason="conversion_stop_unconfirmed",
            task_id="convert-1",
            gateway_link_id=7,
            remote_status="CANCELLING",
            now=started,
        )

        _, progressed = teardown_blocking.record_blocking(
            state,
            reason="conversion_stop_unconfirmed",
            task_id="convert-1",
            gateway_link_id=7,
            remote_status="REVOKED",
            now=started + timedelta(hours=1),
        )

        self.assertEqual(progressed["consecutive_attempts"], 1)
        self.assertEqual(
            progressed["first_seen_at"],
            (started + timedelta(hours=1)).isoformat(),
        )

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
