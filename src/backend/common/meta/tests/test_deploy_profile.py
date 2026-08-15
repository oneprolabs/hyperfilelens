from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient


@override_settings(
    HFL_EMAIL_SIGNUP_ENABLED=False,
    HFL_EMAIL_CODE_LOGIN_ENABLED=False,
    HFL_PLATFORM_OPS_ENABLED=True,
    HFL_ADMIN_PORT=11444,
    FRONTEND_URL="https://127.0.0.1:11443",
    EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    EMAIL_HOST="",
    EMAIL_HOST_USER="",
    EMAIL_HOST_PASSWORD="",
)
@patch.dict("os.environ", {"HFL_EMAIL_SIGNUP_ENABLED": "false"})
class DeployProfileViewTest(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="staff@test.com",
            email="staff@test.com",
            password="Pass1234",
            is_staff=True,
        )
        # EE AuthZ requires an explicit platform role for Console entry.
        try:
            from apps.membership.testing import ensure_platform_role

            ensure_platform_role(self.staff)
        except Exception:
            pass

    def test_anonymous_profile(self):
        response = self.client.get(
            "/api/v1/meta/deploy-profile",
            HTTP_HOST="127.0.0.1:11443",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["site_role"], "tenant")
        self.assertEqual(
            response.data["admin_console_url"],
            "https://127.0.0.1:11444",
        )
        self.assertFalse(response.data["admin_console_entry_visible"])
        self.assertFalse(response.data["platform_ops_access_allowed"])
        self.assertFalse(response.data["email_signup_enabled"])
        self.assertFalse(response.data["password_reset_available"])
        self.assertFalse(response.data["email_code_login_available"])
        from common.deploy.site import platform_ops_landing_path

        self.assertEqual(
            response.data["admin_console_landing_path"],
            platform_ops_landing_path(),
        )

    @patch.dict(
        "os.environ",
        {
            "HFL_PRODUCT_VERSION": "0.2.1",
            "APP_VERSION": "0.2.1-ee",
            "HFL_EDITION": "enterprise",
        },
    )
    def test_profile_exposes_customer_product_identity(self):
        response = self.client.get("/api/v1/meta/deploy-profile")

        self.assertEqual(response.data["product_version"], "0.2.1")
        self.assertEqual(response.data["edition"], "enterprise")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_stays_off_on_community_empty_socket(self):
        response = self.client.get("/api/v1/meta/deploy-profile")
        self.assertFalse(response.data["password_reset_available"])

    @override_settings(
        HFL_EMAIL_SIGNUP_ENABLED=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    @patch.dict("os.environ", {"HFL_EMAIL_SIGNUP_ENABLED": "true"})
    def test_email_signup_stays_off_on_community_empty_socket(self):
        response = self.client.get("/api/v1/meta/deploy-profile")
        self.assertFalse(response.data["email_signup_enabled"])
        self.assertFalse(response.data["email_code_login_available"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    def test_password_reset_is_available_with_extension_and_email(self, _identity):
        tenant = self.client.get("/api/v1/meta/deploy-profile")
        ops = self.client.get(
            "/api/v1/meta/deploy-profile",
            HTTP_X_HFL_SITE_ROLE="ops",
        )
        self.assertTrue(tenant.data["password_reset_available"])
        self.assertFalse(ops.data["password_reset_available"])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        HFL_EMAIL_CODE_LOGIN_ENABLED=True,
    )
    @patch.dict(
        "os.environ",
        {
            "HFL_EMAIL_CODE_LOGIN_ENABLED": "true",
            "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
        },
    )
    @patch(
        "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
        return_value=True,
    )
    def test_email_code_login_requires_tenant_listener_and_email_delivery(
        self,
        _identity,
    ):
        tenant = self.client.get("/api/v1/meta/deploy-profile")
        ops = self.client.get(
            "/api/v1/meta/deploy-profile",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertTrue(tenant.data["email_code_login_available"])
        self.assertFalse(ops.data["email_code_login_available"])

    def test_ops_listener_hides_tenant_registration(self):
        response = self.client.get(
            "/api/v1/meta/deploy-profile",
            HTTP_X_HFL_SITE_ROLE="ops",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["email_signup_enabled"])

    def test_staff_is_denied_platform_ops_on_tenant_listener(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get("/api/v1/meta/deploy-profile")
        self.assertTrue(response.data["admin_console_entry_visible"])
        self.assertFalse(response.data["platform_ops_access_allowed"])
        self.assertTrue(response.data["is_staff"])
        from common.deploy.site import platform_ops_landing_path

        # Tenant post-login stays "/"; Admin deep-link uses ops landing.
        self.assertEqual(response.data["landing_path"], "/")
        self.assertEqual(
            response.data["admin_console_landing_path"],
            platform_ops_landing_path(),
        )

    def test_staff_is_allowed_platform_ops_on_ops_listener(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(
            "/api/v1/meta/deploy-profile",
            HTTP_X_HFL_SITE_ROLE="ops",
        )
        self.assertEqual(response.data["site_role"], "ops")
        self.assertFalse(response.data["admin_console_entry_visible"])
        self.assertTrue(response.data["platform_ops_access_allowed"])
        from common.deploy.site import platform_ops_landing_path

        self.assertEqual(response.data["landing_path"], platform_ops_landing_path())
        self.assertEqual(
            response.data["admin_console_landing_path"],
            platform_ops_landing_path(),
        )

    @override_settings(FRONTEND_URL="https://app.example.com:11443", HFL_ADMIN_PORT=11444)
    def test_admin_console_url_uses_tenant_host_and_configured_port(self):
        response = self.client.get(
            "/api/v1/meta/deploy-profile",
            HTTP_HOST="app.example.com:11443",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(
            response.data["admin_console_url"],
            "https://app.example.com:11444",
        )

    @override_settings(
        FRONTEND_URL="https://app.example.com",
        HFL_ADMIN_PORT=11444,
        HFL_ADMIN_PUBLIC_URL="https://ops.example.com/",
    )
    def test_admin_console_url_prefers_configured_public_url(self):
        response = self.client.get(
            "/api/v1/meta/deploy-profile",
            HTTP_HOST="app.example.com",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(
            response.data["admin_console_url"],
            "https://ops.example.com",
        )

    def test_invalid_access_token_cookie_is_treated_as_anonymous(self):
        self.client.cookies["access_token"] = "not-a-valid-jwt"
        response = self.client.get("/api/v1/meta/deploy-profile")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["platform_ops_access_allowed"])
        self.assertFalse(response.data["is_staff"])
