from importlib import import_module
from unittest.mock import patch

from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase

from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.network_inventory import (
    MAX_ADDRESSES_PER_HOST,
    MAX_INTERFACES,
    normalize_agent_network_state,
    normalize_network_inventory,
    same_network_inventory,
)
from apps.node.ws.uplink import (
    _process_heartbeat_followup,
    apply_heartbeat_inventory_snapshot,
)


def network_payload(primary: str = "10.20.1.15") -> dict:
    return {
        "schema_version": 1,
        "collected_at": "2026-07-28T12:00:00Z",
        "selection": {
            "address": primary,
            "family": "ipv4",
            "interface_id": "mac:00:11:22:33:44:55",
            "source": "route_to_control_plane",
        },
        "interfaces": [
            {
                "id": "mac:00:11:22:33:44:55",
                "name": "Ethernet 0",
                "mac_address": "00:11:22:33:44:55",
                "type": "ethernet",
                "virtual": False,
                "default_route": True,
                "addresses": [
                    {"address": primary, "family": "ipv4", "prefix_length": 24}
                ],
            }
        ],
    }


class NetworkInventoryNormalizationTests(SimpleTestCase):
    def test_normalizes_primary_ip_and_matching_mac(self):
        state = normalize_agent_network_state(
            {
                "primary_ip_address": "198.51.100.90",
                "network_inventory": network_payload(),
            }
        )

        self.assertEqual(state.primary_ip_address, "10.20.1.15")
        self.assertEqual(state.primary_mac_address, "00:11:22:33:44:55")
        self.assertNotIn("network_inventory", state.metadata_inventory)

    def test_bounds_interface_and_address_counts(self):
        payload = network_payload()
        payload["interfaces"] = []
        for index in range(MAX_INTERFACES + 5):
            payload["interfaces"].append(
                {
                    "id": f"if:{index}",
                    "name": f"eth{index}",
                    "type": "ethernet",
                    "addresses": [
                        {
                            "address": f"10.{index}.0.{address + 1}",
                            "prefix_length": 24,
                        }
                        for address in range(8)
                    ],
                }
            )
        payload["selection"] = {
            "address": "10.0.0.1",
            "interface_id": "if:0",
            "source": "route_to_control_plane",
        }

        normalized = normalize_network_inventory(payload)

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertLessEqual(len(normalized["interfaces"]), MAX_INTERFACES)
        count = sum(len(item["addresses"]) for item in normalized["interfaces"])
        self.assertLessEqual(count, MAX_ADDRESSES_PER_HOST)

    def test_skips_invalid_entries_without_hiding_later_valid_data(self):
        payload = network_payload()
        payload["interfaces"] = [None] * MAX_INTERFACES + payload["interfaces"]
        payload["interfaces"][-1]["addresses"] = [None] + payload["interfaces"][-1][
            "addresses"
        ]

        normalized = normalize_network_inventory(payload)

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(len(normalized["interfaces"]), 1)
        self.assertEqual(
            normalized["interfaces"][0]["addresses"][0]["address"],
            "10.20.1.15",
        )

    def test_rejects_primary_not_announced_by_legacy_inventory(self):
        state = normalize_agent_network_state(
            {
                "primary_ip_address": "10.20.1.15",
                "ip_addresses": ["10.20.1.16"],
            }
        )
        self.assertIsNone(state.primary_ip_address)

    def test_ignores_collection_time_when_comparing_snapshots(self):
        left = network_payload()
        right = network_payload()
        right["collected_at"] = "2026-07-28T12:01:00Z"
        self.assertTrue(same_network_inventory(left, right))


class NodeNetworkInventoryHeartbeatTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="network-org", name="Network Org")
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-a",
            role=NodeRole.AGENT,
            ip_address="10.20.1.10",
        )

    def test_inventory_heartbeat_updates_host_ip_and_compact_snapshot(self):
        apply_heartbeat_inventory_snapshot(
            node_id=self.node.id,
            inventory={
                "agent_version": "1.2.3",
                "primary_ip_address": "10.20.1.15",
                "network_inventory": network_payload(),
            },
        )

        self.node.refresh_from_db()
        self.assertEqual(str(self.node.ip_address), "10.20.1.15")
        self.assertEqual(self.node.network_inventory["schema_version"], 1)
        self.assertEqual(
            self.node.metadata["inventory"]["primary_mac_address"],
            "00:11:22:33:44:55",
        )

    def test_invalid_inventory_does_not_overwrite_valid_host_ip(self):
        apply_heartbeat_inventory_snapshot(
            node_id=self.node.id,
            inventory={
                "primary_ip_address": "127.0.0.1",
                "ip_addresses": ["127.0.0.1"],
            },
        )

        self.node.refresh_from_db()
        self.assertEqual(str(self.node.ip_address), "10.20.1.10")

    def test_structured_storage_snapshot_clears_legacy_capacity(self):
        self.node.metadata = {
            "inventory": {
                "disk_total_bytes": 338_700_000_000,
                "disk_used_bytes": 119_600_000_000,
                "disk_free_bytes": 219_100_000_000,
                "disk_count": 5,
            }
        }
        self.node.save(update_fields=["metadata"])

        apply_heartbeat_inventory_snapshot(
            node_id=self.node.id,
            inventory={
                "capabilities": ["storage_inventory_v1"],
                "local_storage_pools": [],
                "network_storage_pools": [],
                "disk_total_bytes": 0,
                "disk_used_bytes": 0,
                "disk_free_bytes": 0,
                "disk_count": 0,
            },
        )

        self.node.refresh_from_db()
        inventory = self.node.metadata["inventory"]
        self.assertEqual(inventory["local_storage_pools"], [])
        self.assertEqual(inventory["network_storage_pools"], [])
        self.assertEqual(inventory["disk_total_bytes"], 0)
        self.assertEqual(inventory["disk_used_bytes"], 0)
        self.assertEqual(inventory["disk_free_bytes"], 0)
        self.assertEqual(inventory["disk_count"], 0)

    def test_inventory_snapshot_preserves_monitor_metadata(self):
        self.node.metadata = {
            "metrics": {"cpu_usage": 12.5},
            "monitor_sample_timestamp": "2026-08-12T10:00:00Z",
            "inventory": {"cpu_cores": 4},
        }
        self.node.save(update_fields=["metadata"])

        apply_heartbeat_inventory_snapshot(
            node_id=self.node.id,
            inventory={
                "capabilities": ["storage_inventory_v1"],
                "storage_inventory_status": "ready",
                "local_storage_pools": [],
                "disk_total_bytes": 0,
            },
        )

        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["metrics"], {"cpu_usage": 12.5})
        self.assertEqual(
            self.node.metadata["monitor_sample_timestamp"],
            "2026-08-12T10:00:00Z",
        )
        self.assertEqual(self.node.metadata["inventory"]["cpu_cores"], 4)
        self.assertEqual(self.node.metadata["inventory"]["local_storage_pools"], [])

    @patch("apps.node.ws.uplink.sync_agent_source_host_by_id")
    @patch("apps.node.ws.uplink.record_node_available")
    @patch("apps.node.ws.uplink._should_process_full_inventory", return_value=True)
    @patch("apps.node.ws.uplink.redis_store.touch_ws_instance_alive")
    @patch("apps.node.ws.uplink.redis_store.touch_agent_location")
    def test_delayed_followup_does_not_reapply_stale_inventory(
        self,
        _mock_touch_location,
        _mock_touch_instance,
        _mock_should_process,
        _mock_record_available,
        _mock_sync_source,
    ):
        self.node.metadata = {
            "inventory": {
                "capabilities": ["storage_inventory_v1"],
                "storage_inventory_status": "ready",
                "local_storage_pools": [{"key": "local:new"}],
                "disk_total_bytes": 100,
            }
        }
        self.node.save(update_fields=["metadata"])

        _process_heartbeat_followup(
            node_id=self.node.id,
            inventory={
                "capabilities": ["storage_inventory_v1"],
                "storage_inventory_status": "ready",
                "local_storage_pools": [{"key": "local:old"}],
                "disk_total_bytes": 50,
            },
        )

        self.node.refresh_from_db()
        inventory = self.node.metadata["inventory"]
        self.assertEqual(inventory["local_storage_pools"], [{"key": "local:new"}])
        self.assertEqual(inventory["disk_total_bytes"], 100)


class NodeNetworkInventoryMigrationTests(TestCase):
    def test_migration_splits_legacy_connection_and_reported_host_ips(self):
        org = Organization.objects.create(key="migration-org", name="Migration Org")
        reported = Node.objects.create(
            organization=org,
            name="reported-agent",
            role=NodeRole.AGENT,
            ip_address="203.0.113.20",
            metadata={"inventory": {"primary_ip_address": "10.20.1.15"}},
        )
        missing = Node.objects.create(
            organization=org,
            name="legacy-agent",
            role=NodeRole.AGENT,
            ip_address="203.0.113.20",
        )

        migration = import_module("apps.node.migrations.0006_node_network_inventory")
        migration.split_host_and_connection_addresses(django_apps, None)

        reported.refresh_from_db()
        missing.refresh_from_db()
        self.assertEqual(str(reported.ip_address), "10.20.1.15")
        self.assertEqual(str(reported.connection_ip_address), "203.0.113.20")
        self.assertIsNone(missing.ip_address)
        self.assertEqual(str(missing.connection_ip_address), "203.0.113.20")
