"""Tests for optional Cloudflare Turnstile enforcement."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.iam.services.turnstile_verification import (
    missing_turnstile_fields,
    turnstile_configured,
    turnstile_enabled,
    turnstile_required,
    verify_turnstile_for_action,
)


class TurnstileVerificationTests(SimpleTestCase):
    def setUp(self):
        patcher = patch(
            "apps.configuration.services.runtime_settings.turnstile_ip_allowlist",
            return_value=[],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _request(site_role: str = "tenant") -> MagicMock:
        request = MagicMock()
        request.META = {
            "HTTP_X_HFL_SITE_ROLE": site_role,
            "REMOTE_ADDR": "127.0.0.1",
        }
        return request

    @override_settings(TURNSTILE_ENABLED=False)
    def test_disabled_mode_requires_no_token(self):
        request = self._request()
        self.assertFalse(turnstile_enabled())
        self.assertEqual(missing_turnstile_fields({}, request), {})
        self.assertTrue(
            verify_turnstile_for_action({}, request, action="login"),
        )

    @override_settings(TURNSTILE_ENABLED=True)
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    def test_enabled_mode_requires_token(self, _identity):
        request = self._request()
        self.assertTrue(turnstile_enabled())
        self.assertTrue(turnstile_required(request))
        self.assertIn("turnstile_token", missing_turnstile_fields({}, request))
        self.assertEqual(
            missing_turnstile_fields({"turnstile_token": "tok"}, request),
            {},
        )

    @override_settings(TURNSTILE_ENABLED=True)
    @patch("apps.iam.services.turnstile_verification.validate_turnstile")
    def test_ops_site_never_requires_or_verifies_turnstile(self, mock_validate):
        request = self._request("ops")

        self.assertFalse(turnstile_required(request))
        self.assertEqual(missing_turnstile_fields({}, request), {})
        self.assertTrue(verify_turnstile_for_action({}, request, action="login"))
        mock_validate.assert_not_called()

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="secret-key",
        FRONTEND_URL="https://app.example.com",
    )
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    @patch(
        "apps.iam.services.turnstile_verification.validate_turnstile",
        return_value=True,
    )
    def test_enabled_mode_binds_token_to_action_and_hostname(
        self, mock_validate, _identity
    ):
        request = self._request()

        with (
            patch(
                "apps.configuration.services.runtime_settings.turnstile_site_key",
                return_value="site-key",
            ),
            patch(
                "apps.configuration.services.runtime_settings.turnstile_secret_key",
                return_value="secret-key",
            ),
        ):
            self.assertTrue(turnstile_configured())
            self.assertTrue(
                verify_turnstile_for_action(
                    {"turnstile_token": "token-value"},
                    request,
                    action="login",
                ),
            )
        mock_validate.assert_called_once_with(
            "token-value",
            "127.0.0.1",
            expected_action="login",
            expected_hostname="app.example.com",
        )

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="",
    )
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    def test_enabled_mode_rejects_incomplete_configuration(self, _identity):
        with (
            patch(
                "apps.configuration.services.runtime_settings.turnstile_site_key",
                return_value="site-key",
            ),
            patch(
                "apps.configuration.services.runtime_settings.turnstile_secret_key",
                return_value="",
            ),
        ):
            self.assertFalse(turnstile_configured())
            self.assertFalse(
                verify_turnstile_for_action(
                    {"turnstile_token": "token-value"},
                    self._request(),
                    action="login",
                ),
            )
