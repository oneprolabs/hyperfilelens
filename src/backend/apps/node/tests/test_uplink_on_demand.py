"""Exceptional lifecycle requests may wake the existing ingest task without Beat."""

import os
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
from redis import Redis
from redis.exceptions import ConnectionError

from apps.node import conf as node_conf
from apps.node.tasks.uplink_ingest import request_uplink_ingest


class OnDemandUplinkTests(SimpleTestCase):
    def setUp(self):
        self.client = Mock()
        self.client.eval.return_value = "OK"
        patcher = patch(
            "apps.node.tasks.uplink_ingest.redis_store.get_redis",
            return_value=self.client,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_directly_wakes_existing_consumer_with_expiry(self, publish):
        self.assertTrue(request_uplink_ingest())
        publish.assert_called_once_with(
            expires=node_conf.UPLINK_INGEST_EXPIRE_SECONDS, retry=False,
        )
        self.client.xdel.assert_not_called()

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_concurrent_requests_are_coalesced(self, publish):
        self.client.eval.side_effect = ["OK", None, None]
        self.assertTrue(request_uplink_ingest())
        self.assertFalse(request_uplink_ingest())
        self.assertFalse(request_uplink_ingest())
        publish.assert_called_once()

    @patch(
        "apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async",
        side_effect=ConnectionError("broker unavailable"),
    )
    def test_publication_failure_releases_only_own_claim_and_keeps_stream(self, publish):
        self.assertFalse(request_uplink_ingest())
        self.assertEqual(self.client.eval.call_count, 2)
        claim = self.client.eval.call_args_list[0].args
        release = self.client.eval.call_args_list[1].args
        self.assertEqual(claim[2:4], release[2:4])
        self.client.xdel.assert_not_called()

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_redis_failure_does_not_replace_lifecycle_diagnostic(self, publish):
        self.client.eval.side_effect = ConnectionError("Redis unavailable")
        self.assertFalse(request_uplink_ingest())
        publish.assert_not_called()

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_no_redis_does_not_publish_unbounded_fallback_tasks(self, publish):
        with patch(
            "apps.node.tasks.uplink_ingest.redis_store.get_redis", return_value=None,
        ):
            self.assertFalse(request_uplink_ingest())
        publish.assert_not_called()

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_expiry_cannot_outlast_cooldown(self, publish):
        with patch.object(node_conf, "UPLINK_INGEST_EXPIRE_SECONDS", 30):
            self.assertTrue(request_uplink_ingest())
        self.assertEqual(publish.call_args.kwargs["expires"], 5)


@skipUnless(os.getenv("HFL_TEST_REDIS_URL"), "Requires an isolated test Redis")
class OnDemandUplinkRedisTests(SimpleTestCase):
    """Exercise the actual Lua scripts, including concurrent claims and rollback TTLs."""

    def setUp(self):
        self.client = Redis.from_url(os.environ["HFL_TEST_REDIS_URL"], decode_responses=True)
        self.key = f"test:uplink-wakeup:{uuid4().hex}"
        patcher = patch(
            "apps.node.tasks.uplink_ingest.redis_store.get_redis", return_value=self.client,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        key_patch = patch("apps.node.tasks.uplink_ingest._ON_DEMAND_WAKEUP_KEY", self.key)
        key_patch.start()
        self.addCleanup(key_patch.stop)
        self.addCleanup(self.client.close)
        self.addCleanup(self.client.delete, self.key)

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_concurrent_claims_publish_once(self, publish):
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: request_uplink_ingest(), range(32)))
        self.assertEqual(sum(outcomes), 1)
        publish.assert_called_once()

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_clock_rollback_or_missing_expiry_cannot_stick_cooldown(self, publish):
        for ttl in (None, 28800):
            with self.subTest(ttl=ttl):
                self.client.set(self.key, "old", ex=ttl)
                self.assertTrue(request_uplink_ingest())
                self.assertGreater(self.client.pttl(self.key), 0)
                self.assertLessEqual(self.client.pttl(self.key), 5000)
        self.assertEqual(publish.call_count, 2)

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_delayed_failed_publish_cannot_delete_successor_claim(self, publish):
        def fail_after_successor(**_kwargs):
            self.client.set(self.key, "successor", ex=5)
            raise ConnectionError("publish failed")

        publish.side_effect = fail_after_successor
        self.assertFalse(request_uplink_ingest())
        self.assertEqual(self.client.get(self.key), "successor")

    @patch("apps.node.tasks.uplink_ingest.ingest_node_uplink_streams.apply_async")
    def test_failed_publication_allows_a_later_request(self, publish):
        publish.side_effect = [ConnectionError("publish failed"), None]
        self.assertFalse(request_uplink_ingest())
        self.assertIsNone(self.client.get(self.key))
        self.assertTrue(request_uplink_ingest())
