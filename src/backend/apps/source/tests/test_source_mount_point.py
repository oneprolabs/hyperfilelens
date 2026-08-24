"""Tests for NAS source mount point resolution across proxy changes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node import agent_paths
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.source.services.internal.nas_agent import (
    build_nas_agent_payload,
    dispatch_nas_agent_task,
)
from apps.source.services.interface import test_draft_connection

MOUNTS_ROOT = agent_paths.agent_mounts_dir()


def custom_mount(leaf: str) -> str:
    return f"{MOUNTS_ROOT}/custom/{leaf}"


class SourceMountPointResolutionTests(SimpleTestCase):
    def test_prefers_config_path_over_invalid_stored_mount_point(self):
        resource = SourceResource(
            id=5,
            mount_point="/export/backup",
            config={"path": custom_mount("nfs-data")},
        )
        self.assertEqual(resource.effective_mount_point(), custom_mount("nfs-data"))

    def test_falls_back_to_canonical_when_stored_and_config_invalid(self):
        resource = SourceResource(
            id=7,
            mount_point="/export/backup",
            config={"path": "/mnt/legacy"},
        )
        self.assertEqual(resource.effective_mount_point(), agent_paths.source_mount_point(7))

    def test_ignores_windows_stored_path_after_proxy_change(self):
        resource = SourceResource(
            id=9,
            mount_point=r"C:\ProgramData\Hyperfilelens\hfl-agent\mounts\custom\data",
            config={"path": custom_mount("smb-share")},
        )
        self.assertEqual(resource.effective_mount_point(), custom_mount("smb-share"))

    def test_build_payload_uses_resolved_mount_point(self):
        resource = SourceResource(
            id=11,
            mount_point="/export/backup",
            config={
                "protocol": "nfs",
                "server": "10.0.0.20",
                "export_path": "/export/backup",
                "path": custom_mount("nfs-export"),
            },
        )
        payload = build_nas_agent_payload(resource=resource)
        self.assertEqual(payload["mount_point"], custom_mount("nfs-export"))

    def test_build_payload_uses_canonical_mount_when_config_path_invalid(self):
        resource = SourceResource(
            id=15,
            mount_point="/mnt/hyperfilelens/source-15",
            config={
                "protocol": "nfs",
                "server": "10.0.0.20",
                "export_path": "/export/data",
                "path": "/mnt/hf1/nfs/10.0.0.20_export",
            },
        )
        payload = build_nas_agent_payload(resource=resource)
        self.assertEqual(payload["mount_point"], agent_paths.source_mount_point(15))

    def test_build_payload_uses_bound_proxy_agent_root(self):
        node = Node(
            metadata={"inventory": {"root_path": "/var/lib/hyperfilelens-agent"}},
        )
        resource = SourceResource(
            id=32,
            bound_node=node,
            config={
                "protocol": "nfs",
                "server": "192.168.8.82",
                "export_path": "/nfs-share1",
            },
        )
        payload = build_nas_agent_payload(resource=resource)
        self.assertEqual(
            payload["mount_point"],
            agent_paths.source_mount_point(
                32, data_dir="/var/lib/hyperfilelens-agent"
            ),
        )

    def test_invalid_bound_proxy_inventory_uses_default_agent_root(self):
        resource = SourceResource(
            id=33,
            bound_node=Node(metadata={"inventory": "legacy-value"}),
            config={"protocol": "nfs"},
        )

        self.assertEqual(
            resource.effective_mount_point(),
            agent_paths.source_mount_point(33),
        )


class DraftConnectionMountPointTests(SimpleTestCase):
    @mock.patch("apps.source.services.interface.run_connection_test")
    @mock.patch("apps.source.services.interface.Node.objects.filter")
    def test_uses_bound_proxy_agent_root(self, node_filter, run_connection_test):
        node_filter.return_value.first.return_value = SimpleNamespace(
            id=17,
            role=NodeRole.PROXY,
            metadata={"inventory": {"root_path": "/var/lib/hfl-agent"}},
        )
        run_connection_test.return_value = {"success": True}

        result = test_draft_connection(
            organization_id=3,
            bound_node_id=17,
            resource_type=ResourceType.NAS,
        )

        self.assertTrue(result["success"])
        mount_point = run_connection_test.call_args.kwargs["mount_point_override"]
        self.assertTrue(mount_point.startswith("/var/lib/hfl-agent/mounts/"))

    @mock.patch("apps.source.services.interface.run_connection_test")
    @mock.patch("apps.source.services.interface.Node.objects.filter")
    def test_invalid_inventory_uses_default_agent_root(
        self,
        node_filter,
        run_connection_test,
    ):
        node_filter.return_value.first.return_value = SimpleNamespace(
            id=18,
            role=NodeRole.PROXY,
            metadata={"inventory": ["legacy-value"]},
        )
        run_connection_test.return_value = {"success": True}

        result = test_draft_connection(
            organization_id=3,
            bound_node_id=18,
            resource_type=ResourceType.NAS,
        )

        self.assertTrue(result["success"])
        mount_point = run_connection_test.call_args.kwargs["mount_point_override"]
        self.assertTrue(mount_point.startswith(agent_paths.agent_mounts_dir()))


class ExplainNasMountPointErrorTests(SimpleTestCase):
    def test_explains_invalid_legacy_config_path(self):
        from apps.source.services.internal.nas_agent import explain_nas_mount_point_error

        resource = SourceResource(
            id=3,
            config={"path": "/mnt/hf1/nfs/host_export"},
        )
        message = explain_nas_mount_point_error(
            resource=resource,
            agent_message="invalid mount_point",
            payload_mount_point=agent_paths.source_mount_point(3),
        )
        self.assertIn("/mnt/hf1/nfs/host_export", message)
        self.assertIn("/opt/hyperfilelens-agent/mounts/", message)

    @mock.patch("apps.source.services.internal.nas_agent.run_agent_task_sync")
    def test_nas_password_is_delivery_only_and_not_persisted_in_task_payload(self, run_sync):
        run_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            task=SimpleNamespace(id="task-1", status="success"),
        )
        payload = {
            "resource_id": 7,
            "protocol": "smb",
            "server": "192.0.2.10",
            "share": "backup",
            "mount_point": custom_mount("secure"),
            "username": "nas-user",
            "password": "must-not-persist",
        }

        dispatch_nas_agent_task(
            node=SimpleNamespace(id=9, organization_id=3),
            kind="nas.mount",
            payload=payload,
            correlation_type="source.mount",
            correlation_id="7",
        )

        kwargs = run_sync.call_args.kwargs
        self.assertEqual(kwargs["payload"]["password"], "must-not-persist")
        self.assertNotIn("password", kwargs["persisted_payload"])
        self.assertNotIn("password", kwargs["persisted_payload"]["nas"])


class SourceMountPointAfterProxyChangeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="source-mount-proxy@test.local",
            email="source-mount-proxy@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="source-mount-proxy-org",
            name="Source Mount Proxy Org",
        )
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
            ip_address="10.0.0.41",
        )
        self.proxy_b = Node.objects.create(
            organization=self.org,
            name="proxy-b",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.42",
        )

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    @mock.patch("apps.source.services.interface.schedule_remount_after_proxy_change")
    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_test_connection_after_proxy_change_uses_config_mount_path(
        self,
        mock_dispatch,
        mock_schedule,
    ):
        custom_path = custom_mount("nfs-host_export")
        resource = SourceResource.objects.create(
            organization=self.org,
            name="nas-after-proxy-change",
            resource_type=ResourceType.NAS,
            bound_node=self.proxy_a,
            mount_point=r"C:\ProgramData\Hyperfilelens\hfl-agent\mounts\custom\stale",
            config={
                "protocol": "nfs",
                "server": "10.0.0.20",
                "export_path": "/export/data",
                "path": custom_path,
            },
        )
        response = self.client.patch(
            f"/api/v1/source/resources/{resource.id}/",
            {"bound_node_id": self.proxy_b.id},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200, response.content)

        resource.refresh_from_db()
        self.assertEqual(resource.bound_node_id, self.proxy_b.id)
        self.assertEqual(resource.mount_point, "")

        mock_dispatch.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            task=None,
            result={
                "mount_point": custom_path,
                "space_info": {
                    "total_bytes": 1_000,
                    "used_bytes": 100,
                    "free_bytes": 900,
                },
            },
        )

        test_resp = self.client.post(
            f"/api/v1/source/resources/{resource.id}/test-connection/",
            **self._headers(),
        )
        self.assertEqual(test_resp.status_code, 200, test_resp.content)
        self.assertTrue(test_resp.data["success"])

        _, kwargs = mock_dispatch.call_args
        self.assertEqual(kwargs["node"].id, self.proxy_b.id)
        self.assertEqual(kwargs["payload"]["mount_point"], custom_path)

        resource.refresh_from_db()
        self.assertEqual(resource.mount_point, custom_path)
