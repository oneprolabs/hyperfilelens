"""Uninstall capability evidence must belong to the current Agent connection."""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from redis.exceptions import ConnectionError

from apps.node.models import Node
from apps.node.services.internal.redis_store import _encode_agent_loc
from apps.node.services.internal.uninstall_readiness import (
    CAPABILITIES_NOT_READY,
    CAPABILITIES_UNAVAILABLE,
    REQUIRED_UNINSTALL_CAPABILITY,
    require_uninstall_capability,
    uninstall_readiness_error,
)


class UninstallReadinessTests(SimpleTestCase):
    def setUp(self):
        self.node = Node(
            id=123,
            metadata={
                "inventory_session_id": "current",
                "inventory_capabilities_session_id": "current",
                "inventory": {"capabilities": [REQUIRED_UNINSTALL_CAPABILITY]},
            },
        )
        self.client = Mock()
        self.client.get.return_value = _encode_agent_loc(
            ws_instance_id="api", session_id="current",
        )
        self.client.exists.return_value = True
        patcher = patch(
            "apps.node.services.internal.uninstall_readiness.redis_store.get_redis",
            return_value=self.client,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_current_session_supports_uninstall(self):
        self.assertIsNone(uninstall_readiness_error(self.node))

    def test_missing_stale_or_malformed_evidence_is_not_upgrade_required(self):
        cases = [
            {},
            {"capabilities": [REQUIRED_UNINSTALL_CAPABILITY]},
            {"inventory": {"capabilities": [REQUIRED_UNINSTALL_CAPABILITY]}},
            {"inventory": {"capabilities": []}},
            {**self.node.metadata, "inventory_session_id": "old"},
            {**self.node.metadata, "inventory_capabilities_session_id": "old"},
        ]
        for capabilities in (None, {}, "detached_uninstall_v2", [123], [""]):
            cases.append({
                **self.node.metadata,
                "inventory": {"capabilities": capabilities},
            })
        for metadata in cases:
            with self.subTest(metadata=metadata):
                self.node.metadata = metadata
                self.assertEqual(
                    uninstall_readiness_error(self.node).code, CAPABILITIES_NOT_READY,
                )

    def test_explicit_empty_or_unsupported_list_requires_upgrade(self):
        for capabilities in ([], ["repository_cleanup_v1"]):
            with self.subTest(capabilities=capabilities):
                self.node.metadata["inventory"]["capabilities"] = capabilities
                self.assertEqual(
                    uninstall_readiness_error(self.node).code, "agent_upgrade_required",
                )

    def test_offline_precedes_missing_capabilities(self):
        self.node.metadata = {}
        self.client.get.return_value = None
        self.assertEqual(uninstall_readiness_error(self.node).code, "node_offline")

    def test_missing_websocket_instance_is_offline(self):
        self.client.exists.return_value = False
        self.assertEqual(uninstall_readiness_error(self.node).code, "node_offline")

    def test_legacy_route_is_not_current_session_evidence(self):
        self.client.get.return_value = "api"
        self.assertEqual(uninstall_readiness_error(self.node).code, CAPABILITIES_NOT_READY)

    def test_redis_failure_is_not_offline_or_upgrade_required(self):
        self.client.get.side_effect = ConnectionError("unavailable")
        self.assertEqual(uninstall_readiness_error(self.node).code, CAPABILITIES_UNAVAILABLE)
        with patch(
            "apps.node.services.internal.uninstall_readiness.redis_store.get_redis",
            return_value=None,
        ):
            self.assertEqual(
                uninstall_readiness_error(self.node).code, CAPABILITIES_UNAVAILABLE,
            )

    @patch("apps.node.tasks.uplink_ingest.request_uplink_ingest")
    def test_only_unready_capabilities_request_on_demand_ingestion(self, wake):
        require_uninstall_capability(self.node)
        wake.assert_not_called()
        self.node.metadata = {}
        from apps.node.exceptions import NodeLifecycleError

        with self.assertRaises(NodeLifecycleError) as raised:
            require_uninstall_capability(self.node)
        self.assertEqual(raised.exception.code, CAPABILITIES_NOT_READY)
        wake.assert_called_once_with()
