"""Product Website is an independent, instance-global navigation setting."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient

from apps.configuration.models import GlobalConfig
from apps.configuration.services.internal.registry import registry_by_key
from apps.instance_settings.conf import (
    CONFIG_KEY_EXTERNAL_ACCESS_URL,
    CONFIG_KEY_PRODUCT_WEBSITE_URL,
)
from apps.instance_settings.services.external_access import (
    configured_external_access_url,
    set_external_access_url,
)
from apps.instance_settings.services.product_website import (
    configured_product_website_url,
    default_product_website_url,
    effective_product_website_url,
    normalize_product_website_url,
    set_product_website_url,
)
from apps.instance_settings.tests.helpers import ensure_ops_staff_role


@override_settings(
    ALLOWED_HOSTS=["*"],
    FRONTEND_URL="https://agent-entry.example:11443",
    HFL_WEBSITE_PORT=11442,
    HFL_INSECURE_TLS=True,
)
class ProductWebsiteSettingsTests(TestCase):
    path = "/api/v1/instance-settings/external-access"

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="website-admin", password="Test-only-123", is_staff=True,
        )
        ensure_ops_staff_role(self.admin)
        self.client.force_authenticate(user=self.admin)
        self.request = RequestFactory().get("/", HTTP_HOST="192.168.8.69:11444")
        self.headers = {
            "HTTP_HOST": "192.168.8.69:11444",
            "HTTP_X_HFL_SITE_ROLE": "ops",
            "HTTP_X_FORWARDED_PROTO": "https",
        }

    def save(self, body):
        return self.client.patch(self.path, body, format="json", **self.headers)

    def test_default_uses_public_host_and_website_port_not_agent_origin(self):
        response = self.client.get(self.path, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["product_website_url"], "")
        self.assertEqual(
            response.data["default_product_website_url"],
            "https://192.168.8.69:11442",
        )
        self.assertEqual(
            effective_product_website_url(self.request),
            "https://192.168.8.69:11442",
        )

    @override_settings(HFL_WEBSITE_PORT=24442)
    def test_default_respects_custom_published_port_and_ipv6(self):
        request = RequestFactory().get("/", HTTP_HOST="[2001:db8::1]:11444")
        self.assertEqual(default_product_website_url(request), "https://[2001:db8::1]:24442")

    @override_settings(HFL_WEBSITE_PORT=443)
    def test_default_port_443_is_canonical(self):
        self.assertEqual(default_product_website_url(self.request), "https://192.168.8.69")

    @override_settings(HFL_WEBSITE_PORT=-1)
    def test_invalid_port_falls_back_to_11442(self):
        self.assertEqual(default_product_website_url(self.request), "https://192.168.8.69:11442")

    def test_saas_override_does_not_change_external_access(self):
        set_external_access_url("https://agents.example:14443", user=self.admin)
        response = self.save({"product_website_url": "https://HyperFileLens.com:443/"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["product_website_url"], "https://hyperfilelens.com/")
        self.assertEqual(configured_external_access_url(), "https://agents.example:14443")
        self.assertEqual(effective_product_website_url(self.request), "https://hyperfilelens.com/")

    def test_external_access_only_patch_preserves_website_override(self):
        set_product_website_url("https://hyperfilelens.com/", user=self.admin)
        response = self.save({"external_access_url": "https://new-agents.example"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(configured_product_website_url(), "https://hyperfilelens.com/")

    def test_clearing_website_removes_override_without_explicit_empty(self):
        for value in ("", "  ", None):
            with self.subTest(value=value):
                set_product_website_url("https://hyperfilelens.com/", user=self.admin)
                response = self.save({"product_website_url": value})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(GlobalConfig.objects.filter(key=CONFIG_KEY_PRODUCT_WEBSITE_URL).exists())
                self.assertEqual(effective_product_website_url(self.request), "https://192.168.8.69:11442")

    def test_both_settings_can_be_saved_together(self):
        response = self.save({
            "external_access_url": "https://agents.example",
            "product_website_url": "https://hyperfilelens.com/",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(configured_external_access_url(), "https://agents.example")
        self.assertEqual(configured_product_website_url(), "https://hyperfilelens.com/")

    def test_invalid_website_prevents_any_external_access_write(self):
        set_external_access_url("https://original.example", user=self.admin)
        response = self.save({
            "external_access_url": "https://changed.example",
            "product_website_url": "javascript:alert(1)",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "PRODUCT_WEBSITE_URL_INVALID")
        self.assertEqual(configured_external_access_url(), "https://original.example")
        self.assertEqual(configured_product_website_url(), "")

    @override_settings(HFL_INSECURE_TLS=False)
    def test_external_tls_policy_failure_does_not_change_either_setting(self):
        set_external_access_url("https://original.example", user=self.admin)
        set_product_website_url("https://original-site.example/", user=self.admin)
        response = self.save({
            "external_access_url": "http://insecure-agents.example",
            "product_website_url": "https://changed-site.example/",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(configured_external_access_url(), "https://original.example")
        self.assertEqual(configured_product_website_url(), "https://original-site.example/")

    def test_browser_link_validation_is_independent_of_agent_tls_policy(self):
        with self.settings(HFL_INSECURE_TLS=False):
            response = self.save({"product_website_url": "http://localhost:11442/info"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(configured_product_website_url(), "http://localhost:11442/info")

    def test_rejects_unsafe_schemes_credentials_and_ambiguous_hosts(self):
        for value in (
            "javascript:alert(1)", "data:text/html,test", "//evil.example",
            "https://user:secret@example.com/", "https://@example.com/",
            "https://example.com:0/", "https://example.com:65536/",
            "https://example.com\\other", "https://example.com%2fevil",
            "https://example.com/\nother", 12, False, {"url": "https://example.com"},
        ):
            with self.subTest(value=value):
                response = self.save({"product_website_url": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], "PRODUCT_WEBSITE_URL_INVALID")
        self.assertFalse(GlobalConfig.objects.filter(key=CONFIG_KEY_PRODUCT_WEBSITE_URL).exists())

    def test_supports_legitimate_custom_paths_without_contacting_the_site(self):
        with patch("socket.create_connection", side_effect=AssertionError("Must not fetch URL")):
            response = self.save({"product_website_url": "https://portal.example/product/?lang=en#about"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(configured_product_website_url(), "https://portal.example/product/?lang=en#about")

    def test_custom_root_and_path_trailing_slashes_are_preserved(self):
        for url in ("https://portal.example/", "https://portal.example/docs/"):
            with self.subTest(url=url):
                response = self.save({"product_website_url": url})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["product_website_url"], url)
                self.assertEqual(effective_product_website_url(self.request), url)

    def test_corrupt_runtime_values_fall_back_to_safe_default(self):
        GlobalConfig.objects.create(
            key=CONFIG_KEY_PRODUCT_WEBSITE_URL, scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="", value="javascript:alert(1)", value_type="string",
        )
        self.assertEqual(effective_product_website_url(self.request), "https://192.168.8.69:11442")

    def test_normalizes_ipv6_and_custom_port(self):
        self.assertEqual(normalize_product_website_url("https://[2001:DB8::1]:24442/"), "https://[2001:db8::1]:24442/")

    def test_setting_is_global_not_tenant_scoped(self):
        self.assertFalse(registry_by_key()[CONFIG_KEY_PRODUCT_WEBSITE_URL].tenant_capable)
        GlobalConfig.objects.create(
            key=CONFIG_KEY_PRODUCT_WEBSITE_URL, scope=GlobalConfig.Scope.TENANT,
            tenant_key="some-org", value="https://tenant.example", value_type="string",
        )
        self.assertEqual(configured_product_website_url(), "")

    def test_regular_user_cannot_modify_instance_website(self):
        user = User.objects.create_user(username="website-user", password="Test-only-123")
        self.client.force_authenticate(user=user)
        response = self.save({"product_website_url": "https://changed.example/"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(GlobalConfig.objects.filter(key=CONFIG_KEY_PRODUCT_WEBSITE_URL).exists())

    def test_anonymous_user_cannot_modify_instance_website(self):
        self.client.force_authenticate(user=None)
        response = self.save({"product_website_url": "https://changed.example/"})
        self.assertIn(response.status_code, (401, 403))

    def test_deploy_profile_exposes_effective_link_without_a_role_requirement(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/v1/meta/deploy-profile", HTTP_HOST="192.168.8.69:11443")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["website_public_url"], "https://192.168.8.69:11442")
        set_product_website_url("https://hyperfilelens.com/", user=self.admin)
        response = self.client.get("/api/v1/meta/deploy-profile", HTTP_HOST="app.hyperfilelens.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["website_public_url"], "https://hyperfilelens.com/")

    def test_empty_request_keeps_existing_api_error_contract(self):
        response = self.save({})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "EXTERNAL_ACCESS_URL_REQUIRED")
        self.assertFalse(GlobalConfig.objects.filter(key=CONFIG_KEY_EXTERNAL_ACCESS_URL).exists())
