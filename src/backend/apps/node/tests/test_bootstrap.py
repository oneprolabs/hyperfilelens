"""Tests for enrollment bootstrap API."""

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.iam.models import Organization
from apps.node.api.views.bootstrap import BootstrapView, _template_values
from apps.node.models import NodeInstallationMode, NodeToken
from apps.node.models.base import NodeRole


class BootstrapViewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="bootstrap-org", name="Bootstrap Org"
        )
        self.token_row = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="bootstrap-token-abc",
            is_active=True,
            enrollment_mode=NodeToken.EnrollmentMode.LEGACY,
        )
        self.factory = APIRequestFactory()

    def _get(self, script_type: str, **extra):
        params = {
            "type": script_type,
            "org": self.org.key,
            "role": "agent",
            "token": self.token_row.token,
            "api_base": "https://console.example",
            **extra,
        }
        request = self.factory.get("/api/v1/node/enrollment/bootstrap", params)
        return BootstrapView.as_view()(request)

    def test_linux_bootstrap_renders_credentials(self):
        response = self._get("linux")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("bootstrap-org", body)
        self.assertIn("bootstrap-token-abc", body)
        self.assertIn("hfl-enroll-linux-${HFL_ARCH}", body)
        self.assertIn("https://console.example", body)
        self.assertIn('HFL_INSECURE_TLS="1"', body)
        self.assertIn('HFL_ENROLL_ARGS=(--yes "$@")', body)

    def test_bootstrap_uses_mode_bound_to_token(self):
        self.token_row.installation_mode = NodeInstallationMode.USER
        self.token_row.save(update_fields=["installation_mode"])

        values = _template_values(
            self.org.key,
            NodeRole.AGENT,
            self.token_row.token,
            "https://console.example",
            self.token_row.installation_mode,
        )

        self.assertEqual(values["HFL_INSTALLATION_MODE"], "user")

    @override_settings(
        HFL_INSECURE_TLS=False,
        FRONTEND_URL="https://console.example",
    )
    def test_linux_bootstrap_enforces_strict_tls_when_configured(self):
        response = self._get("linux")

        self.assertEqual(response.status_code, 200)
        self.assertIn('HFL_INSECURE_TLS="0"', response.content.decode("utf-8"))

    @override_settings(
        HFL_INSECURE_TLS=False,
        FRONTEND_URL="https://console.example",
    )
    def test_strict_bootstrap_rejects_a_different_api_base(self):
        response = self._get("linux", api_base="http://attacker.example")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("[FAIL ]", body)
        self.assertIn("configured HTTPS tenant origin", body)

    def test_windows_bootstrap_renders(self):
        response = self._get("windows")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("$bin install", body)
        self.assertIn("bootstrap-token-abc", body)

    @override_settings(
        HFL_INSECURE_TLS=False,
        FRONTEND_URL="https://console.example",
    )
    def test_windows_bootstrap_enforces_strict_tls_when_configured(self):
        response = self._get("windows")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '$env:HFL_INSECURE_TLS = "0"',
            response.content.decode("utf-8"),
        )

    def test_used_token_still_returns_bootstrap_script(self):
        self.token_row.is_active = False
        self.token_row.used_at = timezone.now()
        self.token_row.save(update_fields=["is_active", "used_at"])

        response = self._get("linux")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("bootstrap-token-abc", body)
        self.assertIn("hfl-enroll-linux-${HFL_ARCH}", body)

    def test_invalid_token_returns_executable_error_script(self):
        response = self._get("linux", token="wrong-token")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertTrue(body.startswith("#!/usr/bin/env bash"))
        self.assertIn("[FAIL ]", body)
        self.assertIn("invalid or expired enrollment link", body)

    def test_missing_token_returns_executable_error_script(self):
        response = self._get("linux", token="")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertTrue(body.startswith("#!/usr/bin/env bash"))
        self.assertIn("[FAIL ]", body)
