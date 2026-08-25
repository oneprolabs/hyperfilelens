"""Agent node online/offline follows WebSocket session lifecycle."""

from __future__ import annotations

import json
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal import redis_store
from apps.node.services.internal.node_registry import (
    CONNECTION_RECONNECTING,
    agent_connection_status,
    effective_agent_node_status,
    record_node_availability,
    reconcile_node_availability,
    reconcile_stale_online_nodes,
)
from apps.node.ws.uplink import on_agent_connected, on_agent_disconnected
from apps.storage.repositories.models import Repository


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        del ex
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.data:
                deleted += 1
                self.data.pop(key, None)
        return deleted

    def exists(self, key: str) -> bool:
        return key in self.data

    def expire(self, key: str, ex: int) -> None:
        return None

    def scan_iter(self, match: str = "*", count: int = 10):
        prefix = match[:-1] if match.endswith("*") else match
        for key in list(self.data):
            if match == "*" or key.startswith(prefix):
                yield key

    def eval(self, script: str, key_count: int, key: str, session_id: str) -> int:
        del script
        if key_count != 1:
            raise ValueError("fake Redis supports one-key scripts only")
        raw = self.data.get(key)
        if raw is None:
            return 1
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if (
            isinstance(payload, dict)
            and payload.get("session")
            and str(payload["session"]) != session_id
        ):
            return 0
        self.delete(key)
        return 1


class AgentNodeOnlineStatusTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="node-status-org", name="Node Status Org")
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-1",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.OFFLINE,
        )
        self.redis = _FakeRedis()
        self._redis_patcher = self._patch_redis(self.redis)
        self._get_redis = self._redis_patcher.start()
        self.addCleanup(self._redis_patcher.stop)
        redis_store._client = None
        from unittest.mock import patch

        self._lifecycle_enqueue_patcher = patch(
            "apps.node.tasks.lifecycle.advance_node_lifecycle_for_node.apply_async",
        )
        self._lifecycle_enqueue = self._lifecycle_enqueue_patcher.start()
        self.addCleanup(self._lifecycle_enqueue_patcher.stop)

    @staticmethod
    def _patch_redis(fake: _FakeRedis):
        from unittest.mock import patch

        return patch(
            "apps.node.services.internal.redis_store.get_redis",
            return_value=fake,
        )

    def _mark_ws_alive(self) -> None:
        ws_id = node_conf.WS_INSTANCE_ID
        self.redis.set(redis_store.ws_alive_key(ws_id), "1")

    def test_ws_connect_marks_online(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")

        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self.assertIsNotNone(self.node.availability_updated_at)
        self.assertEqual(
            effective_agent_node_status(self.node),
            Node.Availability.ONLINE,
        )

    @mock.patch("apps.source.services.internal.availability.project_node_availability")
    @mock.patch("apps.storage.tasks.check_storage_repository_health.apply_async")
    def test_online_transition_probes_bound_unhealthy_repositories(
        self,
        apply_async,
        project_availability,
    ):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="offline-bound-repository",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.node.id,
        )

        with self.captureOnCommitCallbacks(execute=True):
            changed = record_node_availability(
                node_id=self.node.id,
                availability=Node.Availability.ONLINE,
            )

        self.assertTrue(changed)
        project_availability.assert_called_once_with(
            node_id=self.node.id,
            transitioned=True,
        )
        apply_async.assert_called_once_with(
            kwargs={"repository_id": repository.id},
            countdown=2,
        )

    def test_offline_transition_marks_bound_repository_offline(self):
        self.node.availability = Node.Availability.ONLINE
        self.node.save(update_fields=["availability", "updated_at"])
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="online-bound-repository",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            health_failures=1,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.node.id,
        )

        changed = record_node_availability(
            node_id=self.node.id,
            availability=Node.Availability.OFFLINE,
        )

        self.assertTrue(changed)
        repository.refresh_from_db()
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        self.assertEqual(repository.health_failures, 0)

    def test_ws_connect_updates_connection_ip_without_overwriting_host_ip(self):
        self.node.ip_address = "10.20.1.15"
        self.node.save(update_fields=["ip_address", "updated_at"])

        on_agent_connected(
            node_id=self.node.id,
            session_id="session-a",
            client_ip="203.0.113.20",
        )

        self.node.refresh_from_db()
        self.assertEqual(str(self.node.ip_address), "10.20.1.15")
        self.assertEqual(str(self.node.connection_ip_address), "203.0.113.20")

    def test_ws_recovery_hold_requires_live_ws_and_expiry(self):
        self.assertFalse(redis_store.offline_task_finalization_ready())
        self._mark_ws_alive()
        self.assertTrue(redis_store.offline_task_finalization_ready())
        redis_store.begin_ws_recovery_hold(seconds=180)
        self.assertTrue(redis_store.ws_recovery_hold_active())
        self.assertFalse(redis_store.offline_task_finalization_ready())
        self.redis.delete(redis_store.ws_recovery_hold_key())
        self.assertTrue(redis_store.offline_task_finalization_ready())

    def test_ws_disconnect_enters_reconnecting_grace(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        on_agent_disconnected(node_id=self.node.id, session_id="session-a")

        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self.assertEqual(agent_connection_status(self.node), CONNECTION_RECONNECTING)
        self.assertIsNone(redis_store.get_agent_location(agent_id=self.node.id))
        self.assertEqual(
            effective_agent_node_status(self.node),
            Node.Availability.ONLINE,
        )

    def test_flapping_agent_coalesces_lifecycle_wakeups(self):
        """A connect/disconnect burst schedules one lifecycle wake-up per node."""
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        on_agent_disconnected(node_id=self.node.id, session_id="session-a")

        self._lifecycle_enqueue.assert_called_once_with(
            kwargs={"node_id": self.node.id},
            expires=node_conf.LIFECYCLE_ADVANCE_EXPIRE_SECONDS,
        )
        self.assertTrue(
            self.redis.exists(
                redis_store.lifecycle_advance_event_key(node_id=self.node.id)
            )
        )

    def test_connect_reuses_one_redis_lookup_for_route_and_wakeup(self):
        self._get_redis.reset_mock()

        on_agent_connected(node_id=self.node.id, session_id="session-a")

        self._get_redis.assert_called_once_with()

    def test_redis_route_write_failure_does_not_break_connect_callback(self):
        from redis.exceptions import ConnectionError as RedisConnectionError
        from unittest.mock import patch

        with patch.object(
            self.redis,
            "set",
            side_effect=RedisConnectionError("redis unavailable"),
        ):
            on_agent_connected(node_id=self.node.id, session_id="session-a")

        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self._lifecycle_enqueue.assert_not_called()

    def test_lifecycle_enqueue_failure_does_not_break_ws_callback(self):
        self._lifecycle_enqueue.side_effect = RuntimeError("broker unavailable")

        on_agent_connected(node_id=self.node.id, session_id="session-a")

        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self.assertEqual(self._lifecycle_enqueue.call_count, 1)

    def test_redis_outage_defers_lifecycle_wakeup_to_periodic_sweep(self):
        from unittest.mock import patch

        with patch(
            "apps.node.services.internal.redis_store.get_redis",
            return_value=None,
        ):
            claimed = redis_store.claim_lifecycle_advance_event(
                node_id=self.node.id
            )

        self.assertFalse(claimed)

    def test_ws_disconnect_effective_offline_after_grace(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        on_agent_disconnected(node_id=self.node.id, session_id="session-a")

        stale_at = timezone.now() - timezone.timedelta(
            seconds=node_conf.AGENT_LOC_TTL_SECONDS + 5,
        )
        Node.objects.filter(pk=self.node.id).update(last_seen_at=stale_at)
        self.node.refresh_from_db()

        self.assertEqual(
            effective_agent_node_status(self.node),
            Node.Availability.OFFLINE,
        )

    def test_ws_disconnect_does_not_fail_active_task(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="repo.status",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )

        on_agent_disconnected(node_id=self.node.id, session_id="session-a")

        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        self.assertEqual(task.last_error, "")

    def test_ws_disconnect_is_recorded_for_detached_upgrade(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            result={
                "target_version": "1.0.0",
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
            },
            watchdog_deadline_at=timezone.now(),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )

        on_agent_disconnected(node_id=self.node.id, session_id="session-a")

        task.refresh_from_db()
        self.assertIn("disconnect_observed_at", task.result or {})

    def test_stale_disconnect_after_reconnect_stays_online(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        on_agent_connected(node_id=self.node.id, session_id="session-b")
        on_agent_disconnected(node_id=self.node.id, session_id="session-a")

        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self.assertEqual(
            effective_agent_node_status(self.node),
            Node.Availability.ONLINE,
        )
        raw = self.redis.get(redis_store.agent_loc_key(self.node.id))
        assert raw is not None
        self.assertEqual(json.loads(raw)["session"], "session-b")

    def test_effective_status_offline_without_ws_route(self):
        self.node.availability = Node.Availability.ONLINE
        self.node.last_seen_at = timezone.now() - timezone.timedelta(
            seconds=node_conf.AGENT_LOC_TTL_SECONDS + 5,
        )
        self.node.save(update_fields=["availability", "last_seen_at", "updated_at"])

        self.assertEqual(
            effective_agent_node_status(self.node),
            Node.Availability.OFFLINE,
        )
        self.assertEqual(agent_connection_status(self.node), Node.Availability.OFFLINE)

    def test_effective_status_grace_without_ws_route(self):
        self.node.availability = Node.Availability.ONLINE
        self.node.last_seen_at = timezone.now()
        self.node.save(update_fields=["availability", "last_seen_at", "updated_at"])

        self.assertEqual(
            effective_agent_node_status(self.node),
            Node.Availability.ONLINE,
        )

    def test_reconcile_marks_stale_online_offline(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        self.redis.delete(redis_store.agent_loc_key(self.node.id))
        stale_at = timezone.now() - timezone.timedelta(
            seconds=node_conf.AGENT_LOC_TTL_SECONDS + 5,
        )
        Node.objects.filter(pk=self.node.id).update(last_seen_at=stale_at)

        summary = reconcile_stale_online_nodes(limit=10)

        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.OFFLINE)
        self.assertEqual(summary["nodes_marked_offline"], 1)

    def test_reconcile_marks_bound_repository_offline_with_stale_proxy(self):
        self.node.role = NodeRole.PROXY
        self.node.save(update_fields=["role", "updated_at"])
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        self.redis.delete(redis_store.agent_loc_key(self.node.id))
        stale_at = timezone.now() - timezone.timedelta(
            seconds=node_conf.AGENT_LOC_TTL_SECONDS + 5,
        )
        Node.objects.filter(pk=self.node.id).update(last_seen_at=stale_at)
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="proxy-filesystem-repository",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.node.id,
        )

        summary = reconcile_stale_online_nodes(limit=10)

        repository.refresh_from_db()
        self.assertEqual(summary["nodes_marked_offline"], 1)
        self.assertEqual(repository.health, Repository.Health.OFFLINE)

    def test_reconcile_skips_recent_last_seen(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        self.redis.delete(redis_store.agent_loc_key(self.node.id))

        summary = reconcile_stale_online_nodes(limit=10)

        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self.assertEqual(summary["nodes_marked_offline"], 0)

    def test_availability_reconcile_marks_stale_node_offline(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        self.redis.delete(redis_store.agent_loc_key(self.node.id))
        Node.objects.filter(pk=self.node.id).update(
            last_seen_at=timezone.now()
            - timezone.timedelta(seconds=node_conf.AGENT_LOC_TTL_SECONDS + 5),
        )

        summary = reconcile_node_availability(limit=10)

        self.node.refresh_from_db()
        self.assertTrue(summary["redis_healthy"])
        self.assertEqual(summary["nodes_marked_offline"], 1)
        self.assertEqual(self.node.availability, Node.Availability.OFFLINE)

    def test_availability_reconcile_retains_observation_during_redis_outage(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        Node.objects.filter(pk=self.node.id).update(
            last_seen_at=timezone.now()
            - timezone.timedelta(seconds=node_conf.AGENT_LOC_TTL_SECONDS + 5),
        )
        from unittest.mock import patch

        with patch(
            "apps.node.services.internal.redis_store.get_redis",
            return_value=None,
        ):
            summary = reconcile_node_availability(limit=10)

        self.node.refresh_from_db()
        self.assertFalse(summary["redis_healthy"])
        self.assertEqual(summary["nodes_marked_offline"], 0)
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)

    def test_availability_reconcile_recovers_after_redis_outage(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        self.redis.delete(redis_store.agent_loc_key(self.node.id))
        Node.objects.filter(pk=self.node.id).update(
            last_seen_at=timezone.now()
            - timezone.timedelta(seconds=node_conf.AGENT_LOC_TTL_SECONDS + 5),
        )
        from unittest.mock import patch

        with patch(
            "apps.node.services.internal.redis_store.get_redis",
            return_value=None,
        ):
            reconcile_node_availability(limit=10)
        summary = reconcile_node_availability(limit=10)

        self.node.refresh_from_db()
        self.assertTrue(summary["redis_healthy"])
        self.assertEqual(self.node.availability, Node.Availability.OFFLINE)

    def test_availability_reconcile_retains_when_cached_redis_client_fails(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        Node.objects.filter(pk=self.node.id).update(
            last_seen_at=timezone.now()
            - timezone.timedelta(seconds=node_conf.AGENT_LOC_TTL_SECONDS + 5),
        )
        from unittest.mock import Mock, patch

        failed_client = Mock()
        failed_client.ping.side_effect = ConnectionError("redis unavailable")
        with patch(
            "apps.node.services.internal.redis_store.get_redis",
            return_value=failed_client,
        ):
            summary = reconcile_node_availability(limit=10)

        self.node.refresh_from_db()
        self.assertFalse(summary["redis_healthy"])
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)

    def test_availability_reconcile_does_not_override_a_new_heartbeat(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        self.node.refresh_from_db()
        expected_updated_at = self.node.availability_updated_at
        expected_last_seen_at = self.node.last_seen_at
        newer_last_seen_at = timezone.now() + timezone.timedelta(seconds=1)
        Node.objects.filter(pk=self.node.id).update(
            last_seen_at=newer_last_seen_at,
        )

        changed = record_node_availability(
            node_id=self.node.id,
            availability=Node.Availability.OFFLINE,
            expected_updated_at=expected_updated_at,
            expected_last_seen_at=expected_last_seen_at,
        )

        self.node.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        self.assertEqual(self.node.last_seen_at, newer_last_seen_at)

    def test_older_observation_does_not_regress_availability_timestamp(self):
        self._mark_ws_alive()
        on_agent_connected(node_id=self.node.id, session_id="session-a")
        self.node.refresh_from_db()
        current_updated_at = self.node.availability_updated_at

        changed = record_node_availability(
            node_id=self.node.id,
            availability=Node.Availability.ONLINE,
            observed_at=current_updated_at - timezone.timedelta(seconds=1),
        )

        self.node.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(self.node.availability_updated_at, current_updated_at)

    def test_legacy_plain_agent_loc_value_still_routable(self):
        ws_id = node_conf.WS_INSTANCE_ID
        self.redis.set(redis_store.agent_loc_key(self.node.id), ws_id)
        self._mark_ws_alive()

        self.assertEqual(redis_store.get_agent_location(agent_id=self.node.id), ws_id)

        self.node.availability = Node.Availability.ONLINE
        self.node.save(update_fields=["availability", "updated_at"])
        self.assertEqual(
            effective_agent_node_status(self.node),
            Node.Availability.ONLINE,
        )
        self.assertEqual(agent_connection_status(self.node), Node.Availability.ONLINE)

    def test_connection_status_online_when_agent_loc_present_without_ws_alive(self):
        """Other agents must not show reconnecting when only shared ws_alive flickers."""
        ws_id = node_conf.WS_INSTANCE_ID
        redis_store.set_agent_location(
            agent_id=self.node.id,
            session_id="session-z",
            ws_instance_id=ws_id,
        )
        self.node.availability = Node.Availability.ONLINE
        self.node.last_seen_at = timezone.now()
        self.node.save(update_fields=["availability", "last_seen_at", "updated_at"])

        self.assertFalse(redis_store.get_redis().exists(redis_store.ws_alive_key(ws_id)))
        self.assertEqual(agent_connection_status(self.node), Node.Availability.ONLINE)

    def test_session_payload_round_trip(self):
        ws_id = node_conf.WS_INSTANCE_ID
        redis_store.set_agent_location(
            agent_id=self.node.id,
            session_id="session-x",
            ws_instance_id=ws_id,
        )
        raw = self.redis.get(redis_store.agent_loc_key(self.node.id))
        assert raw is not None
        payload = json.loads(raw)
        self.assertEqual(payload["ws"], ws_id)
        self.assertEqual(payload["session"], "session-x")

    def test_clear_ws_instance_routes_only_current_instance(self):
        ws_id = node_conf.WS_INSTANCE_ID
        other_ws = "other-ws"
        redis_store.set_agent_location(
            agent_id=self.node.id,
            session_id="session-x",
            ws_instance_id=ws_id,
        )
        other_node = Node.objects.create(
            organization=self.org,
            name="agent-2",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        redis_store.set_agent_location(
            agent_id=other_node.id,
            session_id="session-y",
            ws_instance_id=other_ws,
        )
        self.redis.set(redis_store.ws_alive_key(ws_id), "1")
        self.redis.set(redis_store.ws_alive_key(other_ws), "1")

        summary = redis_store.clear_ws_instance_routes(ws_instance_id=ws_id)

        self.assertEqual(summary["agent_locations_deleted"], 1)
        self.assertEqual(summary["ws_alive_deleted"], 1)
        self.assertIsNone(redis_store.get_agent_location(agent_id=self.node.id))
        self.assertEqual(redis_store.get_agent_location(agent_id=other_node.id), other_ws)
        self.assertTrue(self.redis.exists(redis_store.ws_alive_key(other_ws)))

    def test_ttl_defaults_exceed_agent_heartbeat_interval(self):
        """Prevent online/offline flicker between 30s WSS heartbeats."""
        heartbeat_seconds = 30
        self.assertGreater(
            node_conf.WS_INSTANCE_ALIVE_TTL_SECONDS,
            heartbeat_seconds,
        )
        self.assertGreater(
            node_conf.AGENT_LOC_TTL_SECONDS,
            heartbeat_seconds,
        )

    def test_ensure_agent_location_on_heartbeat_recreates_expired_lease(self):
        ws_id = node_conf.WS_INSTANCE_ID
        self.node.availability = Node.Availability.ONLINE
        self.node.last_seen_at = timezone.now()
        self.node.save(update_fields=["availability", "last_seen_at", "updated_at"])
        redis_store.set_agent_location(
            agent_id=self.node.id,
            session_id="session-old",
            ws_instance_id=ws_id,
        )
        self.redis.delete(redis_store.agent_loc_key(self.node.id))
        self.assertFalse(self.redis.exists(redis_store.agent_loc_key(self.node.id)))

        redis_store.ensure_agent_location_on_heartbeat(
            agent_id=self.node.id,
            session_id="session-live",
        )

        self.assertTrue(self.redis.exists(redis_store.agent_loc_key(self.node.id)))
        raw = self.redis.get(redis_store.agent_loc_key(self.node.id))
        payload = json.loads(raw)
        self.assertEqual(payload["session"], "session-live")
        self.assertEqual(agent_connection_status(self.node), Node.Availability.ONLINE)
