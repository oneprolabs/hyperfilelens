"""Node monitor ingest (Host) and community read-API gating."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.monitor.models import ResourceMetric
from apps.monitor.services.internal.node_metrics import ingest_node_monitor_sample
from apps.node.models import Node
from apps.node.models.base import NodeRole
from common.extension_loader import extensions_enabled


class NodeMonitorIngestTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="node-monitor-org", name="Node Monitor Org")
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-01",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"inventory": {"hostname": "agent-host", "os": "linux", "arch": "amd64"}},
        )

    def test_ingest_persists_resource_metric(self):
        ingest_node_monitor_sample(
            node=self.node,
            sample={
                "cpu": {"usage_percent": 9.0, "logical_cores": 2},
                "memory": {"percent": 33.0, "total": 100, "available": 67},
                "disks": [{"mountpoint": "/"}, {"mountpoint": "/data"}],
                "networks": [{"bytes_recv": 10, "bytes_sent": 20}],
            },
        )
        row = ResourceMetric.objects.filter(resource_id=str(self.node.id)).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.metrics.get("cpu_usage"), 9.0)
        self.assertEqual(row.metrics.get("memory_usage"), 33.0)
        self.node.refresh_from_db()
        inv = (self.node.metadata or {}).get("inventory") or {}
        self.assertEqual(inv.get("cpu_cores"), 2)
        self.assertEqual(inv.get("memory_total_bytes"), 100)
        self.assertEqual(inv.get("disk_count"), 2)

    def test_ingest_skips_duplicate_agent_sample_timestamp(self):
        sample = {
            "timestamp": "2026-08-12T10:00:00Z",
            "cpu": {"usage_percent": 9.0, "logical_cores": 2},
            "memory": {"percent": 33.0, "total": 100},
        }

        ingest_node_monitor_sample(node=self.node, sample=sample)
        self.node.refresh_from_db()
        ingest_node_monitor_sample(node=self.node, sample=sample)

        self.assertEqual(
            ResourceMetric.objects.filter(resource_id=str(self.node.id)).count(),
            1,
        )

    def test_ingest_skips_delayed_older_agent_sample(self):
        newer = {
            "timestamp": "2026-08-12T10:01:00Z",
            "cpu": {"usage_percent": 12.0, "logical_cores": 4},
        }
        older = {
            "timestamp": "2026-08-12T10:00:00Z",
            "cpu": {"usage_percent": 99.0, "logical_cores": 2},
        }

        ingest_node_monitor_sample(node=self.node, sample=newer)
        ingest_node_monitor_sample(node=self.node, sample=older)

        self.node.refresh_from_db()
        self.assertEqual(
            ResourceMetric.objects.filter(resource_id=str(self.node.id)).count(),
            1,
        )
        self.assertEqual(self.node.metadata["monitor_sample_timestamp"], newer["timestamp"])
        self.assertEqual(self.node.metadata["metrics"]["cpu_usage"], 12.0)
        self.assertEqual(self.node.metadata["inventory"]["cpu_cores"], 4)

    def test_ingest_replaces_legacy_timezone_naive_timestamp(self):
        metadata = dict(self.node.metadata or {})
        metadata["monitor_sample_timestamp"] = "2026-08-12T09:59:00"
        self.node.metadata = metadata
        self.node.save(update_fields=["metadata"])

        ingest_node_monitor_sample(
            node=self.node,
            sample={
                "timestamp": "2026-08-12T10:00:00Z",
                "cpu": {"usage_percent": 12.0},
            },
        )

        self.node.refresh_from_db()
        self.assertEqual(
            self.node.metadata["monitor_sample_timestamp"],
            "2026-08-12T10:00:00Z",
        )
        self.assertEqual(self.node.metadata["metrics"]["cpu_usage"], 12.0)

    def test_ingest_uses_latest_inventory_metadata(self):
        stale_node = Node.objects.get(pk=self.node.pk)
        latest_meta = dict(self.node.metadata or {})
        latest_meta["inventory"] = {
            "capabilities": ["storage_inventory_v1"],
            "storage_inventory_status": "ready",
            "local_storage_pools": [{"key": "local:root"}],
            "disk_total_bytes": 38_000_000_000,
        }
        Node.objects.filter(pk=self.node.pk).update(metadata=latest_meta)

        ingest_node_monitor_sample(
            node=stale_node,
            sample={
                "timestamp": "2026-08-12T10:01:00Z",
                "cpu": {"logical_cores": 4},
                "disks": [{"mountpoint": "/mnt/share", "total": 100_000_000_000}],
            },
        )

        self.node.refresh_from_db()
        inv = (self.node.metadata or {}).get("inventory") or {}
        self.assertEqual(inv.get("local_storage_pools"), [{"key": "local:root"}])
        self.assertEqual(inv.get("disk_total_bytes"), 38_000_000_000)
        self.assertEqual(inv.get("cpu_cores"), 4)

    def test_ingest_sums_disk_capacity_across_volumes(self):
        ingest_node_monitor_sample(
            node=self.node,
            sample={
                "cpu": {"logical_cores": 4},
                "disks": [
                    {
                        "mountpoint": "C:",
                        "total": 500_000_000_000,
                        "used": 200_000_000_000,
                        "free": 300_000_000_000,
                    },
                    {
                        "mountpoint": "D:\\",
                        "total": 1_000_000_000_000,
                        "used": 400_000_000_000,
                        "free": 600_000_000_000,
                    },
                ],
            },
        )
        self.node.refresh_from_db()
        inv = (self.node.metadata or {}).get("inventory") or {}
        self.assertEqual(inv.get("disk_total_bytes"), 1_500_000_000_000)
        self.assertEqual(inv.get("disk_used_bytes"), 600_000_000_000)
        self.assertEqual(inv.get("disk_free_bytes"), 900_000_000_000)
        self.assertEqual(inv.get("disk_count"), 2)

    def test_monitor_sample_does_not_override_structured_storage_inventory(self):
        self.node.metadata = {
            "inventory": {
                "capabilities": ["storage_inventory_v1"],
                "disk_total_bytes": 38_000_000_000,
                "disk_used_bytes": 7_200_000_000,
                "disk_free_bytes": 30_800_000_000,
                "disk_count": 1,
                "local_storage_pools": [{"key": "local:root"}],
                "network_storage_pools": [{"key": "network:smb:share"}],
            }
        }
        self.node.save(update_fields=["metadata"])

        ingest_node_monitor_sample(
            node=self.node,
            sample={
                "cpu": {"logical_cores": 4},
                "memory": {"total": 16_000_000_000},
                "disks": [
                    {
                        "mountpoint": "/",
                        "total": 38_000_000_000,
                        "used": 7_200_000_000,
                        "free": 30_800_000_000,
                    },
                    {
                        "mountpoint": "/mnt/share",
                        "total": 100_000_000_000,
                        "used": 38_000_000_000,
                        "free": 62_000_000_000,
                    },
                ],
            },
        )

        self.node.refresh_from_db()
        inv = (self.node.metadata or {}).get("inventory") or {}
        self.assertEqual(inv.get("disk_total_bytes"), 38_000_000_000)
        self.assertEqual(inv.get("disk_used_bytes"), 7_200_000_000)
        self.assertEqual(inv.get("disk_count"), 1)
        self.assertEqual(inv.get("cpu_cores"), 4)
        self.assertEqual(inv.get("memory_total_bytes"), 16_000_000_000)
        self.assertEqual(inv.get("local_storage_pools"), [{"key": "local:root"}])
        self.assertEqual(
            inv.get("network_storage_pools"),
            [{"key": "network:smb:share"}],
        )


class NodeMonitorReadApiCommunityTests(TestCase):
    """HTTP-level check: community process has no node-monitor read routes.

    URL-pattern gating (both on/off) is covered in ``test_monitor_url_gating``
    without requiring EE. This class only runs when the process itself is a
    community socket so Django's live URLconf matches that edition.
    """

    def setUp(self):
        if extensions_enabled():
            self.skipTest("live URLconf has extension routes; see test_monitor_url_gating")
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="node-monitor-community@test.local",
            email="node-monitor-community@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="node-monitor-community", name="Community Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)
        self.client.credentials(HTTP_X_ORG_KEY=self.org.key)

    def test_list_nodes_unavailable_without_extension(self):
        resp = self.client.get("/api/v1/monitors/nodes/", {"role": "agent"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_node_detail_unavailable_without_extension(self):
        resp = self.client.get("/api/v1/monitors/nodes/1/", {"hours": "1"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
