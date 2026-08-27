"""Installer-managed local platform Gateway tests."""

from __future__ import annotations

import io
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import platform_lens, provisioning
from apps.node.api.views.node import NodeViewSet
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.local_platform_gateway import (
    LOCAL_PLATFORM_GATEWAY_INSTALL_KEY,
    LOCAL_PLATFORM_GATEWAY_METADATA,
    LOCAL_PLATFORM_GATEWAY_TOKEN_NOTE,
    ensure_local_platform_gateway_token,
    platform_gateway_api_base,
    registration_metadata,
)


class LocalPlatformGatewayConfigTests(SimpleTestCase):
    @override_settings(FRONTEND_URL="https://console.example.com:11443/")
    def test_platform_gateway_api_base_returns_canonical_origin(self):
        self.assertEqual(
            platform_gateway_api_base(),
            "https://console.example.com:11443",
        )

    @override_settings(FRONTEND_URL="https://console.example.com/tenant")
    def test_platform_gateway_api_base_rejects_paths(self):
        with self.assertRaisesMessage(ValueError, "FRONTEND_URL"):
            platform_gateway_api_base()

    @override_settings(
        FRONTEND_URL="http://console.example.com:11443",
        HFL_INSECURE_TLS=False,
    )
    def test_platform_gateway_api_base_requires_https_in_strict_mode(self):
        with self.assertRaisesMessage(ValueError, "must use HTTPS"):
            platform_gateway_api_base()

    @override_settings(FRONTEND_URL="https://127.0.0.1:11443")
    def test_remote_platform_gateway_rejects_loopback_origin(self):
        with self.assertRaisesMessage(ValueError, "network-reachable"):
            platform_gateway_api_base(require_remote=True)

    @override_settings(FRONTEND_URL="https://0.0.0.0:11443")
    def test_remote_platform_gateway_rejects_unspecified_origin(self):
        with self.assertRaisesMessage(ValueError, "network-reachable"):
            platform_gateway_api_base(require_remote=True)

    def test_registration_metadata_requires_trusted_installer_state(self):
        untrusted = registration_metadata(LOCAL_PLATFORM_GATEWAY_METADATA)
        self.assertNotIn("install_key", untrusted)

        managed = registration_metadata(
            {"hostname": "gateway-host"},
            token_note=LOCAL_PLATFORM_GATEWAY_TOKEN_NOTE,
        )
        self.assertEqual(managed["install_key"], LOCAL_PLATFORM_GATEWAY_INSTALL_KEY)
        self.assertEqual(managed["hostname"], "gateway-host")

    def test_registration_metadata_preserves_same_version_commit(self):
        existing = {
            "agent_version": "1.2.3",
            "agent_commit": "ABC123",
            "inventory": {
                "agent_version": "1.2.3",
                "agent_commit": "ABC123",
            },
        }

        metadata = registration_metadata(
            {
                "agent_version": "1.2.3",
                "inventory": {"agent_version": "1.2.3"},
            },
            existing_metadata=existing,
        )

        self.assertEqual(metadata["agent_commit"], "abc123")
        self.assertEqual(metadata["inventory"]["agent_commit"], "abc123")

    def test_registration_metadata_does_not_reuse_commit_for_new_version(self):
        existing = {
            "agent_version": "1.2.2",
            "agent_commit": "old123",
            "inventory": {
                "agent_version": "1.2.2",
                "agent_commit": "old123",
            },
        }

        metadata = registration_metadata(
            {
                "agent_version": "1.2.3",
                "inventory": {"agent_version": "1.2.3"},
            },
            existing_metadata=existing,
        )

        self.assertNotIn("agent_commit", metadata)
        self.assertNotIn("agent_commit", metadata["inventory"])

    def test_registration_metadata_does_not_cross_pair_identity_layers(self):
        metadata = registration_metadata(
            {
                "agent_version": "1.2.2",
                "agent_commit": "old123",
                "inventory": {"agent_version": "1.2.3"},
            }
        )

        self.assertEqual(metadata["agent_version"], "1.2.3")
        self.assertNotIn("agent_commit", metadata)
        self.assertNotIn("agent_commit", metadata["inventory"])


