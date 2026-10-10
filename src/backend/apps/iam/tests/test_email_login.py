from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.iam.profile_models import Profile
from apps.configuration.services.runtime_settings import (
    KEY_IDENTITY_TURNSTILE_IP_ALLOWLIST,
    invalidate_runtime_settings_cache,
    set_str_list,
)


@override_settings(TURNSTILE_ENABLED=True, TRUSTED_PROXY=False)
class AllowlistedEmailLoginTests(APITestCase):
    def setUp(self):
        invalidate_runtime_settings_cache()
        self.addCleanup(invalidate_runtime_settings_cache)
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user(
            username="allowlist-login", email="allowlist@example.com",
            password="CorrectPass123", is_active=True,
        )
        set_str_list(KEY_IDENTITY_TURNSTILE_IP_ALLOWLIST, ["203.0.113.10"])

    def _login(self, ip="203.0.113.10", password="CorrectPass123", **headers):
        return self.client.post(
            reverse("email_login"),
            {"email": self.user.email, "password": password},
            format="json", REMOTE_ADDR=ip, **headers,
        )

    def test_allowlisted_login_accepts_credentials_without_token(self):
        with patch("apps.iam.services.turnstile_verification.validate_turnstile") as verify:
            response = self._login()
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data["data"]["user"]["id"], self.user.id)
            self.assertEqual(self.client.session["pending_user_id"], self.user.id)
            verify.assert_not_called()

    def test_allowlisted_login_does_not_bypass_password_or_account_status(self):
        response = self._login(password="WrongPass123")
        self.assertEqual(response.data["error"]["error_code"], "INVALID_PASSWORD")
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self._login()
        self.assertNotEqual(response.status_code, status.HTTP_200_OK)

    def test_non_allowlisted_or_removed_ip_requires_token(self):
        response = self._login(ip="203.0.113.20")
        self.assertIn("turnstile_token", response.data["error"]["fields"])
        set_str_list(KEY_IDENTITY_TURNSTILE_IP_ALLOWLIST, [])
        response = self._login()
        self.assertIn("turnstile_token", response.data["error"]["fields"])

    def test_allowlisted_ip_still_obeys_password_lockout(self):
        for _ in range(3):
            response = self._login(password="WrongPass123")
        self.assertEqual(response.data["error"]["error_code"], "ACCOUNT_LOCKED")
        response = self._login()
        self.assertEqual(response.data["error"]["error_code"], "ACCOUNT_LOCKED")

    @override_settings(TRUSTED_PROXY=True)
    def test_forged_forwarding_prefix_cannot_exempt_login(self):
        response = self._login(
            HTTP_X_FORWARDED_FOR="203.0.113.10, 203.0.113.20",
            HTTP_CF_CONNECTING_IP="203.0.113.10",
        )
        self.assertIn("turnstile_token", response.data["error"]["fields"])


class EmailLoginApiTests(APITestCase):
    def _payload(self, email: str, password: str = "WrongPass123") -> dict:
        return {
            "email": email,
            "password": password,
        }

    def test_pending_registration_user_returns_not_registered(self):
        email = "pending-login@example.com"
        user = User.objects.create_user(
            username="pending-login",
            email=email,
            password="unused",
            is_active=False,
        )
        Profile.objects.create(user=user, registration_completed=False)

        response = self.client.post(
            reverse("email_login"),
            self._payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "1001")
        self.assertEqual(response.data["error"]["error_code"], "EMAIL_NOT_REGISTERED")
        self.assertIn("email", response.data["error"]["fields"])

    def test_active_user_with_wrong_password_returns_password_error(self):
        email = "active-login@example.com"
        User.objects.create_user(
            username="active-login",
            email=email,
            password="CorrectPass123",
            is_active=True,
        )

        response = self.client.post(
            reverse("email_login"),
            self._payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "1001")
        self.assertEqual(response.data["error"]["error_code"], "INVALID_PASSWORD")
        self.assertIn("password", response.data["error"]["fields"])

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="",
    )
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    def test_incomplete_turnstile_configuration_fails_closed(self, _identity):
        response = self.client.post(
            reverse("email_login"),
            {
                "email": "anyone@example.com",
                "password": "Pass1234",
                "turnstile_token": "token",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["error"]["error_code"],
            "TURNSTILE_MISCONFIGURED",
        )

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="",
    )
    def test_ops_site_login_bypasses_turnstile(self):
        email = "ops-login@example.com"
        User.objects.create_user(
            username=email,
            email=email,
            password="CorrectPass123",
            is_active=True,
            is_staff=True,
        )

        response = self.client.post(
            reverse("email_login"),
            self._payload(email, password="CorrectPass123"),
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")
        self.assertTrue(response.data["data"]["user"]["is_staff"])
        self.assertEqual(response.data["data"]["available_orgs"], [])
        self.assertIn("access_token", response.cookies)
        self.assertIn("refresh_token", response.cookies)
