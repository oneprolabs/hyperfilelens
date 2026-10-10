"""Health modes validate metadata without opening Kopia or adopting ownership."""

import io
import os
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from apps.storage.conf import (
    repository_health_check_mode,
    repository_health_timeout_seconds,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_health import probe_repository_health
from apps.storage.services.internal.repository_health_budget import (
    remaining_health_seconds,
    repository_health_budget,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
)
from apps.storage.services.internal.repository_lightweight_health import (
    check_s3_repository_lightweight,
)
from apps.storage.services.internal.repository_ownership import RepositoryOwnershipError
from apps.storage.services.internal.s3_client import S3ClientError, read_s3_object

MODULE = "apps.storage.services.internal.repository_lightweight_health"


class LightweightHealthTests(SimpleTestCase):
    def test_modes_and_budgets(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(repository_health_check_mode(), "lightweight")
            self.assertEqual(repository_health_timeout_seconds(), 60)
            os.environ["STORAGE_REPOSITORY_HEALTH_CHECK_MODE"] = "legacy"
            self.assertEqual(repository_health_timeout_seconds(), 900)
            os.environ["STORAGE_REPOSITORY_HEALTH_LEGACY_TIMEOUT_SECONDS"] = "1200"
            self.assertEqual(repository_health_timeout_seconds(), 1200)
            os.environ["STORAGE_REPOSITORY_HEALTH_LEGACY_TIMEOUT_SECONDS"] = "0"
            with self.assertRaises(ImproperlyConfigured):
                repository_health_timeout_seconds()
            os.environ["STORAGE_REPOSITORY_HEALTH_CHECK_MODE"] = "invalid"
            with self.assertRaises(ImproperlyConfigured):
                repository_health_check_mode()

    def test_s3_modes_do_not_fall_back(self):
        repository = SimpleNamespace(repo_type=Repository.Type.S3)
        with (
            mock.patch(
                "apps.storage.services.internal.repository_health.check_s3_repository_lightweight"
            ) as light,
            mock.patch(
                "apps.storage.services.internal.repository_health.check_s3_repository"
            ) as legacy,
        ):
            with mock.patch.dict(
                os.environ, {"STORAGE_REPOSITORY_HEALTH_CHECK_MODE": "lightweight"}
            ):
                self.assertEqual(probe_repository_health(repository), "online")
                legacy.assert_not_called()
                light.side_effect = RepositoryInitializationError("missing")
                with self.assertRaises(RepositoryInitializationError):
                    probe_repository_health(repository)
                legacy.assert_not_called()
            with mock.patch.dict(
                os.environ, {"STORAGE_REPOSITORY_HEALTH_CHECK_MODE": "legacy"}
            ):
                probe_repository_health(repository)
                legacy.assert_called_once_with(repository)

    def test_exact_two_bounded_reads_and_owner_validation(self):
        with (
            mock.patch(MODULE + "._s3_args", return_value={}),
            mock.patch(MODULE + "._prefix_with_slash", return_value="repo/"),
            mock.patch(
                MODULE + "._marker_key",
                return_value="repo/.hyperfilelens/repository-owner-v1.json",
            ),
            mock.patch(
                MODULE + ".ownership_payload",
                return_value={"repository_uuid": "expected"},
            ),
            mock.patch(MODULE + "._require_matching_marker") as owner,
            mock.patch(
                MODULE + ".read_s3_object",
                side_effect=[b'{"uniqueID":"id","keyAlgo":"algorithm"}', b"{}"],
            ) as read,
        ):
            check_s3_repository_lightweight(object())
            self.assertEqual(read.call_count, 2)
            self.assertEqual(
                read.call_args_list[0].kwargs["key"], "repo/kopia.repository"
            )
            self.assertEqual(read.call_args_list[1].kwargs["max_bytes"], 1024 * 1024)
            owner.assert_called_once_with(
                b"{}", expected={"repository_uuid": "expected"}
            )

    def test_missing_or_invalid_format_and_owner(self):
        for raw in [None, b"", b"[]", b"{}", b"bad"]:
            with (
                self.subTest(raw=raw),
                mock.patch(MODULE + "._s3_args", return_value={}),
                mock.patch(MODULE + "._prefix_with_slash", return_value=""),
                mock.patch(MODULE + ".read_s3_object", return_value=raw),
            ):
                with self.assertRaises(RepositoryInitializationError):
                    check_s3_repository_lightweight(object())
        with (
            mock.patch(MODULE + "._s3_args", return_value={}),
            mock.patch(MODULE + "._prefix_with_slash", return_value=""),
            mock.patch(MODULE + "._marker_key", return_value="owner"),
            mock.patch(
                MODULE + ".read_s3_object",
                side_effect=[b'{"uniqueID":"id","keyAlgo":"algo"}', None],
            ),
        ):
            with self.assertRaises(RepositoryOwnershipError):
                check_s3_repository_lightweight(object())

    def test_bounded_s3_body_is_closed_on_failure(self):
        body = io.BytesIO(b"oversized")
        with mock.patch("apps.storage.services.internal.s3_client._client") as client:
            client.return_value.get_object.return_value = {"Body": body}
            with self.assertRaises(S3ClientError):
                read_s3_object(
                    endpoint="example",
                    region="region",
                    bucket="bucket",
                    key="key",
                    access_key_id="test",
                    secret_access_key="test",
                    max_bytes=2,
                )
        self.assertTrue(body.closed)

    def test_real_streaming_body_eof_does_not_reset_released_socket(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        import urllib3
        from botocore.response import StreamingBody

        contents = {
            "/empty": b"",
            "/small": b"metadata",
            "/boundary": b"x" * 16384,
            "/multiple": b"x" * 32768,
            "/unknown": b"metadata",
            "/oversized": b"x" * 65537,
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = contents[self.path]
                self.send_response(200)
                if self.path != "/unknown":
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        pool = urllib3.PoolManager()
        try:
            for path, expected in contents.items():
                with self.subTest(path=path):
                    response = pool.request(
                        "GET",
                        f"http://127.0.0.1:{server.server_port}{path}",
                        preload_content=False,
                    )
                    length = response.headers.get("Content-Length")
                    body = StreamingBody(response, length)
                    with (
                        mock.patch(
                            "apps.storage.services.internal.s3_client._client"
                        ) as client,
                        repository_health_budget(60),
                    ):
                        result = {"Body": body}
                        if length is not None:
                            result["ContentLength"] = int(length)
                        client.return_value.get_object.return_value = result
                        args = dict(
                            endpoint="example",
                            region="region",
                            bucket="bucket",
                            key="key",
                            access_key_id="test",
                            secret_access_key="test",
                            max_bytes=65536,
                        )
                        if path == "/oversized":
                            with self.assertRaises(S3ClientError):
                                read_s3_object(**args)
                        else:
                            self.assertEqual(read_s3_object(**args), expected)
                    self.assertTrue(response.closed)
        finally:
            pool.clear()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_health_deadline_is_shared_and_reset(self):
        self.assertEqual(remaining_health_seconds(120), 120)
        with mock.patch(
            "apps.storage.services.internal.repository_health_budget.time.monotonic",
            side_effect=[100, 120, 161],
        ):
            with repository_health_budget(60):
                self.assertEqual(remaining_health_seconds(120), 40)
                with self.assertRaises(TimeoutError):
                    remaining_health_seconds(120)
        self.assertEqual(remaining_health_seconds(120), 120)

    def test_agent_policy_rejects_old_nodes_without_legacy_fallback(self):
        from apps.storage.services.internal.repository_health_policy import (
            repository_health_options,
        )
        from apps.storage.services.internal.repository_errors import (
            RepositoryHealthTransportUnconfirmed,
        )

        node = SimpleNamespace(metadata={})
        with mock.patch.dict(
            os.environ, {"STORAGE_REPOSITORY_HEALTH_CHECK_MODE": "lightweight"}
        ):
            with self.assertRaises(RepositoryHealthTransportUnconfirmed):
                repository_health_options(node)
            node.metadata = {"capabilities": ["repository_lightweight_health_v1"]}
            self.assertEqual(
                repository_health_options(node)["health_timeout_seconds"], 60
            )
        node.metadata = {}
        with mock.patch.dict(
            os.environ, {"STORAGE_REPOSITORY_HEALTH_CHECK_MODE": "legacy"}
        ):
            self.assertEqual(
                repository_health_options(node)["health_timeout_seconds"], 900
            )

    def test_watchdog_uses_pinned_health_budget_only(self):
        from datetime import datetime, timedelta, timezone
        from apps.node.services.internal.task import _initial_watchdog_deadline

        start = datetime(2026, 10, 10, tzinfo=timezone.utc)
        deadline = _initial_watchdog_deadline(
            correlation_type="storage.repository_health",
            kind="repo.status",
            from_time=start,
            payload={"health_timeout_seconds": 900},
        )
        self.assertGreaterEqual(deadline, start + timedelta(seconds=960))
        ordinary = _initial_watchdog_deadline(
            correlation_type="storage.repository_health",
            kind="repo.status",
            from_time=start,
        )
        from apps.node import conf as node_conf

        self.assertEqual(
            ordinary,
            start + timedelta(seconds=node_conf.AUTOMATIC_PROBE_WATCHDOG_SECONDS),
        )

    def test_bound_nas_and_local_disk_health_payloads_leave_usage_unchanged(self):
        from apps.storage.services.internal.repository_health import (
            _dispatch_repository_observation_task,
        )

        repository = SimpleNamespace(id=1, organization_id=1)
        node = SimpleNamespace(
            id=2, metadata={"capabilities": ["repository_lightweight_health_v1"]}
        )
        task = SimpleNamespace(refresh_from_db=lambda: None)
        for repo_type in ["nas", "proxy_fs"]:
            for include_usage in [False, True]:
                with (
                    self.subTest(repo_type=repo_type, include_usage=include_usage),
                    mock.patch.dict(
                        os.environ,
                        {"STORAGE_REPOSITORY_HEALTH_CHECK_MODE": "lightweight"},
                    ),
                    mock.patch(
                        "apps.storage.services.internal.repository_health.run_agent_task_async",
                        return_value=SimpleNamespace(task=task),
                    ) as dispatch,
                ):
                    _dispatch_repository_observation_task(
                        repository=repository,
                        node=node,
                        repository_payload={"type": repo_type},
                        repository_subdir="repo",
                        revision="revision",
                        group_id="group",
                        expected_node_ids=[2],
                        retry_attempt=0,
                        allow_ownership_adoption=True,
                        legacy_compatibility_allowed=False,
                        direct_nas=False,
                        transport_unknown=False,
                        include_usage=include_usage,
                        recorded_at=None,
                    )
                    payload = dispatch.call_args.kwargs["payload"]
                    persisted = dispatch.call_args.kwargs["persisted_payload"]
                    if include_usage:
                        self.assertNotIn("health_check_mode", payload)
                        self.assertNotIn("health_timeout_seconds", persisted)
                        self.assertTrue(payload["allow_ownership_adoption"])
                    else:
                        self.assertEqual(payload["health_check_mode"], "lightweight")
                        self.assertEqual(persisted["health_timeout_seconds"], 60)
                        self.assertFalse(payload["allow_ownership_adoption"])
