"""Tests for Proxy bindings guard and detail endpoint."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.source.models import SourceResource
from apps.source.constants import ResourceType, MountStatus, ResourceStatus
from apps.storage.repositories.models import Repository


class ProxyBindingsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="proxy-bindings@test.local",
            email="proxy-bindings@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="proxy-bindings-org", name="Proxy Bindings Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)
        self.proxy = Node.objects.create(
            organization=self.org,
            name="proxy-1",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.50",
        )

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def _response_bound(self, response) -> dict:
        payload = response.data
        if isinstance(payload.get("bound"), dict):
            return payload["bound"]
        inner = payload.get("data") or {}
        if isinstance(inner.get("bound"), dict):
            return inner["bound"]
        return {}

    def test_proxy_repository_server_address_defaults_to_host_ip_and_can_be_overridden(self):
        response = self.client.get(
            f"/api/v1/node/nodes/{self.proxy.id}/", **self._headers()
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["effective_repository_server_address"], "10.0.0.50")
        self.assertEqual(response.data["repository_server_address_source"], "agent_reported")

        response = self.client.patch(
            f"/api/v1/node/nodes/{self.proxy.id}/",
            {"repository_server_address": "Repo-Proxy.Example.Test."},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["repository_server_address"], "repo-proxy.example.test")
        self.assertEqual(
            response.data["effective_repository_server_address"],
            "repo-proxy.example.test",
        )
        self.assertEqual(response.data["repository_server_address_source"], "proxy_override")

    def test_repository_server_address_rejects_urls_and_non_proxy_nodes(self):
        invalid = self.client.patch(
            f"/api/v1/node/nodes/{self.proxy.id}/",
            {"repository_server_address": "https://proxy.example.test:51515"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

        agent = Node.objects.create(
            organization=self.org,
            name="agent-1",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.60",
        )
        non_proxy = self.client.patch(
            f"/api/v1/node/nodes/{agent.id}/",
            {"repository_server_address": "10.0.0.61"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(non_proxy.status_code, status.HTTP_400_BAD_REQUEST)

    def test_offline_proxy_with_no_bindings_requires_force_cleanup(self):
        response = self.client.post(
            f"/api/v1/node/nodes/{self.proxy.id}/operations/",
            {"kind": "remove", "force": True},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.content)
        self.assertFalse(Node.objects.filter(id=self.proxy.id, is_deleted=False).exists())

    def test_proxy_with_target_nas_repository_cannot_be_deleted(self):
        Repository.objects.create(
            organization_id=self.org.id,
            name="target-nas",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy.id,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        response = self.client.delete(
            f"/api/v1/node/nodes/{self.proxy.id}/", **self._headers()
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._response_bound(response)["target_nas_repositories"], 1)
        self.assertTrue(Node.objects.filter(id=self.proxy.id, is_deleted=False).exists())

    def test_proxy_with_standalone_disk_repository_cannot_be_deleted(self):
        Repository.objects.create(
            organization_id=self.org.id,
            name="disk-1",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy.id,
            config={"proxy_node_dir": "/data/repo"},
        )
        response = self.client.delete(
            f"/api/v1/node/nodes/{self.proxy.id}/", **self._headers()
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._response_bound(response)["standalone_disk_repositories"], 1)

    def test_proxy_with_source_nas_cannot_be_deleted(self):
        SourceResource.objects.create(
            organization=self.org,
            name="source-nas",
            resource_type=ResourceType.NAS,
            bound_node=self.proxy,
            config={"server_address": "10.0.0.20", "share_path": "/export"},
        )
        response = self.client.delete(
            f"/api/v1/node/nodes/{self.proxy.id}/", **self._headers()
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._response_bound(response)["source_nas_resources"], 1)

    def test_bindings_endpoint_returns_three_groups(self):
        target = Repository.objects.create(
            organization_id=self.org.id,
            name="target-nas",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy.id,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
            estimated_usage_bytes=2048,
            storage_total_bytes=20 * 1024**3,
            storage_used_bytes=3 * 1024**3,
            storage_available_bytes=17 * 1024**3,
            storage_pool_key="nas:nfs:10.0.0.10:/backup",
            storage_mount_point="/mnt/hfl/target-nas",
            usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
            capacity_probe_status=Repository.MetricProbeStatus.SUCCESS,
        )
        Repository.objects.create(
            organization_id=self.org.id,
            name="disk-1",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy.id,
            config={"proxy_node_dir": "/data/repo"},
        )
        SourceResource.objects.create(
            organization=self.org,
            name="source-nas",
            resource_type=ResourceType.NFS,
            bound_node=self.proxy,
            config={"server_address": "10.0.0.20", "share_path": "/export"},
        )

        response = self.client.get(
            f"/api/v1/node/nodes/{self.proxy.id}/bindings/", **self._headers()
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(len(response.data["target_nas_repositories"]), 1)
        self.assertEqual(len(response.data["standalone_disk_repositories"]), 1)
        self.assertEqual(len(response.data["source_nas_resources"]), 1)
        self.assertEqual(
            response.data["target_nas_repositories"][0]["storage_pool_key"],
            target.storage_pool_key,
        )
        self.assertEqual(
            response.data["target_nas_repositories"][0]["storage_total_bytes"],
            20 * 1024**3,
        )
        self.assertEqual(
            response.data["target_nas_repositories"][0]["usage_probe_status"],
            Repository.MetricProbeStatus.SUCCESS,
        )
        self.assertEqual(
            response.data["target_nas_repositories"][0]["capacity_probe_status"],
            Repository.MetricProbeStatus.SUCCESS,
        )
        self.assertEqual(response.data["totals"], {
            "target_nas_repositories": 1,
            "standalone_disk_repositories": 1,
            "source_nas_resources": 1,
        })

        list_response = self.client.get(
            "/api/v1/node/nodes/?role=proxy&page=1&page_size=30",
            **self._headers(),
        )
        self.assertEqual(list_response.status_code, status.HTTP_200_OK, list_response.content)
        self.assertEqual(list_response.data["results"][0]["associated_repository_count"], 2)

    @patch("apps.source.services.interface.unmount_resource")
    def test_force_remove_proxy_cannot_bypass_source_nas_binding(self, mock_umount):
        resource = SourceResource.objects.create(
            organization=self.org,
            name="source-nas-force",
            resource_type=ResourceType.NAS,
            bound_node=self.proxy,
            mount_status=MountStatus.MOUNTED,
            mount_point="/mnt/nas-force",
            config={"server_address": "10.0.0.20", "share_path": "/export"},
        )
        response = self.client.post(
            f"/api/v1/node/nodes/{self.proxy.id}/operations/",
            {"kind": "remove", "force": True},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        resource.refresh_from_db()
        self.assertEqual(resource.bound_node_id, self.proxy.id)
        self.assertEqual(resource.mount_status, MountStatus.MOUNTED)
        self.assertEqual(resource.status, ResourceStatus.ACTIVE)
        self.assertTrue(Node.objects.filter(id=self.proxy.id, is_deleted=False).exists())
        mock_umount.assert_not_called()

    @patch("apps.source.services.interface.unmount_resource")
    def test_force_remove_proxy_cannot_bypass_target_nas_binding(self, mock_umount):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="target-nas-force",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy.id,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        response = self.client.post(
            f"/api/v1/node/nodes/{self.proxy.id}/operations/",
            {"kind": "remove", "force": True},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        repo.refresh_from_db()
        self.assertEqual(repo.bind_node_id, self.proxy.id)
        self.assertEqual(repo.health, Repository.Health.ONLINE)
        self.assertFalse(repo.config.get("needs_proxy"))
        self.assertTrue(Node.objects.filter(id=self.proxy.id, is_deleted=False).exists())
        mock_umount.assert_not_called()
