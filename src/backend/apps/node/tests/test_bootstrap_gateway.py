"""Tests for Data Gateway enrollment bootstrap API."""

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.iam.models import Organization
from apps.node.api.views.bootstrap import BootstrapGatewayView
from apps.node.models import NodeToken
from apps.node.models.base import NodeRole


class BootstrapGatewayViewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="gw-bootstrap-org", name="GW Bootstrap Org"
        )
        self.token_row = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.GATEWAY,
            token="gw-bootstrap-token",
            is_active=True,
            enrollment_mode=NodeToken.EnrollmentMode.LEGACY,
        )
        self.factory = APIRequestFactory()

    def _get(self, **extra):
        params = {
            "org": self.org.key,
            "token": self.token_row.token,
            "api_base": "https://console.example",
            **extra,
        }
        request = self.factory.get("/api/v1/node/enrollment/bootstrap-gateway", params)
        return BootstrapGatewayView.as_view()(request)

    def test_gateway_bootstrap_renders_and_calls_gateway_install(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("gw-bootstrap-org", body)
        self.assertIn("gw-bootstrap-token", body)
        self.assertIn('HFL_NODE_ROLE="gateway"', body)
        self.assertIn('HFL_INSECURE_TLS="1"', body)
        self.assertIn("gateway-install", body)
        self.assertIn('HFL_ENROLL_ARGS=(--yes "$@")', body)
        self.assertNotIn('" install', body)

    @override_settings(
        HFL_INSECURE_TLS=False,
        FRONTEND_URL="https://console.example",
    )
    def test_gateway_bootstrap_enforces_strict_tls_when_configured(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertIn('HFL_INSECURE_TLS="0"', response.content.decode("utf-8"))

    @override_settings(
        HFL_INSECURE_TLS=False,
        FRONTEND_URL="https://console.example",
    )
    def test_strict_gateway_bootstrap_rejects_a_different_api_base(self):
        response = self._get(api_base="https://other.example")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("[FAIL ]", body)
        self.assertIn("configured HTTPS tenant origin", body)

    def test_agent_token_rejected_for_gateway_bootstrap(self):
        agent_token = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="agent-only-token",
            is_active=True,
        )
        response = self._get(token=agent_token.token)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("[FAIL ]", body)
        self.assertIn("invalid, expired, or revoked", body)
        self.assertIn("copy the currently displayed install command", body)

    def test_used_gateway_token_still_returns_bootstrap_script(self):
        self.token_row.is_active = False
        self.token_row.used_at = timezone.now()
        self.token_row.save(update_fields=["is_active", "used_at"])

        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("gateway-install", body)
