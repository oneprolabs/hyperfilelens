"""Platform Gateway observability policy and authenticated delivery tests."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY
from apps.node.api.views.gateway_lens import GatewayLensConfigView
from apps.node.models import Node, NodeCredential, NodeToken
from apps.node.models.base import NodeRole
from apps.node.services.internal.gateway_observability import (
    gateway_observability_policy,
)


@override_settings(SENTRY_ENABLED=True, SENTRY_ENVIRONMENT="hfl-test")
class GatewayObservabilityPolicyTests(TestCase):
    def setUp(self) -> None:
        self.org = Organization.objects.create(
            key=PLATFORM_ORG_KEY, name="Platform Lens"
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="platform-gateway",
            role=NodeRole.GATEWAY,
            version="main-123abcd",
        )
        LensGatewayLink.objects.create(
            organization=self.org,
            gateway=self.node,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )

    @patch.dict(
        "os.environ",
        {
            "SENTRY_BACKEND_DSN": "https://public@sentry.example.com/25",
            "SOURCELENS_GIT_REF": "v0.20.0",
        },
        clear=False,
    )
    def test_platform_gateway_receives_error_tracking_policy(self) -> None:
        policy = gateway_observability_policy(self.node)

        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["environment"], "hfl-test")
        self.assertEqual(policy["traces_sample_rate"], 0.0)
        self.assertFalse(policy["send_default_pii"])
        self.assertEqual(policy["agent_release"], "hyperfilelens-agent@main-123abcd")
        self.assertEqual(
            policy["lensnode_release"],
            "hyperfilelens-lensnode@main-123abcd-sl0.20.0",
        )

    @override_settings(SENTRY_ENVIRONMENT="hfl-community")
    @patch.dict(
        "os.environ",
        {
            "SENTRY_BACKEND_DSN": "https://public@sentry.example.com/25",
            "SOURCELENS_GIT_REF": "v0.20.0",
        },
        clear=False,
    )
    def test_community_gateway_receives_error_tracking_policy(self) -> None:
        policy = gateway_observability_policy(self.node)

        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["environment"], "hfl-community")

    @patch.dict(
        "os.environ",
        {"SENTRY_BACKEND_DSN": "https://public@sentry.example.com/25"},
        clear=False,
    )
    def test_tenant_private_gateway_never_receives_platform_dsn(self) -> None:
        user = User.objects.create_user(username="tenant@example.com")
        tenant = Organization.objects.create(key="tenant-org", name="Tenant")
        gateway = Node.objects.create(
            organization=tenant,
            name="private-gateway",
            role=NodeRole.GATEWAY,
        )
        LensGatewayLink.objects.create(
            organization=tenant,
            gateway=gateway,
            owner_user=user,
            scope=LensGatewayLink.GatewayScope.USER,
        )

        self.assertEqual(gateway_observability_policy(gateway), {"enabled": False})

    @patch.dict(
        "os.environ",
        {"SENTRY_BACKEND_DSN": "https://public:private@sentry.example.com/25"},
        clear=False,
    )
    def test_private_credential_dsn_is_not_distributed(self) -> None:
        self.assertEqual(gateway_observability_policy(self.node), {"enabled": False})


@override_settings(SENTRY_ENABLED=True, SENTRY_ENVIRONMENT="hfl-test")
class GatewayLensConfigObservabilityTests(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.org = Organization.objects.create(
            key=PLATFORM_ORG_KEY, name="Platform Lens"
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="platform-gateway",
            role=NodeRole.GATEWAY,
            version="0.2.0",
        )
        LensGatewayLink.objects.create(
            organization=self.org,
            gateway=self.node,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )
        self.token = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.GATEWAY,
            token="platform-gateway-token",
            gateway_scope=LensGatewayLink.GatewayScope.PLATFORM,
        )
        self.node_credential = "hfln_platform-gateway-credential"
        credential = NodeCredential(
            organization=self.org,
            node=self.node,
            role=NodeRole.GATEWAY,
            installation_id="platform-gateway",
        )
        credential.set_secret(self.node_credential)
        credential.save()

    def _request(self, token: str):
        request = self.factory.get(
            "/api/v1/node/enrollment/gateway-lens-config",
            {"node_id": self.node.id},
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=token,
        )
        return GatewayLensConfigView.as_view()(request)

    @patch.dict(
        "os.environ",
        {
            "SENTRY_BACKEND_DSN": "https://public@sentry.example.com/25",
            "SOURCELENS_GIT_REF": "v0.20.0",
        },
        clear=False,
    )
    @patch(
        "apps.node.api.views.gateway_lens.provisioning.provision_gateway_lens_on_register"
    )
    def test_authenticated_response_contains_no_store_policy(self, provision) -> None:
        provision.return_value = {
            "lens_base_url": "https://lens.example.com",
            "lensnode_uuid": "26d1822b-3ccc-48f8-80f1-f4c0ae99e61e",
            "lensnode_token": "lens-token",
            "lensnode_name": "platform-lens",
            "workspace_root": "/workspace",
        }

        response = self._request(self.node_credential)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertTrue(response.data["observability"]["enabled"])
        self.assertNotIn("frontend_dsn", response.data["observability"])

    def test_invalid_node_token_is_rejected_before_provisioning(self) -> None:
        with patch(
            "apps.node.api.views.gateway_lens.provisioning.provision_gateway_lens_on_register"
        ) as provision:
            response = self._request("wrong-token")

        self.assertEqual(response.status_code, 401)
        provision.assert_not_called()

    def test_non_platform_gateway_token_is_rejected(self) -> None:
        wrong_scope = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.GATEWAY,
            token="non-platform-gateway-token",
            gateway_scope=LensGatewayLink.GatewayScope.USER,
        )
        with patch(
            "apps.node.api.views.gateway_lens.provisioning.provision_gateway_lens_on_register"
        ) as provision:
            response = self._request(wrong_scope.token)

        self.assertEqual(response.status_code, 401)
        provision.assert_not_called()
