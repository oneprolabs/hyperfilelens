"""Tests for Celery Beat publish-side wake-up coalescing."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from celery import Celery, states
from django.test import SimpleTestCase
from django_celery_beat.schedulers import DatabaseScheduler

from common.scheduling.periodic_wakeup import (
    PERIODIC_WAKEUP_COALESCE_HEADER,
    PERIODIC_WAKEUP_NAME_HEADER,
    PERIODIC_WAKEUP_TOKEN_HEADER,
    _mark_periodic_wakeup_running,
    _release_periodic_wakeup_after_run,
    claim_periodic_wakeup,
    mark_periodic_wakeup_running,
    promote_periodic_wakeup,
    requeue_periodic_wakeup,
    release_periodic_wakeup,
)
from common.scheduling.scheduler import CoalescingDatabaseScheduler


_TEST_TOKEN = "87400:0123456789abcdef0123456789abcdef"


class PeriodicWakeupLeaseTests(SimpleTestCase):
    @patch("common.scheduling.periodic_wakeup.time.time", return_value=1_000)
    @patch("common.scheduling.periodic_wakeup._redis_client")
    def test_claim_uses_single_flight_lease(
        self,
        client_factory,
        _time,
    ) -> None:
        client = Mock()
        client.set.side_effect = [True, None]
        client_factory.return_value = client

        token = claim_periodic_wakeup("reconcile")
        duplicate = claim_periodic_wakeup("reconcile")

        self.assertIsInstance(token, str)
        self.assertFalse(duplicate)
        self.assertTrue(client.set.call_args.kwargs["nx"])
        claiming_value = client.set.call_args_list[0].args[1]
        self.assertTrue(claiming_value.startswith("claiming:87400:"))
        client.eval.assert_called_once()
        self.assertIn("^queued:(%d+):", client.eval.call_args.args[0])
        self.assertNotIn("^claiming:(%d+):", client.eval.call_args.args[0])

    @patch("common.scheduling.periodic_wakeup._redis_client")
    def test_publish_claim_is_promoted_only_for_current_token(
        self, client_factory
    ) -> None:
        client = Mock()
        client.eval.return_value = 1
        client_factory.return_value = client

        self.assertTrue(promote_periodic_wakeup("reconcile", _TEST_TOKEN))
        self.assertEqual(client.eval.call_args.args[-3], f"claiming:{_TEST_TOKEN}")
        self.assertEqual(client.eval.call_args.args[-2], f"queued:{_TEST_TOKEN}")
        self.assertIn("redis.call('set', key, queued", client.eval.call_args.args[0])

    @patch("common.scheduling.periodic_wakeup._redis_client")
    def test_invalid_token_is_rejected_before_running_lua(self, client_factory) -> None:
        self.assertFalse(promote_periodic_wakeup("reconcile", "owner-token"))
        self.assertFalse(mark_periodic_wakeup_running("reconcile", "owner-token"))
        self.assertFalse(release_periodic_wakeup("reconcile", "owner-token"))

        client_factory.assert_not_called()

    @patch("common.scheduling.periodic_wakeup._redis_client")
    def test_worker_consumption_transitions_to_bounded_running_lease(
        self,
        client_factory,
    ) -> None:
        client = Mock()
        client.eval.return_value = 1
        client_factory.return_value = client

        self.assertTrue(mark_periodic_wakeup_running("reconcile", _TEST_TOKEN))

        args = client.eval.call_args.args
        self.assertEqual(args[-4], f"claiming:{_TEST_TOKEN}")
        self.assertEqual(args[-3], f"queued:{_TEST_TOKEN}")
        self.assertEqual(args[-2], f"running:{_TEST_TOKEN}")
        self.assertIn("current == claiming or current == queued", args[0])

    @patch("common.scheduling.periodic_wakeup._redis_client")
    def test_worker_retry_returns_running_lease_to_queue(
        self,
        client_factory,
    ) -> None:
        client = Mock()
        client.eval.return_value = 1
        client_factory.return_value = client

        self.assertTrue(requeue_periodic_wakeup("reconcile", _TEST_TOKEN))

        args = client.eval.call_args.args
        self.assertEqual(args[-3], f"queued:{_TEST_TOKEN}")
        self.assertEqual(args[-2], f"running:{_TEST_TOKEN}")
        self.assertIn("remaining", args[0])

    @patch("common.scheduling.periodic_wakeup.release_periodic_wakeup")
    @patch("common.scheduling.periodic_wakeup.mark_periodic_wakeup_running")
    def test_worker_prerun_marks_matching_wakeup_running(
        self,
        mark_running,
        release,
    ) -> None:
        task = SimpleNamespace(
            request=SimpleNamespace(
                stamps={
                    PERIODIC_WAKEUP_NAME_HEADER: "reconcile",
                    PERIODIC_WAKEUP_TOKEN_HEADER: _TEST_TOKEN,
                }
            )
        )

        _mark_periodic_wakeup_running(task=task)

        mark_running.assert_called_once_with("reconcile", _TEST_TOKEN)
        release.assert_not_called()

    @patch("common.scheduling.periodic_wakeup._redis_client")
    def test_release_is_fenced_by_token(self, client_factory) -> None:
        client = Mock()
        client.eval.return_value = 1
        client_factory.return_value = client

        self.assertTrue(release_periodic_wakeup("reconcile", _TEST_TOKEN))
        self.assertEqual(client.eval.call_args.args[-3], f"claiming:{_TEST_TOKEN}")
        self.assertEqual(client.eval.call_args.args[-2], f"queued:{_TEST_TOKEN}")
        self.assertEqual(client.eval.call_args.args[-1], f"running:{_TEST_TOKEN}")

    @patch("common.scheduling.periodic_wakeup.release_periodic_wakeup")
    def test_worker_completion_releases_matching_wakeup(self, release) -> None:
        task = SimpleNamespace(
            request=SimpleNamespace(
                stamps={
                    PERIODIC_WAKEUP_NAME_HEADER: "reconcile",
                    PERIODIC_WAKEUP_TOKEN_HEADER: _TEST_TOKEN,
                }
            )
        )

        _release_periodic_wakeup_after_run(task=task, state=states.SUCCESS)

        release.assert_called_once_with("reconcile", _TEST_TOKEN)

    @patch("common.scheduling.periodic_wakeup.release_periodic_wakeup")
    @patch("common.scheduling.periodic_wakeup.requeue_periodic_wakeup")
    def test_worker_retry_requeues_matching_wakeup(self, requeue, release) -> None:
        task = SimpleNamespace(
            request=SimpleNamespace(
                stamps={
                    PERIODIC_WAKEUP_NAME_HEADER: "reconcile",
                    PERIODIC_WAKEUP_TOKEN_HEADER: _TEST_TOKEN,
                }
            )
        )

        _release_periodic_wakeup_after_run(task=task, state=states.RETRY)

        requeue.assert_called_once_with("reconcile", _TEST_TOKEN)
        release.assert_not_called()

    def test_celery_serializes_custom_wakeup_headers_into_request(self) -> None:
        app = Celery("periodic-header-test")
        message = app.amqp.create_task_message(
            "task-id",
            "tests.periodic",
            (),
            {},
            stamped_headers=[
                PERIODIC_WAKEUP_NAME_HEADER,
                PERIODIC_WAKEUP_TOKEN_HEADER,
            ],
            hfl_periodic_wakeup_name="reconcile",
            hfl_periodic_wakeup_token=_TEST_TOKEN,
        )

        self.assertEqual(
            message.headers["stamps"][PERIODIC_WAKEUP_NAME_HEADER],
            "reconcile",
        )
        self.assertEqual(
            message.headers["stamps"][PERIODIC_WAKEUP_TOKEN_HEADER],
            _TEST_TOKEN,
        )


class CoalescingSchedulerTests(SimpleTestCase):
    def _scheduler(self) -> CoalescingDatabaseScheduler:
        scheduler = object.__new__(CoalescingDatabaseScheduler)
        scheduler._tasks_since_sync = 0
        scheduler.reserve = Mock()
        scheduler.should_sync = Mock(return_value=False)
        scheduler._do_sync = Mock()
        return scheduler

    @patch("common.scheduling.scheduler.claim_periodic_wakeup", return_value=False)
    def test_duplicate_is_advanced_without_publish(self, _claim) -> None:
        scheduler = self._scheduler()
        entry = SimpleNamespace(
            name="reconcile",
            options={"headers": {PERIODIC_WAKEUP_COALESCE_HEADER: True}},
        )

        with patch.object(DatabaseScheduler, "apply_async") as publish:
            result = scheduler.apply_async(entry)

        self.assertIsNone(result)
        scheduler.reserve.assert_called_once_with(entry)
        publish.assert_not_called()

    @patch(
        "common.scheduling.scheduler.claim_periodic_wakeup",
        return_value=_TEST_TOKEN,
    )
    @patch("common.scheduling.scheduler.promote_periodic_wakeup")
    def test_claim_token_is_carried_in_message_headers(self, promote, _claim) -> None:
        scheduler = self._scheduler()
        entry = SimpleNamespace(
            name="reconcile",
            options={
                "headers": {
                    "x": "y",
                    PERIODIC_WAKEUP_COALESCE_HEADER: True,
                }
            },
        )

        with patch.object(
            DatabaseScheduler, "apply_async", return_value="sent"
        ) as publish:
            result = scheduler.apply_async(entry)

        self.assertEqual(result, "sent")
        claimed_entry = publish.call_args.args[0]
        self.assertEqual(
            claimed_entry.options[PERIODIC_WAKEUP_TOKEN_HEADER],
            _TEST_TOKEN,
        )
        self.assertIn(
            PERIODIC_WAKEUP_TOKEN_HEADER,
            claimed_entry.options["stamped_headers"],
        )
        self.assertEqual(
            entry.options,
            {
                "headers": {
                    "x": "y",
                    PERIODIC_WAKEUP_COALESCE_HEADER: True,
                }
            },
        )
        promote.assert_called_once_with("reconcile", _TEST_TOKEN)

    @patch("common.scheduling.scheduler.release_periodic_wakeup")
    @patch("common.scheduling.scheduler.promote_periodic_wakeup")
    @patch(
        "common.scheduling.scheduler.claim_periodic_wakeup",
        return_value=_TEST_TOKEN,
    )
    def test_publish_failure_releases_claim(self, _claim, promote, release) -> None:
        scheduler = self._scheduler()
        entry = SimpleNamespace(
            name="reconcile",
            options={"headers": {PERIODIC_WAKEUP_COALESCE_HEADER: True}},
        )

        with (
            patch.object(
                DatabaseScheduler,
                "apply_async",
                side_effect=RuntimeError("broker failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "broker failed"),
        ):
            scheduler.apply_async(entry)

        release.assert_called_once_with("reconcile", _TEST_TOKEN)
        promote.assert_not_called()

    @patch("common.scheduling.scheduler.claim_periodic_wakeup")
    def test_unmanaged_schedule_uses_standard_delivery(self, claim) -> None:
        scheduler = self._scheduler()
        entry = SimpleNamespace(name="operator-task", options={"headers": {}})

        with patch.object(
            DatabaseScheduler,
            "apply_async",
            return_value="sent",
        ) as publish:
            result = scheduler.apply_async(entry)

        self.assertEqual(result, "sent")
        publish.assert_called_once_with(
            entry,
            producer=None,
            advance=True,
        )
        claim.assert_not_called()
