"""Community external-access settings tests."""

import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.configuration.models import GlobalConfig
from apps.configuration.selectors.interface import invalidate_config_cache
from apps.instance_settings.conf import CONFIG_KEY_EXTERNAL_ACCESS_URL
from apps.instance_settings.services.external_access import (
    configured_external_access_url,
    effective_external_access_url,
    normalize_external_access_url,
    set_external_access_url,
)
from apps.instance_settings.tests.helpers import ensure_ops_staff_role


@override_settings(
    ALLOWED_HOSTS=["*"],
    FRONTEND_URL="https://192.168.0.89:11443",
    HFL_INSECURE_TLS=True,
    HFL_TENANT_PORT=11443,
)
@patch.dict(os.environ, {"HFL_EDITION": "community"})
class ExternalAccessSettingsTests(TestCase):
    path = "/api/v1/instance-settings/external-access"

    def setUp(self):
        self._invalidate_external_access_cache()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="external-access-admin@example.com",
            password="Pass1234",
            is_staff=True,
        )
        ensure_ops_staff_role(self.staff)
        self.client.force_authenticate(user=self.staff)

    def tearDown(self):
        self._invalidate_external_access_cache()
        super().tearDown()

    @staticmethod
    def _invalidate_external_access_cache():
        invalidate_config_cache(
            key=CONFIG_KEY_EXTERNAL_ACCESS_URL,
            tenant_key="",
            scope="global",
        )

    def _get(self):
        return self.client.get(
            self.path,
            HTTP_HOST="113.44.213.250:11444",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

    def _patch(self, value):
        return self.client.patch(
            self.path,
            {"external_access_url": value},
            format="json",
            HTTP_HOST="113.44.213.250:11444",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

    def _store_ignored_runtime_override(self):
        GlobalConfig.objects.create(
            key=CONFIG_KEY_EXTERNAL_ACCESS_URL,
            scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="",
            value="https://ignored.example.com",
            value_type=GlobalConfig.ValueType.STRING,
            category="deployment",
            is_active=True,
        )
        self._invalidate_external_access_cache()

    def test_get_reports_installation_default_and_public_suggestion(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["external_access_url"], "")
        self.assertEqual(response.data["effective_url"], "https://192.168.0.89:11443")
        self.assertEqual(response.data["source"], "deployment")
        self.assertEqual(response.data["suggested_url"], "https://113.44.213.250:11443")
        self.assertTrue(response.data["editable"])

    def test_patch_sets_normalized_runtime_override(self):
        response = self._patch("https://HFL.Example.com:443/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["external_access_url"], "https://hfl.example.com")
        self.assertEqual(response.data["effective_url"], "https://hfl.example.com")
        self.assertEqual(response.data["source"], "runtime")
        self.assertEqual(effective_external_access_url(), "https://hfl.example.com")

    def test_empty_patch_restores_installation_default(self):
        self._patch("https://hfl.example.com")

        response = self._patch("")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["source"], "deployment")
        self.assertEqual(response.data["effective_url"], "https://192.168.0.89:11443")
        self.assertFalse(
            GlobalConfig.objects.filter(
                key=CONFIG_KEY_EXTERNAL_ACCESS_URL,
                scope=GlobalConfig.Scope.GLOBAL,
                tenant_key="",
            ).exists()
        )

    def test_patch_requires_explicit_url_field(self):
        response = self.client.patch(
            self.path,
            {},
            format="json",
            HTTP_HOST="113.44.213.250:11444",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "EXTERNAL_ACCESS_URL_REQUIRED")

    def test_patch_rejects_non_object_request_body(self):
        response = self.client.patch(
            self.path,
            ["https://hfl.example.com"],
            format="json",
            HTTP_HOST="113.44.213.250:11444",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "EXTERNAL_ACCESS_REQUEST_INVALID")

    def test_patch_rejects_non_origin_url(self):
        response = self._patch("https://hfl.example.com/a/path")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "EXTERNAL_ACCESS_URL_INVALID")
        self.assertEqual(configured_external_access_url(), "")

    def test_patch_rejects_ambiguous_host_syntax(self):
        for value in (
            "https://hfl.example.com\\unexpected",
            "https://hfl.example.com%2Funexpected",
        ):
            with self.subTest(value=value):
                response = self._patch(value)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    response.data["code"],
                    "EXTERNAL_ACCESS_URL_INVALID",
                )
                self.assertEqual(configured_external_access_url(), "")

    def test_patch_rejects_non_string_and_zero_port(self):
        for value in (None, 42, "https://hfl.example.com:0"):
            with self.subTest(value=value):
                response = self._patch(value)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    response.data["code"],
                    "EXTERNAL_ACCESS_URL_INVALID",
                )
                self.assertEqual(configured_external_access_url(), "")

    @override_settings(HFL_INSECURE_TLS=False)
    def test_patch_rejects_http_when_tls_verification_is_enabled(self):
        response = self._patch("http://hfl.example.com")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "EXTERNAL_ACCESS_URL_INVALID")
        self.assertEqual(configured_external_access_url(), "")

    def test_http_override_is_ignored_after_tls_verification_is_enabled(self):
        response = self._patch("http://hfl.example.com")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        with self.settings(HFL_INSECURE_TLS=False):
            self.assertEqual(configured_external_access_url(), "")
            self.assertEqual(
                effective_external_access_url(),
                "https://192.168.0.89:11443",
            )

    def test_cli_uses_the_same_configuration_service(self):
        call_command(
            "configure_external_access",
            url="https://hfl.example.com:443/",
        )
        self.assertEqual(configured_external_access_url(), "https://hfl.example.com")

        call_command("configure_external_access", clear=True)
        self.assertEqual(configured_external_access_url(), "")

    @patch(
        "apps.instance_settings.services.external_access.product_edition",
        return_value="enterprise",
    )
    def test_enterprise_effective_url_ignores_runtime_override(self, _edition):
        self._store_ignored_runtime_override()

        self.assertEqual(
            effective_external_access_url(),
            "https://192.168.0.89:11443",
        )

    @patch(
        "apps.instance_settings.services.external_access.product_edition",
        return_value="enterprise",
    )
    def test_enterprise_service_rejects_runtime_updates(self, _edition):
        with self.assertRaisesMessage(ValueError, "managed by Enterprise"):
            set_external_access_url("https://other.example.com", user=self.staff)

    def test_normalizer_supports_ipv6_origins(self):
        self.assertEqual(
            normalize_external_access_url("https://[2001:DB8::1]:11443/"),
            "https://[2001:db8::1]:11443",
        )

    @patch(
        "apps.instance_settings.api.views.settings.product_edition",
        return_value="enterprise",
    )
    def test_enterprise_patch_remains_deployment_managed(self, _edition):
        response = self._patch("https://other.example.com")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["code"],
            "EXTERNAL_ACCESS_MANAGED_BY_DEPLOYMENT",
        )

    @patch(
        "apps.instance_settings.services.external_access.product_edition",
        return_value="enterprise",
    )
    @patch(
        "apps.instance_settings.api.views.settings.product_edition",
        return_value="enterprise",
    )
    def test_enterprise_get_hides_ignored_runtime_override(
        self,
        _view_edition,
        _service_edition,
    ):
        self._store_ignored_runtime_override()

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["external_access_url"], "")
        self.assertEqual(
            response.data["effective_url"],
            "https://192.168.0.89:11443",
        )
        self.assertEqual(response.data["source"], "deployment")
        self.assertEqual(response.data["suggested_url"], "")
        self.assertFalse(response.data["editable"])