@override_settings(FRONTEND_URL="https://console.example.com:11443")
class LocalPlatformGatewayEnrollmentTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = get_user_model().objects.create_user(
            username="gateway-owner@example.test",
            email="gateway-owner@example.test",
        )

    def test_token_is_reused(self):
        first = ensure_local_platform_gateway_token()
        second = ensure_local_platform_gateway_token()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.organization.key, platform_lens.PLATFORM_ORG_KEY)
        self.assertEqual(first.role, NodeRole.GATEWAY)
        self.assertEqual(first.gateway_scope, LensGatewayLink.GatewayScope.PLATFORM)

    def test_management_command_emits_machine_readable_enrollment(self):
        org = platform_lens.get_or_create_platform_org()
        managed = Node.objects.create(
            organization=org,
            name="managed-gateway",
            role=NodeRole.GATEWAY,
            metadata=dict(LOCAL_PLATFORM_GATEWAY_METADATA),
        )
        Node.objects.create(
            organization=org,
            name="partial-metadata-gateway",
            role=NodeRole.GATEWAY,
            metadata={"install_key": LOCAL_PLATFORM_GATEWAY_INSTALL_KEY},
        )
        stdout = io.StringIO()

        call_command("ensure_local_platform_gateway_enrollment", stdout=stdout)

        line = stdout.getvalue().strip()
        prefix = "HFL_LOCAL_PLATFORM_GATEWAY_ENROLLMENT="
        self.assertTrue(line.startswith(prefix))
        payload = json.loads(line.removeprefix(prefix))
        self.assertEqual(payload["org_key"], platform_lens.PLATFORM_ORG_KEY)
        self.assertEqual(payload["api_base"], "https://console.example.com:11443")
        self.assertEqual(
            payload["wss_url"],
            "wss://console.example.com:11443/ws/node/agent/",
        )
        self.assertTrue(payload["token"])
        self.assertEqual(payload["managed_node_ids"], [managed.id])

    @mock.patch(
        "apps.lens_bridge.services.provisioning.provision_gateway_lens_on_register",
        return_value=None,
    )
    def test_installer_metadata_survives_followup_heartbeat(self, mock_provision):
        token = ensure_local_platform_gateway_token()
        first_request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "role": NodeRole.GATEWAY,
                "name": "gateway-host",
                "version": "1.0.0",
                "os_name": "linux amd64",
                "metadata": {"hostname": "gateway-host"},
            },
            format="json",
            HTTP_X_ORG_KEY=token.organization.key,
            HTTP_X_NODE_TOKEN=token.token,
        )
        first_response = NodeViewSet.as_view({"post": "heartbeat"})(first_request)
        self.assertEqual(first_response.status_code, 200)
        node_credential = first_response.data["node_credential"]
        node = Node.objects.get(pk=first_response.data["node_id"])
        self.assertEqual(
            node.metadata["install_key"], LOCAL_PLATFORM_GATEWAY_INSTALL_KEY
        )

        second_request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": node.id,
                "role": NodeRole.GATEWAY,
                "name": "gateway-host",
                "version": "1.0.0",
                "os_name": "linux amd64",
                "metadata": {"hostname": "gateway-host", "agent_version": "1.0.0"},
            },
            format="json",
            HTTP_X_ORG_KEY=token.organization.key,
            HTTP_X_NODE_TOKEN=node_credential,
        )
        second_response = NodeViewSet.as_view({"post": "heartbeat"})(second_request)
        self.assertEqual(second_response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(
            node.metadata["install_key"], LOCAL_PLATFORM_GATEWAY_INSTALL_KEY
        )
        self.assertEqual(node.metadata["agent_version"], "1.0.0")
        self.assertEqual(
            mock_provision.call_args_list[0].kwargs["scope"],
            LensGatewayLink.GatewayScope.PLATFORM,
        )
        self.assertIsNone(mock_provision.call_args_list[1].kwargs["scope"])

    def test_unknown_scope_preserves_existing_non_platform_identity(self):
        org = platform_lens.get_or_create_platform_org()
        gateway = Node.objects.create(
            organization=org,
            name="external-gateway",
            role=NodeRole.GATEWAY,
        )
        LensGatewayLink.objects.create(
            organization=org,
            gateway=gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.EXTERNAL,
            sl_lensnode_uuid="a3b9975f-cded-4c0a-a754-ec6f954d2b2c",
        )

        result = provisioning.ensure_lensnode_for_gateway(
            org=org,
            gateway=gateway,
            scope=None,
        )

        result.refresh_from_db()
        self.assertEqual(result.scope, LensGatewayLink.GatewayScope.USER)
        self.assertEqual(result.origin, LensGatewayLink.Origin.EXTERNAL)

    def test_platform_to_private_conversion_is_rejected(self):
        org = platform_lens.get_or_create_platform_org()
        gateway = Node.objects.create(
            organization=org,
            name="converted-private-gateway",
            role=NodeRole.GATEWAY,
        )
        LensGatewayLink.objects.create(
            organization=org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            sl_lensnode_uuid="d896d7ee-9903-4d55-9050-44e3f93c0103",
            is_platform_default=True,
        )

        with self.assertRaises(ValidationError):
            provisioning.ensure_lensnode_for_gateway(
                org=org,
                gateway=gateway,
                owner_user=self.user,
                scope=LensGatewayLink.GatewayScope.USER,
            )

        link = LensGatewayLink.objects.get(gateway=gateway)
        self.assertEqual(link.scope, LensGatewayLink.GatewayScope.PLATFORM)
        self.assertIsNone(link.owner_user)
        self.assertTrue(link.is_platform_default)

    @mock.patch(
        "apps.lens_bridge.services.provisioning.sl_client.request_json",
        return_value={
            "uuid": "aa8cd5e8-364e-4cc5-816e-3c54fe72119f",
            "token": "lensnode-token",
        },
    )
    def test_first_installer_gateway_becomes_platform_default(self, _mock_sl):
        org = platform_lens.get_or_create_platform_org()
        gateway = Node.objects.create(
            organization=org,
            name="gateway-host",
            role=NodeRole.GATEWAY,
            metadata=dict(LOCAL_PLATFORM_GATEWAY_METADATA),
        )

        link = provisioning.ensure_lensnode_for_gateway(
            org=org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
        )

        self.assertTrue(link.is_platform_default)

    @mock.patch(
        "apps.lens_bridge.services.provisioning.sl_client.request_json",
        return_value={
            "uuid": "f077f894-38c9-4576-8de4-808cb320b67c",
            "token": "lensnode-token",
        },
    )
    def test_installer_gateway_does_not_override_existing_platform_default(
        self, _mock_sl
    ):
        org = platform_lens.get_or_create_platform_org()
        current_gateway = Node.objects.create(
            organization=org,
            name="current-default",
            role=NodeRole.GATEWAY,
        )
        current_default = LensGatewayLink.objects.create(
            organization=org,
            gateway=current_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            sl_lensnode_uuid="b1e3bfc3-ac2c-4721-803f-121849e92728",
            is_platform_default=True,
        )
        local_gateway = Node.objects.create(
            organization=org,
            name="gateway-host",
            role=NodeRole.GATEWAY,
            metadata=dict(LOCAL_PLATFORM_GATEWAY_METADATA),
        )

        local_link = provisioning.ensure_lensnode_for_gateway(
            org=org,
            gateway=local_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
        )

        current_default.refresh_from_db()
        self.assertTrue(current_default.is_platform_default)
        self.assertFalse(local_link.is_platform_default)
