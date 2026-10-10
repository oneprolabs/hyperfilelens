"""Tests for GET /api/v1/auth/turnstile/config."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from apps.configuration.services.runtime_settings import (
    KEY_IDENTITY_TURNSTILE_IP_ALLOWLIST,
    invalidate_runtime_settings_cache,
    set_str_list,
)


class TurnstileConfigViewTests(TestCase):
    def setUp(self):
        invalidate_runtime_settings_cache()
        self.addCleanup(invalidate_runtime_settings_cache)

    @override_settings(TURNSTILE_ENABLED=True, TRUSTED_PROXY=False)
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    def test_exemption_is_login_only_ip_specific_and_not_cacheable(self, _identity):
        set_str_list(KEY_IDENTITY_TURNSTILE_IP_ALLOWLIST, ["203.0.113.10"])
        for ip, exempt in (("203.0.113.10", True), ("203.0.113.20", False)):
            with self.subTest(ip=ip):
                response = self.client.get("/api/v1/auth/turnstile/config", REMOTE_ADDR=ip)
                self.assertTrue(response.json()["data"]["enabled"])
                self.assertEqual(response.json()["data"]["login_exempt"], exempt)
                self.assertEqual(response["Cache-Control"], "private, no-store")
                self.assertNotIn("203.0.113.10", response.content.decode())

    @override_settings(TURNSTILE_ENABLED=False)
    def test_disabled_config(self):
        response = self.client.get("/api/v1/auth/turnstile/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], "0000")
        self.assertFalse(payload["data"]["enabled"])
        self.assertFalse(payload["data"]["configured"])
        self.assertNotIn("site_key", payload["data"])

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="test-secret-key",
    )
    def test_community_empty_socket_keeps_turnstile_off(self):
        response = self.client.get("/api/v1/auth/turnstile/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["data"]["enabled"])
        self.assertFalse(payload["data"]["configured"])
        self.assertNotIn("site_key", payload["data"])

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="test-secret-key",
    )
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    def test_enabled_config(self, _identity):
        response = self.client.get("/api/v1/auth/turnstile/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["data"]["enabled"])
        self.assertTrue(payload["data"]["configured"])
        self.assertEqual(payload["data"]["site_key"], "test-site-key")

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="",
    )
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    def test_incomplete_config_fails_closed(self, _identity):
        response = self.client.get("/api/v1/auth/turnstile/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["data"]["enabled"])
        self.assertFalse(payload["data"]["configured"])
        self.assertNotIn("site_key", payload["data"])
    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="test-secret-key",
    )
    def test_ops_site_reports_turnstile_disabled(self):
        response = self.client.get(
            "/api/v1/auth/turnstile/config",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["data"]["enabled"])
        self.assertFalse(payload["data"]["configured"])
        self.assertNotIn("site_key", payload["data"])

    def test_public_config_ignores_invalid_access_token_cookie(self):
        self.client.cookies["access_token"] = "not-a-valid-jwt"
        response = self.client.get("/api/v1/auth/turnstile/config")

        self.assertEqual(response.status_code, 200, response.content)

    def test_legacy_image_captcha_endpoints_are_removed(self):
        for path in (
            "/api/v1/auth/captcha-config",
            "/api/v1/auth/captcha",
            "/api/v1/auth/captcha/validate",
            "/api/v1/auth/captcha-fallback-report",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
