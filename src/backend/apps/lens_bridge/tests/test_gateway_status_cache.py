"""Dedicated bounded status cache configuration and stalled-server regression."""

from concurrent.futures import wait
import socket
import threading
import time
import uuid
from unittest.mock import patch

from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache
from django.test import SimpleTestCase

from apps.lens_bridge.services import gateway_insights
from project.settings.cache import gateway_directory_cache_config


class GatewayDirectoryCacheConfigurationTests(SimpleTestCase):
    def test_redis_timeouts_and_retries_are_isolated_from_default_cache(self):
        default = {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379/1",
            "OPTIONS": {"socket_timeout": 60},
            "KEY_PREFIX": "backend",
        }
        bounded = gateway_directory_cache_config(default)
        self.assertEqual(default["OPTIONS"], {"socket_timeout": 60})
        self.assertEqual(default["KEY_PREFIX"], "backend")
        self.assertEqual(bounded["OPTIONS"]["socket_timeout"], 0.25)
        self.assertEqual(bounded["OPTIONS"]["socket_connect_timeout"], 0.25)
        self.assertEqual(bounded["OPTIONS"]["retry"].get_retries(), 0)
        self.assertFalse(bounded["OPTIONS"]["retry_on_timeout"])
        self.assertNotEqual(bounded["KEY_PREFIX"], default["KEY_PREFIX"])

    def test_redis_url_cannot_override_status_cache_limits(self):
        default = {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": (
                "redis://127.0.0.1:6379/1"
                "?socket_timeout=90&socket_connect_timeout=90&retry_on_timeout=true"
            ),
        }
        bounded = gateway_directory_cache_config(default)
        backend = RedisCache(bounded["LOCATION"], bounded)
        # Constructing a connection pool does not perform network I/O.
        options = backend._cache.get_client().connection_pool.connection_kwargs
        self.assertEqual(options["socket_timeout"], 0.25)
        self.assertEqual(options["socket_connect_timeout"], 0.25)
        self.assertFalse(options["retry_on_timeout"])
        self.assertIn("socket_timeout=90", default["LOCATION"])

    def test_memcached_limits_are_isolated_from_default_cache(self):
        default = {
            "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
            "LOCATION": "127.0.0.1:11211",
            "OPTIONS": {"no_delay": True, "ignore_exc": True},
        }
        bounded = gateway_directory_cache_config(default)
        self.assertEqual(bounded["OPTIONS"]["connect_timeout"], 0.25)
        self.assertEqual(bounded["OPTIONS"]["timeout"], 0.25)
        self.assertNotIn("timeout", default["OPTIONS"])

    def test_database_and_custom_caches_use_local_status_backoff(self):
        for backend in (
            "django.core.cache.backends.db.DatabaseCache",
            "example.UnboundedCache",
        ):
            with self.subTest(backend=backend):
                bounded = gateway_directory_cache_config(
                    {
                        "BACKEND": backend,
                        "LOCATION": "default-cache",
                    }
                )
                self.assertEqual(
                    bounded["BACKEND"],
                    "django.core.cache.backends.locmem.LocMemCache",
                )

    def test_unresponsive_redis_releases_all_workers_and_next_refresh_can_run(self):
        stop = threading.Event()
        connections = []
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.settimeout(0.05)

        def accept_without_reply():
            while not stop.is_set():
                try:
                    connection, _address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                connections.append(connection)

        server = threading.Thread(target=accept_without_reply, daemon=True)
        server.start()
        port = listener.getsockname()[1]
        config = gateway_directory_cache_config(
            {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": f"redis://127.0.0.1:{port}/1",
            }
        )
        backend = RedisCache(config["LOCATION"], config)
        futures = []
        try:
            with (
                patch.object(
                    gateway_insights, "caches", {"gateway_directory": backend}
                ),
                patch.object(gateway_insights.sl_client, "request_json") as sl,
            ):
                futures = [
                    gateway_insights._SL_STATUS_EXECUTOR.submit(
                        gateway_insights._fetch_lensnode_snapshot,
                        str(uuid.uuid4()),
                        deadline=time.monotonic() + 2,
                    )
                    for _ in range(8)
                ]
                done, pending = wait(futures, timeout=3)
                self.assertTrue(connections)
                self.assertFalse(pending)
                self.assertEqual(len(done), 8)
                self.assertTrue(all(future.result() is None for future in done))
                sl.assert_not_called()
        finally:
            stop.set()
            listener.close()
            server.join(timeout=1)
            for connection in connections:
                connection.close()
            wait(futures, timeout=3)
            backend._cache.get_client().connection_pool.disconnect()

        healthy_cache = LocMemCache(f"status-recovery-{uuid.uuid4()}", {})
        sl_uuid = str(uuid.uuid4())
        with (
            patch.object(
                gateway_insights, "caches", {"gateway_directory": healthy_cache}
            ),
            patch.object(
                gateway_insights.sl_client,
                "request_json",
                return_value={"uuid": sl_uuid, "status": "online"},
            ),
        ):
            future = gateway_insights._SL_STATUS_EXECUTOR.submit(
                gateway_insights._fetch_lensnode_snapshot,
                sl_uuid,
                deadline=time.monotonic() + 2,
            )
            self.assertEqual(future.result(timeout=3).data["status"], "online")
