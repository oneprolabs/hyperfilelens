"""Tests for the storage-side Proxy binding guard."""

from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.nas_repository import (
    check_proxy_nas_repository,
    initialize_proxy_nas_repository,
)


class StorageRepositoryProxyBindingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="storage-proxy-bind@test.local",
            email="storage-proxy-bind@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="storage-proxy-bind-org", name="Storage Proxy Bind Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)
        self.proxy_a = Node.objects.create(
            organization=self.org,
            name="proxy-a",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.31",
        )
        self.proxy_b = Node.objects.create(
            organization=self.org,
            name="proxy-b",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.32",
        )

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def test_proxy_fs_cannot_unbind_existing_proxy(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="disk-1",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy_a.id,
            config={"proxy_node_dir": "/data/repo"},
        )
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/",
            {"bind_node_id": None},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        repo.refresh_from_db()
        self.assertEqual(repo.bind_node_id, self.proxy_a.id)

    def test_proxy_fs_cannot_change_physical_directory(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="disk-immutable-path",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy_a.id,
            config={"proxy_node_dir": "/data/repo"},
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/",
            {"config": {"proxy_node_dir": "/data/other-repo"}},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        repo.refresh_from_db()
        self.assertEqual(repo.config["proxy_node_dir"], "/data/repo")

    @mock.patch("apps.storage.services.interface.enqueue_repository_usage_refresh")
    def test_proxy_fs_can_replace_proxy(self, enqueue_usage):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="disk-1",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy_a.id,
            config={"proxy_node_dir": "/data/repo"},
        )
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/",
            {"bind_node_id": self.proxy_b.id},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        repo.refresh_from_db()
        self.assertEqual(repo.bind_node_id, self.proxy_b.id)
        enqueue_usage.assert_called_once_with(
            organization_id=self.org.id,
            repository_ids=[repo.id],
            force=True,
            trigger="storage.repository.update",
        )

    @mock.patch("apps.storage.services.interface.sync_repository_usage")
    @mock.patch("apps.storage.services.interface.enqueue_repository_usage_refresh")
    def test_proxy_fs_quota_update_queues_usage_refresh_without_syncing(
        self,
        enqueue_usage,
        sync_usage,
    ):
        sync_usage.side_effect = AssertionError("quota updates must not sync usage inline")
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="disk-1",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy_a.id,
            config={"proxy_node_dir": "/data/repo"},
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/",
            {
                "name": "disk-1-renamed",
                "config": {
                    "quota_gb": 10,
                    "quota_alert_enabled": True,
                    "quota_alert_threshold": 80,
                },
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        repo.refresh_from_db()
        self.assertEqual(repo.name, "disk-1-renamed")
        self.assertEqual(repo.config["quota_gb"], 10)
        self.assertEqual(repo.config["quota_alert_enabled"], True)
        self.assertEqual(repo.config["quota_alert_threshold"], 80)
        self.assertEqual(repo.capacity_bytes, 10 * 1024**3)
        sync_usage.assert_not_called()
        enqueue_usage.assert_called_once_with(
            organization_id=self.org.id,
            repository_ids=[repo.id],
            force=True,
            trigger="storage.repository.update",
        )

    def test_target_nas_cannot_unbind_existing_proxy(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="target-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy_a.id,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/",
            {"bind_node_id": None},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        repo.refresh_from_db()
        self.assertEqual(repo.bind_node_id, self.proxy_a.id)

    def test_nas_cannot_change_physical_location_or_protocol(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="target-nas-immutable-location",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/",
            {
                "nas_protocol": Repository.NasProtocol.SMB,
                "config": {
                    "server_address": "10.0.0.11",
                    "share_path": "/other-backup",
                },
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        repo.refresh_from_db()
        self.assertEqual(repo.nas_protocol, Repository.NasProtocol.NFS)
        self.assertEqual(repo.config["server_address"], "10.0.0.10")
        self.assertEqual(repo.config["share_path"], "/backup")

    @mock.patch("apps.storage.services.internal.nas_repository.run_agent_task_sync")
    def test_target_nas_initializer_persists_agent_mount_point(self, run_agent_task_sync):
        real_mount = "/mnt/hfl/storage-repositories/repo-34-node-45"
        run_agent_task_sync.return_value = mock.Mock(
            task=mock.Mock(status="success", last_error=""),
            result={"mount_point": real_mount},
            ok=True,
        )
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="target-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.SMB,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy_a.id,
            config={
                "server_address": "192.168.8.82",
                "share_path": "/smb-share",
                "smb_username": "u",
                "smb_password": "p",
            },
        )

        initialize_proxy_nas_repository(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.config["proxy_mount_path"], real_mount)
        self.assertEqual(run_agent_task_sync.call_args.kwargs["kind"], "repo.initialize")

        run_agent_task_sync.reset_mock()
        check_proxy_nas_repository(repo)
        self.assertEqual(run_agent_task_sync.call_args.kwargs["kind"], "repo.status")

    def test_proxy_fs_rejects_non_proxy_bind_node(self):
        agent = Node.objects.create(
            organization=self.org,
            name="agent-x",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.33",
        )
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="disk-1",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy_a.id,
            config={"proxy_node_dir": "/data/repo"},
        )
        response = self.client.patch(
            f"/api/v1/storage/repositories/{repo.id}/",
            {"bind_node_id": agent.id},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
