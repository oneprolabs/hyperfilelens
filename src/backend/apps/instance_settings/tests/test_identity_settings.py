"""Identity settings API: Community empty socket vs enterprise extension."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.configuration.services.runtime_settings import (
    KEY_IDENTITY_EMAIL_SIGNUP,
    KEY_IDENTITY_TURNSTILE_IP_ALLOWLIST,
    get_source,
    invalidate_runtime_settings_cache,
    turnstile_ip_allowlist,
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

    def test_community_cannot_configure_turnstile_allowlist(self):
        response = self._patch({"turnstile_ip_allowlist": ["203.0.113.10"]})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotEqual(get_source(KEY_IDENTITY_TURNSTILE_IP_ALLOWLIST), "runtime")
        self.assertEqual(self._get().data["turnstile_ip_allowlist"], [])

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

    @patch.dict("os.environ", {"TURNSTILE_ENABLED": ""})
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

    def _patch(self, payload):
        return self.client.patch(
            self.path, payload, format="json", HTTP_X_HFL_SITE_ROLE="ops",
        )

    def test_allowlist_defaults_empty_and_round_trips_normalized_values(self):
        response = self.client.get(self.path, HTTP_X_HFL_SITE_ROLE="ops")
        self.assertEqual(response.data["turnstile_ip_allowlist"], [])
        response = self._patch({
            "turnstile_ip_allowlist": ["203.0.113.10", " 2001:0db8::1 ", "203.0.113.10"],
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["turnstile_ip_allowlist"], ["203.0.113.10", "2001:db8::1"])
        self.assertTrue(response.data["has_runtime_override"])
        response = self.client.get(self.path, HTTP_X_HFL_SITE_ROLE="ops")
        self.assertEqual(response.data["turnstile_ip_allowlist"], turnstile_ip_allowlist())

    def test_omitted_field_preserves_allowlist_and_empty_array_clears_it(self):
        self._patch({"turnstile_ip_allowlist": ["203.0.113.10"]})
        response = self._patch({"email_signup_enabled": True})
        self.assertEqual(response.data["turnstile_ip_allowlist"], ["203.0.113.10"])
        response = self._patch({"turnstile_ip_allowlist": []})
        self.assertEqual(response.data["turnstile_ip_allowlist"], [])
        self.assertEqual(turnstile_ip_allowlist(), [])

    def test_restore_defaults_clears_allowlist(self):
        self._patch({"turnstile_ip_allowlist": ["203.0.113.10"]})
        response = self._patch({"clear_runtime": True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["turnstile_ip_allowlist"], [])
        self.assertFalse(response.data["has_runtime_override"])

    def test_invalid_allowlist_does_not_partially_save_other_settings(self):
        for value in (["bad-ip"], ["203.0.113.0/24"], "203.0.113.10", None):
            with self.subTest(value=value):
                response = self._patch({
                    "email_signup_enabled": True, "turnstile_ip_allowlist": value,
                })
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("turnstile_ip_allowlist", response.data)
                self.assertNotEqual(get_source(KEY_IDENTITY_EMAIL_SIGNUP), "runtime")
                self.assertEqual(turnstile_ip_allowlist(), [])

    def test_non_staff_cannot_change_allowlist(self):
        user = User.objects.create_user(username="ordinary", password="Pass1234")
        self.client.force_authenticate(user=user)
        response = self._patch({"turnstile_ip_allowlist": ["203.0.113.10"]})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(turnstile_ip_allowlist(), [])

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

    def test_clear_runtime_restores_deployment_defaults(self):
        response = self.client.patch(
            self.path,
            {"email_signup_enabled": True},
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["has_runtime_override"])
        self.assertTrue(response.data["email_signup_enabled"])

        response = self.client.patch(
            self.path,
            {"clear_runtime": True},
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["has_runtime_override"])
        self.assertFalse(response.data["email_signup_enabled"])

    @patch.dict("os.environ", {"TURNSTILE_ENABLED": ""})
    @override_settings(TURNSTILE_ENABLED=False, TURNSTILE_SITE_KEY="", TURNSTILE_SECRET_KEY="")
    def test_turnstile_enable_requires_keys_without_partially_saving_other_fields(self):
        response = self._patch({"turnstile_enabled": True, "email_signup_enabled": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "TURNSTILE_KEYS_REQUIRED")
        self.assertNotEqual(get_source(KEY_IDENTITY_EMAIL_SIGNUP), "runtime")

    @patch.dict("os.environ", {"TURNSTILE_ENABLED": ""})
    @override_settings(TURNSTILE_ENABLED=False, TURNSTILE_SITE_KEY="", TURNSTILE_SECRET_KEY="")
    def test_turnstile_runtime_enable_disable_updates_public_config_immediately(self):
        response = self._patch({
            "turnstile_enabled": True,
            "turnstile_site_key": "test-site-key",
            "turnstile_secret_key": "test-secret-key",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["turnstile_enabled"])
        self.assertEqual(response.data["turnstile_source"], "runtime")
        self.assertNotIn("test-secret-key", str(response.data))
        public = self.client.get("/api/v1/auth/turnstile/config", HTTP_X_HFL_SITE_ROLE="tenant")
        self.assertTrue(public.json()["data"]["enabled"])
        self.assertTrue(public.json()["data"]["configured"])
        response = self._patch({"turnstile_enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["turnstile_enabled"])
        self.assertTrue(response.data["turnstile_secret_configured"])
        public = self.client.get("/api/v1/auth/turnstile/config", HTTP_X_HFL_SITE_ROLE="tenant")
        self.assertFalse(public.json()["data"]["enabled"])
        self.assertNotIn("site_key", public.json()["data"])

    @patch.dict("os.environ", {"TURNSTILE_ENABLED": ""})
    @override_settings(TURNSTILE_ENABLED=True, TURNSTILE_SITE_KEY="deployment-site", TURNSTILE_SECRET_KEY="deployment-secret")
    def test_turnstile_restore_defaults_restores_deployment_enabled_state(self):
        response = self._patch({"turnstile_enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["turnstile_enabled"])
        response = self._patch({"clear_runtime": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["turnstile_enabled"])
        self.assertFalse(response.data["has_runtime_override"])

    @patch.dict("os.environ", {"TURNSTILE_ENABLED": ""})
    @override_settings(TURNSTILE_ENABLED=False, TURNSTILE_SITE_KEY="deployment-site", TURNSTILE_SECRET_KEY="deployment-secret")
    def test_turnstile_can_reuse_saved_or_deployment_secret(self):
        response = self._patch({"turnstile_enabled": True, "turnstile_secret_key": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["turnstile_enabled"])
        response = self._patch({"turnstile_site_key": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "TURNSTILE_KEYS_REQUIRED")
        response = self._patch({"turnstile_enabled": False, "turnstile_site_key": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["turnstile_enabled"])

    def test_turnstile_rejects_non_boolean_flag(self):
        for value in ("false", "true", 0, 1, None):
            with self.subTest(value=value):
                self.assertEqual(self._patch({"turnstile_enabled": value}).status_code, 400)

    def test_turnstile_runtime_flag_requires_enterprise_extension(self):
        with patch("apps.configuration.services.runtime_settings.enterprise_identity_enabled", return_value=False):
            response = self._patch({"turnstile_enabled": False})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "IDENTITY_EXTENSION_REQUIRED")

    def test_non_staff_cannot_enable_turnstile(self):
        user = User.objects.create_user(username="turnstile-ordinary", password="Pass1234")
        self.client.force_authenticate(user=user)
        self.assertEqual(self._patch({"turnstile_enabled": True}).status_code, 403)


    @patch.dict("os.environ", {"TURNSTILE_ENABLED": "", "TURNSTILE_SITE_KEY": "", "TURNSTILE_SECRET_KEY": ""})
    @override_settings(TURNSTILE_ENABLED=False, TURNSTILE_SITE_KEY="", TURNSTILE_SECRET_KEY="")
    def test_turnstile_enforcement_changes_with_saved_flag_without_restart(self):
        from django.test import RequestFactory
        from apps.iam.services.turnstile_verification import verify_turnstile_for_action

        request = RequestFactory().post("/", HTTP_X_HFL_SITE_ROLE="tenant", REMOTE_ADDR="203.0.113.20")
        self.assertTrue(verify_turnstile_for_action({}, request, action="login"))
        response = self._patch({
            "turnstile_enabled": True,
            "turnstile_site_key": "saved-site",
            "turnstile_secret_key": "saved-secret",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(verify_turnstile_for_action({}, request, action="login"))
        response = self._patch({"turnstile_enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(verify_turnstile_for_action({}, request, action="login"))
        response = self._patch({"turnstile_enabled": True, "turnstile_secret_key": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["turnstile_enabled"])
        self.assertFalse(verify_turnstile_for_action({}, request, action="login"))

    def test_turnstile_rejects_non_string_credentials(self):
        for field in ("turnstile_site_key", "turnstile_secret_key"):
            for value in (True, 123, [], {}):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self._patch({field: value}).status_code, 400)


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

    @patch.dict("os.environ", {"TURNSTILE_ENABLED": ""})
    @override_settings(TURNSTILE_ENABLED=True)
    def test_effective_turnstile_stays_off_without_extension(self):
        response = self.client.get(self.path, HTTP_X_HFL_SITE_ROLE="ops")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["effective"]["turnstile_enabled"])
        self.assertEqual(response.data["sources"]["turnstile_enabled"], "extension")
        self.assertIn("edition", response.data)
        self.assertIn("nginx", response.data["health"])
        self.assertIn("web", response.data["health"])
        self.assertIn("scheduler", response.data["health"])
        self.assertIn("data_gateway", response.data["health"])
