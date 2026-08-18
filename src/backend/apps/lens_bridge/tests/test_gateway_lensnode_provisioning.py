import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import provisioning
from apps.node.models import Node
from apps.node.models.base import NodeRole


class DurableGatewayLensNodeProvisioningTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="lensnode-provision",
            name="LensNode provision",
        )
        self.user = get_user_model().objects.create_user(
            username="lensnode-provision@example.test",
            email="lensnode-provision@example.test",
        )
        self.gateway = Node.objects.create(
            organization=self.org,
            name="Provision gateway",
            role=NodeRole.GATEWAY,
        )
        self.link = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=self.gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
        )

    @mock.patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_records_intent_and_credentials_atomically(self, request_json):
        remote_uuid = uuid.uuid4()

        def response_for(method, path, **kwargs):
            if method == "GET":
                return []
            self.assertEqual(path, "/api/lens/admin/lensnodes/")
            lookup_name = kwargs["json_body"]["name"]
            self.assertIn(f"hfl-gateway-link-{self.link.id}", lookup_name)
            return {"uuid": str(remote_uuid), "token": "issued-token"}

        request_json.side_effect = response_for

        result = provisioning.ensure_lensnode_for_gateway(
            org=self.org,
            gateway=self.gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )

        self.assertEqual(result.sl_lensnode_uuid, remote_uuid)
        self.assertEqual(result.config_json["lensnode_token"], "issued-token")
        self.assertEqual(
            result.lensnode_provision_state_json["status"],
            "ready",
        )
        self.assertIsNone(result.lensnode_provision_claim_token)

    @mock.patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_recovers_remote_create_and_reissues_plaintext_token(
        self,
        request_json,
    ):
        remote_uuid = uuid.uuid4()
        lookup_name = f"gateway-hfl-gateway-link-{self.link.id}"
        self.link.lensnode_provision_state_json = {
            "lookup_name": lookup_name,
            "status": "error",
        }
        self.link.lensnode_provision_claim_token = uuid.uuid4()
        self.link.lensnode_provision_claimed_at = timezone.now() - timedelta(
            minutes=10
        )
        self.link.save()

        def response_for(method, path, **_kwargs):
            if method == "GET":
                return [{"uuid": str(remote_uuid), "name": lookup_name}]
            self.assertEqual(
                path,
                f"/api/lens/admin/lensnodes/{remote_uuid}/issue-token/",
            )
            return {"lensnode_uuid": str(remote_uuid), "token": "rotated-token"}

        request_json.side_effect = response_for

        result = provisioning.ensure_lensnode_for_gateway(
            org=self.org,
            gateway=self.gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )

        self.assertEqual(result.sl_lensnode_uuid, remote_uuid)
        self.assertEqual(result.config_json["lensnode_token"], "rotated-token")
        self.assertFalse(
            any(
                call.args[:2]
                == ("POST", "/api/lens/admin/lensnodes/")
                for call in request_json.call_args_list
            )
        )

    @mock.patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_live_lease_prevents_a_second_remote_create(self, request_json):
        self.link.lensnode_provision_claim_token = uuid.uuid4()
        self.link.lensnode_provision_claimed_at = timezone.now()
        self.link.lensnode_provision_state_json = {
            "lookup_name": f"gateway-hfl-gateway-link-{self.link.id}",
            "status": "provisioning",
        }
        self.link.save()

        with self.assertRaises(provisioning.LensNodeProvisionBusyError):
            provisioning.ensure_lensnode_for_gateway(
                org=self.org,
                gateway=self.gateway,
                owner_user=self.user,
                scope=LensGatewayLink.GatewayScope.USER,
            )

        request_json.assert_not_called()

    @mock.patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_recovery_never_rotates_token_owned_by_another_link(
        self,
        request_json,
    ):
        remote_uuid = uuid.uuid4()
        lookup_name = f"gateway-hfl-gateway-link-{self.link.id}"
        other_gateway = Node.objects.create(
            organization=self.org,
            name="Other gateway",
            role=NodeRole.GATEWAY,
        )
        LensGatewayLink.objects.create(
            organization=self.org,
            gateway=other_gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            sl_lensnode_uuid=remote_uuid,
        )
        self.link.lensnode_provision_state_json = {
            "lookup_name": lookup_name,
            "status": "error",
        }
        self.link.save(update_fields=["lensnode_provision_state_json"])
        request_json.return_value = [
            {"uuid": str(remote_uuid), "name": lookup_name}
        ]

        with self.assertRaises(provisioning.sl_client.LensBridgeError):
            provisioning.ensure_lensnode_for_gateway(
                org=self.org,
                gateway=self.gateway,
                owner_user=self.user,
                scope=LensGatewayLink.GatewayScope.USER,
            )

        request_json.assert_called_once()

    @mock.patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_private_gateway_owner_cannot_be_replaced(self, request_json):
        another_user = get_user_model().objects.create_user(
            username="other-lensnode-owner@example.test",
            email="other-lensnode-owner@example.test",
        )

        with self.assertRaisesMessage(
            Exception,
            "Private Data Gateway belongs to another user.",
        ):
            provisioning.ensure_lensnode_for_gateway(
                org=self.org,
                gateway=self.gateway,
                owner_user=another_user,
                scope=LensGatewayLink.GatewayScope.USER,
            )

        request_json.assert_not_called()

    @mock.patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_platform_gateway_create_defaults_capacity_bytes(self, request_json):
        """Regression: DB NOT NULL capacity_bytes is set when creating the link."""
        platform_org = Organization.objects.create(
            key="__platform_lens__",
            name="Platform Lens",
        )
        gateway = Node.objects.create(
            organization=platform_org,
            name="Local platform gateway",
            role=NodeRole.GATEWAY,
            metadata={
                "deployment_mode": "local-platform",
                "managed_by": "hfl-installer",
                "install_key": "local-platform-gateway",
            },
        )
        remote_uuid = uuid.uuid4()

        def response_for(method, path, **kwargs):
            if method == "GET":
                return []
            return {"uuid": str(remote_uuid), "token": "platform-token"}

        request_json.side_effect = response_for

        result = provisioning.ensure_lensnode_for_gateway(
            org=platform_org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
        )

        self.assertEqual(result.capacity_bytes, -1)
        self.assertEqual(result.scope, LensGatewayLink.GatewayScope.PLATFORM)
        self.assertEqual(result.sl_lensnode_uuid, remote_uuid)
