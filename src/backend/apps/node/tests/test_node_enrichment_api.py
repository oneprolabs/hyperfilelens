"""Tests for read-only node enrichment and lifecycle watch API."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal.node_lifecycle import enrich_node_row

User = get_user_model()


class NodeEnrichmentReadOnlyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="enrich-user@test.local",
            email="enrich-user@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-enrich",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            version="1.0.0",
        )

    @patch("apps.node.services.internal.node_lifecycle.advance_node_lifecycle")
    def test_enrich_node_row_does_not_advance(self, mock_advance):
        enrich_node_row(org=self.org, node=self.node, user=self.user)
        mock_advance.assert_not_called()

    @patch("apps.node.services.internal.node_lifecycle.advance_node_lifecycle")
    def test_list_nodes_does_not_advance(self, mock_advance):
        client = APIClient()
        client.force_authenticate(user=self.user)
        response = client.get(
            "/api/v1/node/nodes/",
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, 200)
        mock_advance.assert_not_called()

    def test_list_nodes_reports_compatible_agent_release(self):
        self.node.role = NodeRole.PROXY
        self.node.version = "1.0.0"
        self.node.metadata = {
            "inventory": {
                "os": "linux",
                "arch": "amd64",
                "os_version": "22.04",
            }
        }
        self.node.save(update_fields=["role", "version", "metadata"])

        with TemporaryDirectory() as media_root:
            release_dir = Path(media_root) / "agent-releases" / "1.1.0"
            release_dir.mkdir(parents=True)
            (
                release_dir
                / "hfl-agent-1.1.0-linux-amd64-ubuntu2204.tar.gz"
            ).write_bytes(b"x")
            with self.settings(MEDIA_ROOT=media_root):
                client = APIClient()
                client.force_authenticate(user=self.user)
                response = client.get(
                    "/api/v1/node/nodes/",
                    HTTP_X_ORG_KEY=self.org.key,
                )

        self.assertEqual(response.status_code, 200)
        release = response.data[0]["agent_release"]
        self.assertEqual(release["current_version"], "1.0.0")
        self.assertEqual(release["target_version"], "1.1.0")
        self.assertTrue(release["update_available"])
        self.assertTrue(release["upgrade_version_allowed"])


class NodeLifecycleWatchApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="watch-user@test.local",
            email="watch-user@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-watch",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            version="1.0.0",
            last_seen_at=timezone.now(),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("apps.node.services.internal.node_lifecycle.advance_node_lifecycle")
    def test_lifecycle_watch_returns_requested_nodes(self, mock_advance):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            payload={"target_version": "1.1.0"},
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        response = self.client.post(
            "/api/v1/node/nodes/lifecycle-watch/",
            {"node_ids": [self.node.id]},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, 200)
        mock_advance.assert_not_called()
        body = response.data
        self.assertEqual(len(body["nodes"]), 1)
        row = body["nodes"][0]
        self.assertEqual(row["id"], self.node.id)
        self.assertEqual(row["status"], Node.Status.ACTIVE)
        self.assertEqual(row["availability"], Node.Availability.ONLINE)
        self.assertEqual(row["lifecycle"]["task_id"], str(task.id))
