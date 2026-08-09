"""Identity settings API: Community empty socket vs enterprise extension."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.configuration.services.runtime_settings import (
    KEY_IDENTITY_EMAIL_SIGNUP,
    get_source,
    invalidate_runtime_settings_cache,
)
from apps.instance_settings.tests.helpers import (
    ensure_ops_staff_role,
    skip_if_extensions_loaded,
)


class PlatformIdentitySettingsCommunityTests(TestCase):
    """Empty-socket identity behavior (skipped when EE is mounted)."""

    path = "/api/v1/instance-settings/identity"

    def setUp(self):
        skip_if_extensions_loaded()
        invalidate_runtime_settings_cache()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="identity-admin@example.com",
            email="identity-admin@example.com",
            password="Pass1234",
            is_staff=True,
        )
        ensure_ops_staff_role(self.staff)
        self.client.force_authenticate(user=self.staff)

    def tearDown(self):
        invalidate_runtime_settings_cache()

    def _get(self):
        return self.client.get(self.path, HTTP_X_HFL_SITE_ROLE="ops")

    def _patch(self, payload):
        return self.client.patch(
            self.path,
            payload,
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

    def test_get_reports_enterprise_identity_disabled(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["enterprise_identity_enabled"])
        self.assertFalse(response.data["email_signup_enabled"])
        self.assertFalse(response.data["google_oauth_enabled"])
        self.assertFalse(response.data["turnstile_enabled"])

    def test_patch_rejects_ee_identity_fields_without_extension(self):
        response = self._patch({"email_signup_enabled": True})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "IDENTITY_EXTENSION_REQUIRED")
        self.assertNotEqual(get_source(KEY_IDENTITY_EMAIL_SIGNUP), "runtime")

    def test_patch_rejects_non_empty_iam_without_extension(self):
        response = self._patch(
            {
                "platform_ops_enabled": True,
                "iam": {"login_verification_code_minutes": 5},
            }
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "IDENTITY_EXTENSION_REQUIRED")

    def test_patch_still_allows_platform_ops_controls(self):
        response = self._patch(
            {
                "platform_ops_enabled": True,
                "platform_ops_allowed_cidrs": "10.0.0.0/8",
            }
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["platform_ops_enabled"])
        self.assertEqual(response.data["platform_ops_allowed_cidrs"], ["10.0.0.0/8"])

    def test_patch_ignores_empty_iam_object_on_community(self):
        response = self._patch(
            {
                "platform_ops_enabled": True,
                "iam": {},
            }
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["platform_ops_enabled"])

    @override_settings(TURNSTILE_ENABLED=True)
    def test_get_masks_turnstile_when_extension_missing(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["turnstile_enabled"])
        self.assertEqual(response.data["turnstile_site_key"], "")
        self.assertFalse(response.data["turnstile_secret_configured"])


class PlatformIdentitySettingsEnterpriseTests(TestCase):
    path = "/api/v1/instance-settings/identity"

    def setUp(self):
        invalidate_runtime_settings_cache()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="identity-ee@example.com",
            email="identity-ee@example.com",
            password="Pass1234",
            is_staff=True,
        )
        ensure_ops_staff_role(self.staff)
        self.client.force_authenticate(user=self.staff)
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        invalidate_runtime_settings_cache()

    def test_patch_persists_email_signup_when_extension_loaded(self):
        response = self.client.patch(
            self.path,
            {"email_signup_enabled": True},
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["enterprise_identity_enabled"])
        self.assertTrue(response.data["email_signup_enabled"])


class PlatformEmailSettingsCommunityTests(TestCase):
    path = "/api/v1/instance-settings/email"

    def setUp(self):
        skip_if_extensions_loaded()
        invalidate_runtime_settings_cache()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="smtp-community@example.com",
            email="smtp-community@example.com",
            password="Pass1234",
            is_staff=True,
        )
        ensure_ops_staff_role(self.staff)
        self.client.force_authenticate(user=self.staff)

    def tearDown(self):
        invalidate_runtime_settings_cache()

    def test_patch_rejects_smtp_without_extension(self):
        response = self.client.patch(
            self.path,
            {
                "backend": "django.core.mail.backends.smtp.EmailBackend",
                "host": "smtp.example.com",
                "port": 465,
                "use_tls": False,
                "use_ssl": True,
                "host_user": "mailer@example.com",
                "password": "secret",
                "from_email": "HyperFileLens <mailer@example.com>",
            },
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "IDENTITY_EXTENSION_REQUIRED")

    def test_email_test_rejects_without_extension(self):
        response = self.client.post(
            "/api/v1/instance-settings/email/test",
            {"recipient": "recipient@example.com"},
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "IDENTITY_EXTENSION_REQUIRED")


class PlatformEnvironmentSettingsCommunityTests(TestCase):
    path = "/api/v1/instance-settings/environment"

    def setUp(self):
        skip_if_extensions_loaded()
        invalidate_runtime_settings_cache()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="env-admin@example.com",
            email="env-admin@example.com",
            password="Pass1234",
            is_staff=True,
        )
        ensure_ops_staff_role(self.staff)
        self.client.force_authenticate(user=self.staff)

    def tearDown(self):
        invalidate_runtime_settings_cache()

    @override_settings(TURNSTILE_ENABLED=True)
    def test_effective_turnstile_stays_off_without_extension(self):
        response = self.client.get(self.path, HTTP_X_HFL_SITE_ROLE="ops")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["effective"]["turnstile_enabled"])
        self.assertEqual(response.data["sources"]["turnstile_enabled"], "extension")
