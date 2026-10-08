"""HFL-authoritative private directory and bounded SourceLens enrichment."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
import threading
import time
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.core.management.color import no_style
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.iam.models import Membership
from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import gateway_insights as directory, sl_client


LOCAL_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "gateway-directory-tests",
    },
    "gateway_directory": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "gateway-directory-status-tests",
    },
}


@override_settings(CACHES=LOCAL_CACHE)
class GatewayDirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.node.models import Node

        cls.owner = get_user_model().objects.create_user(
            username="directory-owner", email="directory-owner@example.test"
        )
        cls.org, _ = provision_registered_user_tenant(cls.owner)
        cls.peer = get_user_model().objects.create_user(
            username="directory-peer", email="directory-peer@example.test"
        )
        Membership.objects.create(
            organization=cls.org, user=cls.peer, role=Membership.Role.ADMIN
        )
        cls.other = get_user_model().objects.create_user(
            username="directory-other", email="directory-other@example.test"
        )
        cls.other_org, _ = provision_registered_user_tenant(cls.other)
        cls.links = []
        for index in range(15):
            node = Node.objects.create(
                id=295 if index == 14 else 301 + index,
                organization=cls.org,
                name=f"Gateway {index:02d}",
                role=Node.Role.GATEWAY,
                ip_address=f"192.0.2.{index + 1}",
                connection_ip_address=f"198.51.100.{index + 1}",
                status=Node.Status.ACTIVE,
            )
            cls.links.append(
                LensGatewayLink.objects.create(
                    organization=cls.org,
                    gateway=node,
                    scope="user" if index % 2 else "organization",
                    owner_user=cls.owner,
                    created_by=cls.owner,
                    sl_lensnode_uuid=uuid.uuid4() if index else None,
                    sidecar_status="online",
                    config_json={
                        "sl_lensnode_snapshot": {
                            "sl_name": f"Old SL name {index}",
                            "sl_status": "online",
                        }
                    },
                )
            )
        # Explicit regression IDs do not advance PostgreSQL's sequence. Other
        # test classes may have advanced it into this range before our fixture.
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(no_style(), [Node]):
                cursor.execute(statement)
        cls.foreign_link = LensGatewayLink.objects.create(
            organization=cls.other_org,
            gateway=Node.objects.create(
                organization=cls.other_org, role="gateway", name="Foreign gateway"
            ),
            scope="organization",
            sl_lensnode_uuid=uuid.uuid4(),
        )
        cls.public_link = LensGatewayLink.objects.create(
            organization=cls.org,
            gateway=Node.objects.create(
                organization=cls.org, role="gateway", name="Public gateway"
            ),
            scope="platform",
        )
        for target in ("link", "node"):
            node = Node.objects.create(
                organization=cls.org,
                role="gateway",
                name=f"Deleted {target}",
                is_deleted=target == "node",
            )
            LensGatewayLink.objects.create(
                organization=cls.org,
                gateway=node,
                scope="organization",
                is_deleted=target == "link",
            )

    def setUp(self):
        cache.clear()
        caches["gateway_directory"].clear()
        self.client = APIClient()
        # A different admin sees organization resources regardless of installer.
        self.client.force_authenticate(self.peer)
        for target, value in (
            ("directory.enrich_node_row", {"lifecycle": None, "workload": None}),
            ("directory.node_agent_release_status", {}),
            (
                "directory.gateway_readiness.gateway_runtime_state",
                {
                    "hfl_managed": True,
                    "hfl_usable": True,
                    "hfl_agent_online": True,
                    "hfl_sidecar_online": True,
                },
            ),
        ):
            path = target.replace(
                "directory.", "apps.lens_bridge.services.gateway_insights."
            )
            mock = patch(path, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        routable = patch(
            "apps.node.api.serializers.node.agent_ws_routable", return_value=True
        )
        routable.start()
        self.addCleanup(routable.stop)

    def get_page(self, **params):
        return self.client.get(
            reverse("lens-gateway-directory"), params, HTTP_X_ORG_KEY=self.org.key
        )

    def refresh(self, ids, **extra):
        return self.client.post(
            reverse("lens-gateway-directory-status"),
            {"gateway_ids": ids, **extra},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

    def test_hfl_pages_include_all_links_without_any_source_lens_directory_read(self):
        with patch.object(
            sl_client, "request_json", side_effect=AssertionError("No SL")
        ) as sl:
            first = self.get_page(page=1, page_size=10)
            second = self.get_page(page=2, page_size=10)
            all_rows = self.get_page()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["count"], 15)
        self.assertEqual(second.data["count"], 15)
        self.assertEqual(len(first.data["results"]), 10)
        self.assertEqual(len(second.data["results"]), 5)
        self.assertEqual(all_rows.data["page_size"], 30)
        self.assertEqual(len(all_rows.data["results"]), 15)
        self.assertIn(295, [row["id"] for row in second.data["results"]])
        self.assertEqual(first.data["results"][0]["name"], "Gateway 00")
        self.assertEqual(first.data["results"][0]["status"], "active")
        self.assertIsNone(first.data["results"][0]["sl_lensnode_uuid"])
        self.assertTrue(
            all(row["scope"] == "organization" for row in all_rows.data["results"])
        )
        sl.assert_not_called()

    def test_search_counts_and_pages_are_computed_before_slicing(self):
        first = self.get_page(search="Gateway 0", page_size=10)
        self.assertEqual(first.data["count"], 10)
        self.assertEqual(len(first.data["results"]), 10)
        self.assertEqual(
            self.get_page(search="192.0.2.15").data["results"][0]["id"], 295
        )
        self.assertEqual(
            self.get_page(search="198.51.100.15").data["results"][0]["id"], 295
        )
        self.assertEqual(self.get_page(search="no match").data["count"], 0)
        self.assertEqual(self.get_page(page=10**30).data["results"], [])

    def test_ordering_is_stable_even_with_duplicate_names(self):
        for link in self.links:
            link.gateway.name = "Same name"
            link.gateway.save(update_fields=["name"])
        ids = [row["id"] for row in self.get_page().data["results"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, [row["id"] for row in self.get_page().data["results"]])

    def test_invalid_pagination_returns_400(self):
        for params in (
            {"page": 0},
            {"page": -1},
            {"page": "abc"},
            {"page_size": 15},
            {"page_size": 0},
            {"page_size": "abc"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.get_page(**params).status_code, 400)
        with self.assertRaises(ValidationError):
            directory.list_organization_gateway_directory_page(
                organization=self.org, page=0
            )

    def test_status_rejects_other_organizations_public_missing_and_large_batches(self):
        with patch.object(sl_client, "request_json") as sl:
            for ids in (
                [self.foreign_link.gateway_id],
                [self.public_link.gateway_id],
                [999999],
                [],
                list(range(1, 102)),
            ):
                with self.subTest(ids=ids):
                    self.assertEqual(self.refresh(ids).status_code, 400)
        sl.assert_not_called()

    def test_status_permission_matches_staff_directory_readers(self):
        with patch("apps.iam.permissions_org.get_effective_role") as role:
            for value in ("owner", "admin", "manager", "operator"):
                role.return_value = value
                with self.subTest(role=value):
                    self.assertEqual(
                        self.refresh([self.links[0].gateway_id]).status_code, 200
                    )
            role.return_value = "auditor"
            self.assertEqual(self.get_page().status_code, 403)
            self.assertEqual(self.refresh([self.links[0].gateway_id]).status_code, 403)

    def test_client_uuid_cannot_override_the_server_binding(self):
        link = self.links[1]
        snapshot = {"uuid": str(link.sl_lensnode_uuid), "status": "online"}
        with patch.object(sl_client, "request_json", return_value=snapshot) as sl:
            response = self.refresh(
                [link.gateway_id],
                sl_lensnode_uuid=str(self.foreign_link.sl_lensnode_uuid),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sl.call_args.args[1], f"/api/lens/admin/lensnodes/{link.sl_lensnode_uuid}/"
        )

    def test_fresh_snapshot_does_not_request_sl_but_force_does(self):
        link = self.links[1]
        link.config_json[directory._SL_SNAPSHOT_REFRESHED_AT_KEY] = (
            timezone.now().isoformat()
        )
        link.save(update_fields=["config_json"])
        snapshot = {
            "uuid": str(link.sl_lensnode_uuid),
            "status": "online",
            "name": "SL",
        }
        with patch.object(sl_client, "request_json", return_value=snapshot) as sl:
            cached = self.refresh([link.gateway_id])
            sl.assert_not_called()
            refreshed = self.refresh([link.gateway_id], force=True)
        self.assertEqual(cached.status_code, 200)
        self.assertEqual(refreshed.status_code, 200)
        sl.assert_called_once()
        self.assertEqual(
            sl.call_args.args[1], f"/api/lens/admin/lensnodes/{link.sl_lensnode_uuid}/"
        )
        self.assertIn("deadline", sl.call_args.kwargs)
        link.refresh_from_db()
        self.assertEqual(link.config_json["sl_lensnode_snapshot"]["sl_name"], "SL")
        self.assertNotIn("name", refreshed.data["results"][0])
        self.assertNotIn("status", refreshed.data["results"][0])
        self.assertNotIn("lifecycle", refreshed.data["results"][0])

    def test_partial_failure_keeps_last_snapshot_and_other_gateway_can_succeed(self):
        failed, success = self.links[1:3]

        def read(_method, path, **_kwargs):
            if str(failed.sl_lensnode_uuid) in path:
                raise sl_client.LensBridgeUnavailable()
            return {"uuid": str(success.sl_lensnode_uuid), "status": "offline"}

        with patch.object(sl_client, "request_json", side_effect=read):
            response = self.refresh([failed.gateway_id, success.gateway_id])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 2)
        failed.refresh_from_db()
        success.refresh_from_db()
        self.assertEqual(failed.sidecar_status, "online")
        self.assertEqual(
            failed.config_json["sl_lensnode_snapshot"]["sl_name"], "Old SL name 1"
        )
        self.assertEqual(success.sidecar_status, "offline")
        self.assertEqual(self.get_page().data["count"], 15)

    def test_total_sl_outage_never_hides_hfl_rows_or_sets_error(self):
        with patch.object(
            sl_client, "request_json", side_effect=sl_client.LensBridgeUnavailable()
        ):
            response = self.refresh([link.gateway_id for link in self.links])
            page = self.get_page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 15)
        self.assertEqual(page.data["count"], 15)
        self.assertFalse(
            LensGatewayLink.objects.filter(
                id__in=[link.id for link in self.links], sidecar_status="error"
            ).exists()
        )

    def test_snapshot_write_preserves_concurrent_lifecycle_config(self):
        link = self.links[1]
        snapshot = {"uuid": str(link.sl_lensnode_uuid), "status": "offline"}
        real_wait = directory.wait

        def concurrent_change(*args, **kwargs):
            result = real_wait(*args, **kwargs)
            current = LensGatewayLink.objects.get(id=link.id)
            current.sidecar_status = "removing"
            current.config_json["concurrent_marker"] = "keep me"
            current.save(update_fields=["sidecar_status", "config_json"])
            return result

        with (
            patch.object(sl_client, "request_json", return_value=snapshot),
            patch.object(directory, "wait", side_effect=concurrent_change),
        ):
            self.refresh([link.gateway_id])
        link.refresh_from_db()
        self.assertEqual(link.sidecar_status, "removing")
        self.assertEqual(link.config_json["concurrent_marker"], "keep me")

    def test_older_batch_cannot_overwrite_a_newer_successful_observation(self):
        link = self.links[1]
        real_wait = directory.wait
        newer_time = []

        def newer_request_finishes_first(*args, **kwargs):
            result = real_wait(*args, **kwargs)
            current = LensGatewayLink.objects.get(id=link.id)
            stamp = timezone.now().isoformat()
            newer_time.append(stamp)
            current.sidecar_status = "offline"
            current.config_json["sl_lensnode_snapshot"] = {
                "sl_status": "offline",
                "sl_name": "Newer offline observation",
            }
            current.config_json[directory._SL_SNAPSHOT_REFRESHED_AT_KEY] = stamp
            current.save(update_fields=["sidecar_status", "config_json"])
            return result

        with (
            patch.object(
                sl_client,
                "request_json",
                return_value={
                    "uuid": str(link.sl_lensnode_uuid),
                    "status": "online",
                    "name": "Older online observation",
                },
            ),
            patch.object(directory, "wait", side_effect=newer_request_finishes_first),
        ):
            response = self.refresh([link.gateway_id])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["sidecar_status"], "offline")
        link.refresh_from_db()
        self.assertEqual(
            link.config_json["sl_lensnode_snapshot"]["sl_name"],
            "Newer offline observation",
        )
        self.assertEqual(
            link.config_json[directory._SL_SNAPSHOT_REFRESHED_AT_KEY], newer_time[0]
        )

    def test_shared_cache_hit_retains_original_observation_time(self):
        link = self.links[1]
        observed_at = timezone.now() - timedelta(seconds=10)
        caches["gateway_directory"].set(
            f"gateway-directory:v2:snapshot:{link.sl_lensnode_uuid}",
            {
                "data": {"uuid": str(link.sl_lensnode_uuid), "status": "offline"},
                "observed_at": observed_at.isoformat(),
            },
            timeout=30,
        )
        with patch.object(sl_client, "request_json") as sl:
            response = self.refresh([link.gateway_id])
        self.assertEqual(response.status_code, 200)
        sl.assert_not_called()
        link.refresh_from_db()
        self.assertEqual(
            link.config_json[directory._SL_SNAPSHOT_REFRESHED_AT_KEY],
            observed_at.isoformat(),
        )
        self.assertEqual(link.sidecar_status, "offline")

    def test_expired_result_is_not_persisted_as_a_fresh_snapshot(self):
        link = self.links[1]
        expired = directory.LensNodeSnapshot(
            data={"uuid": str(link.sl_lensnode_uuid), "status": "offline"},
            observed_at=timezone.now() - timedelta(seconds=31),
        )
        with patch.object(directory, "_fetch_lensnode_snapshot", return_value=expired):
            self.refresh([link.gateway_id])
        link.refresh_from_db()
        self.assertEqual(link.sidecar_status, "online")
        self.assertNotIn(directory._SL_SNAPSHOT_REFRESHED_AT_KEY, link.config_json)

    def test_status_patch_reads_current_hfl_connectivity_independently_of_sl(self):
        link = self.links[1]
        node = link.gateway
        node.availability = "offline"
        node.availability_updated_at = timezone.now()
        node.last_seen_at = timezone.now() - timedelta(minutes=2)
        node.save(
            update_fields=["availability", "availability_updated_at", "last_seen_at"]
        )
        with (
            patch.object(
                directory.gateway_readiness,
                "gateway_runtime_state",
                return_value={
                    "hfl_managed": True,
                    "hfl_usable": False,
                    "hfl_agent_online": False,
                    "hfl_sidecar_online": True,
                },
            ),
            patch.object(
                sl_client, "request_json", side_effect=sl_client.LensBridgeUnavailable()
            ),
        ):
            response = self.refresh([link.gateway_id])
        row = response.data["results"][0]
        self.assertEqual(row["availability"], "offline")
        self.assertFalse(row["routable"])
        self.assertFalse(row["hfl_agent_online"])
        self.assertEqual(row["sidecar_status"], "online")
        self.assertEqual(row["last_seen_at"], node.last_seen_at)
        self.assertEqual(row["availability_updated_at"], node.availability_updated_at)

    def test_refresh_priority_uses_existing_snapshot_times_not_directory_order(self):
        links = self.links[1:5]
        now = timezone.now()
        timestamps = [
            now - timedelta(seconds=60),
            now - timedelta(seconds=300),
            None,
            "invalid timestamp",
        ]
        for link, timestamp in zip(links, timestamps, strict=True):
            if timestamp is not None:
                link.config_json[directory._SL_SNAPSHOT_REFRESHED_AT_KEY] = (
                    timestamp.isoformat()
                    if not isinstance(timestamp, str)
                    else timestamp
                )
                link.save(update_fields=["config_json"])
        directory_before = self.get_page().data

        def submit_no_result(*_args, **_kwargs):
            future = Future()
            future.set_result(None)
            return future

        with patch.object(
            directory._SL_STATUS_EXECUTOR, "submit", side_effect=submit_no_result
        ) as submit:
            response = self.refresh([link.gateway_id for link in reversed(links)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [call.args[1] for call in submit.call_args_list],
            [
                str(link.sl_lensnode_uuid)
                for link in (links[2], links[3], links[1], links[0])
            ],
        )
        self.assertEqual(
            [row["id"] for row in response.data["results"]],
            [link.gateway_id for link in links],
        )
        self.assertEqual(self.get_page().data, directory_before)

    def test_large_slow_page_repeated_batches_eventually_refresh_page_tail(self):
        from apps.node.models import Node

        links = list(self.links)
        links[0].sl_lensnode_uuid = uuid.uuid4()
        links[0].save(update_fields=["sl_lensnode_uuid"])
        for index in range(15, 100):
            node = Node.objects.create(
                organization=self.org,
                role=Node.Role.GATEWAY,
                name=f"Gateway {index:02d}",
            )
            links.append(
                LensGatewayLink.objects.create(
                    organization=self.org,
                    gateway=node,
                    scope="organization",
                    sl_lensnode_uuid=uuid.uuid4(),
                    sidecar_status="offline",
                )
            )
        gateway_ids = [link.gateway_id for link in links]
        directory_before = self.get_page(page_size=100).data
        clock = [timezone.now()]
        for start in range(0, 100, 8):
            successful_uuids = {
                str(link.sl_lensnode_uuid) for link in links[start : start + 8]
            }
            release_slow = threading.Event()
            observed_calls = []

            def slow_read(sl_uuid, *, deadline, force=False):
                if sl_uuid not in successful_uuids:
                    release_slow.wait(2)
                    return None
                return directory.LensNodeSnapshot(
                    data={"uuid": sl_uuid, "status": "online"},
                    observed_at=clock[0],
                )

            # Gate the slow workers instead of relying on sleep timing. Each
            # batch can persist eight results; the queued remainder times out.
            with (
                ThreadPoolExecutor(max_workers=8) as executor,
                patch.object(directory, "_SL_STATUS_EXECUTOR", executor),
                patch.object(directory, "_SL_STATUS_REFRESH_BUDGET_SECONDS", 0.05),
                patch.object(
                    directory, "_fetch_lensnode_snapshot", side_effect=slow_read
                ),
                patch.object(directory.timezone, "now", side_effect=lambda: clock[0]),
                patch.object(executor, "submit", wraps=executor.submit) as submit,
            ):
                try:
                    response = self.refresh(gateway_ids)
                    observed_calls = list(submit.call_args_list)
                finally:
                    release_slow.set()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                [row["id"] for row in response.data["results"]], gateway_ids
            )
            self.assertEqual(
                [call.args[1] for call in observed_calls[: len(successful_uuids)]],
                [str(link.sl_lensnode_uuid) for link in links[start : start + 8]],
            )
            self.assertEqual(
                LensGatewayLink.objects.filter(
                    id__in=[link.id for link in links],
                    config_json__has_key=directory._SL_SNAPSHOT_REFRESHED_AT_KEY,
                ).count(),
                min(start + 8, 100),
            )
            # Previous successful rows are stale again on the next poll, yet
            # rows without observations must still take priority.
            clock[0] += timedelta(seconds=31)

        directory_after = self.get_page(page_size=100).data
        self.assertEqual(directory_after["count"], directory_before["count"])
        self.assertEqual([row["id"] for row in directory_after["results"]], gateway_ids)
        self.assertEqual(
            LensGatewayLink.objects.filter(
                id__in=[link.id for link in links], sidecar_status="online"
            ).count(),
            100,
        )

    def test_no_uuid_skips_sl_and_unavailable_workers_degrade_to_cached_patch(self):
        with patch.object(sl_client, "request_json") as sl:
            self.assertEqual(self.refresh([self.links[0].gateway_id]).status_code, 200)
            with patch.object(directory, "_SL_STATUS_SLOTS") as slots:
                slots.acquire.return_value = False
                self.assertEqual(
                    self.refresh([self.links[1].gateway_id]).status_code, 200
                )
        sl.assert_not_called()

    def test_batch_budget_returns_cached_rows_before_slow_worker_completes(self):
        released = threading.Event()
        completed = threading.Event()

        def slow(*_args, **_kwargs):
            try:
                released.wait(2)
                return {}
            finally:
                completed.set()

        started = time.monotonic()
        try:
            with (
                patch.object(directory, "_fetch_lensnode_snapshot", side_effect=slow),
                patch.object(directory, "_SL_STATUS_REFRESH_BUDGET_SECONDS", 0.02),
            ):
                response = self.refresh([self.links[1].gateway_id])
            self.assertEqual(response.status_code, 200)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            released.set()
            completed.wait(2)


@override_settings(CACHES=LOCAL_CACHE)
class GatewayStatusCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        caches["gateway_directory"].clear()
        self.sl_uuid = str(uuid.uuid4())

    def read(self, **kwargs):
        return directory._fetch_lensnode_snapshot(
            self.sl_uuid, deadline=time.monotonic() + 6, **kwargs
        )

    def test_cached_success_and_force_refresh(self):
        snapshot = {"uuid": self.sl_uuid, "status": "online"}
        with patch.object(sl_client, "request_json", return_value=snapshot) as sl:
            first = self.read()
            self.assertEqual(first.data, snapshot)
            self.assertEqual(self.read(), first)
            self.assertEqual(sl.call_count, 1)
            self.read(force=True)
            self.assertEqual(sl.call_count, 2)

    def test_failure_has_short_backoff_and_manual_refresh_can_retry(self):
        with patch.object(
            sl_client, "request_json", side_effect=sl_client.LensBridgeUnavailable()
        ) as sl:
            self.assertIsNone(self.read())
            self.assertIsNone(self.read())
            self.assertEqual(sl.call_count, 1)
            self.read(force=True)
            self.assertEqual(sl.call_count, 2)

    def test_cache_hit_does_not_renew_original_timestamp(self):
        observed_at = timezone.now() - timedelta(seconds=10)
        snapshot = {"uuid": self.sl_uuid, "status": "online"}
        caches["gateway_directory"].set(
            f"gateway-directory:v2:snapshot:{self.sl_uuid}",
            {"data": snapshot, "observed_at": observed_at.isoformat()},
            timeout=30,
        )
        with patch.object(sl_client, "request_json") as sl:
            result = self.read()
        self.assertEqual(result.observed_at, observed_at)
        sl.assert_not_called()

    def test_expired_shared_entry_is_refetched_not_rejuvenated(self):
        snapshot = {"uuid": self.sl_uuid, "status": "online"}
        caches["gateway_directory"].set(
            f"gateway-directory:v2:snapshot:{self.sl_uuid}",
            {
                "data": {"uuid": self.sl_uuid, "status": "offline"},
                "observed_at": (timezone.now() - timedelta(seconds=31)).isoformat(),
            },
            timeout=30,
        )
        with patch.object(sl_client, "request_json", return_value=snapshot) as sl:
            result = self.read()
        self.assertEqual(result.data, snapshot)
        sl.assert_called_once()

    def test_successful_observation_survives_cache_write_failure(self):
        from unittest.mock import MagicMock

        status_cache = MagicMock()
        status_cache.get.return_value = None
        status_cache.add.return_value = True
        status_cache.set.side_effect = TimeoutError("cache write timed out")
        snapshot = {"uuid": self.sl_uuid, "status": "online"}
        with (
            patch.object(directory, "caches", {"gateway_directory": status_cache}),
            patch.object(sl_client, "request_json", return_value=snapshot),
        ):
            self.assertEqual(self.read().data, snapshot)
        status_cache.delete.assert_called_once()

    def test_single_flight_and_expired_deadline_do_not_start_new_sl_requests(self):
        caches["gateway_directory"].add(
            f"gateway-directory:v2:snapshot:{self.sl_uuid}:in-flight", True
        )
        with patch.object(sl_client, "request_json") as sl:
            self.assertIsNone(self.read(force=True))
            self.assertIsNone(
                directory._fetch_lensnode_snapshot(self.sl_uuid, deadline=0)
            )
        sl.assert_not_called()

    def test_expired_persistent_snapshot_requires_refresh(self):
        link = self._snapshot_link(timezone.now() - timedelta(seconds=31))
        self.assertFalse(directory._snapshot_is_fresh(link, now=timezone.now()))
        self.assertTrue(
            directory._snapshot_is_fresh(
                self._snapshot_link(timezone.now()), now=timezone.now()
            )
        )
        from types import SimpleNamespace

        self.assertFalse(
            directory._snapshot_is_fresh(
                SimpleNamespace(
                    config_json={
                        directory._SL_SNAPSHOT_REFRESHED_AT_KEY: "2026-99-99T00:00:00"
                    }
                ),
                now=timezone.now(),
            )
        )

    @staticmethod
    def _snapshot_link(timestamp):
        from types import SimpleNamespace

        return SimpleNamespace(
            config_json={directory._SL_SNAPSHOT_REFRESHED_AT_KEY: timestamp.isoformat()}
        )
