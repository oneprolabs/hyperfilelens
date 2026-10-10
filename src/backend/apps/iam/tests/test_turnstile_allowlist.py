"""Password-login exemptions never trust client-controlled IP prefixes."""

from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.iam.services.turnstile_allowlist import (
    login_client_ip,
    normalize_allowlist,
)
from apps.iam.services.turnstile_verification import (
    missing_turnstile_fields,
    turnstile_required,
    verify_turnstile_for_action,
)


class TurnstileAllowlistValidationTests(SimpleTestCase):
    def test_normalizes_and_deduplicates_individual_addresses(self):
        self.assertEqual(
            normalize_allowlist(
                [
                    " 203.0.113.10 ",
                    "203.0.113.10",
                    "2001:0db8:0:0::1",
                    "::ffff:203.0.113.10",
                ]
            ),
            ["203.0.113.10", "2001:db8::1"],
        )
        self.assertEqual(normalize_allowlist([]), [])

    def test_rejects_invalid_types_networks_scopes_and_excessive_lists(self):
        for value in (
            None,
            "203.0.113.10",
            {},
            [10],
            [""],
            ["not-an-ip"],
            ["203.0.113.0/24"],
            ["fe80::1%eth0"],
            ["::1"] * 101,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_allowlist(value)

    @override_settings(TRUSTED_PROXY=False)
    def test_direct_request_ignores_all_forwarding_headers(self):
        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="203.0.113.20",
            HTTP_X_FORWARDED_FOR="203.0.113.10",
            HTTP_CF_CONNECTING_IP="203.0.113.10",
        )
        self.assertEqual(login_client_ip(request), "203.0.113.20")

    @override_settings(TRUSTED_PROXY=True)
    def test_uses_gateway_peer_not_forged_forwarded_prefix(self):
        request = RequestFactory().get(
            "/",
            HTTP_X_FORWARDED_FOR="203.0.113.10, 203.0.113.20",
            HTTP_CF_CONNECTING_IP="203.0.113.10",
        )
        self.assertEqual(login_client_ip(request), "203.0.113.20")

    @override_settings(TRUSTED_PROXY=True)
    def test_cloudflare_header_requires_a_cloudflare_peer(self):
        for peer in ("173.245.48.1", "2606:4700::1"):
            with self.subTest(peer=peer):
                request = RequestFactory().get(
                    "/",
                    HTTP_X_FORWARDED_FOR=f"203.0.113.30, {peer}",
                    HTTP_CF_CONNECTING_IP="2001:0db8::1",
                )
                self.assertEqual(login_client_ip(request), "2001:db8::1")

    @override_settings(TRUSTED_PROXY=True)
    def test_missing_or_invalid_trusted_ip_fails_closed(self):
        for headers in (
            {},
            {"HTTP_X_FORWARDED_FOR": "bad-ip"},
            {"HTTP_X_FORWARDED_FOR": "173.245.48.1"},
            {
                "HTTP_X_FORWARDED_FOR": "173.245.48.1",
                "HTTP_CF_CONNECTING_IP": "bad-ip",
            },
        ):
            with self.subTest(headers=headers):
                self.assertIsNone(login_client_ip(RequestFactory().get("/", **headers)))


@override_settings(TURNSTILE_ENABLED=True, TRUSTED_PROXY=False)
@patch(
    "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
    return_value=True,
)
@patch(
    "apps.configuration.services.runtime_settings.turnstile_ip_allowlist",
    return_value=["203.0.113.10", "2001:db8::1"],
)
class TurnstileLoginExemptionTests(SimpleTestCase):
    def test_allowlisted_login_skips_token_and_siteverify(self, _list, _identity):
        for ip in ("203.0.113.10", "2001:0db8::1"):
            with self.subTest(ip=ip):
                request = RequestFactory().post("/", REMOTE_ADDR=ip)
                self.assertFalse(turnstile_required(request, action="login"))
                self.assertEqual(
                    missing_turnstile_fields({}, request, action="login"), {}
                )
                with patch(
                    "apps.iam.services.turnstile_verification.validate_turnstile"
                ) as verify:
                    self.assertTrue(
                        verify_turnstile_for_action({}, request, action="login")
                    )
                    verify.assert_not_called()

    def test_non_allowlisted_login_still_requires_token(self, _list, _identity):
        request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.20")
        self.assertTrue(turnstile_required(request, action="login"))
        self.assertIn(
            "turnstile_token", missing_turnstile_fields({}, request, action="login")
        )

    def test_exemption_does_not_apply_to_registration_or_reset(self, _list, _identity):
        request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.10")
        for action in (
            "", "register", "register_code", "forgot_password", "email_login_send_code",
        ):
            with self.subTest(action=action):
                self.assertTrue(turnstile_required(request, action=action))
                self.assertIn(
                    "turnstile_token",
                    missing_turnstile_fields({}, request, action=action),
                )
                with patch(
                    "apps.iam.services.turnstile_verification.turnstile_configured",
                    return_value=True,
                ):
                    self.assertFalse(
                        verify_turnstile_for_action({}, request, action=action)
                    )

    def test_bad_or_empty_persisted_allowlist_never_exempts(self, values, _identity):
        request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.10")
        for entries in ([], ["bad-ip", "203.0.113.10"]):
            values.return_value = entries
            self.assertTrue(turnstile_required(request, action="login"))
