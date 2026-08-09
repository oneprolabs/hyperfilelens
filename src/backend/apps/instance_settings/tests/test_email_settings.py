from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.configuration.services.runtime_settings import (
    CONSOLE_EMAIL_BACKEND,
    SMTP_EMAIL_BACKEND,
    invalidate_runtime_settings_cache,
)
from apps.instance_settings.tests.helpers import ensure_ops_staff_role


@override_settings(
    EMAIL_BACKEND=CONSOLE_EMAIL_BACKEND,
    EMAIL_HOST="",
    EMAIL_HOST_USER="",
    EMAIL_HOST_PASSWORD="",
)
class PlatformEmailSettingsTests(TestCase):
    path = "/api/v1/instance-settings/email"
    test_path = "/api/v1/instance-settings/email/test"

    def setUp(self):
        invalidate_runtime_settings_cache()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="smtp-admin@example.com",
            email="smtp-admin@example.com",
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

    def _get(self):
        return self.client.get(self.path, HTTP_X_HFL_SITE_ROLE="ops")

    def _patch(self, payload):
        return self.client.patch(
            self.path,
            payload,
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

    def test_default_console_backend_is_unconfigured_and_runtime_editable(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["delivery_configured"])
        self.assertFalse(response.data["managed_by_deployment"])
        self.assertEqual(response.data["source"], "default")

    def test_complete_runtime_smtp_configuration_becomes_effective(self):
        response = self._patch(
            {
                "backend": SMTP_EMAIL_BACKEND,
                "host": "smtp.example.com",
                "port": 465,
                "use_tls": False,
                "use_ssl": True,
                "host_user": "mailer@example.com",
                "password": "runtime-secret",
                "from_email": "HyperFileLens <mailer@example.com>",
            }
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["delivery_configured"])
        self.assertFalse(response.data["managed_by_deployment"])
        self.assertEqual(response.data["source"], "runtime")
        self.assertTrue(response.data["password_configured"])
        self.assertNotIn("runtime-secret", str(response.data))

    def test_partial_runtime_smtp_configuration_is_rejected(self):
        response = self._patch(
            {
                "backend": SMTP_EMAIL_BACKEND,
                "host": "smtp.example.com",
                "port": 465,
                "use_tls": False,
                "use_ssl": True,
                "host_user": "",
                "password": "runtime-secret",
                "from_email": "HyperFileLens <mailer@example.com>",
            }
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "EMAIL_CONFIGURATION_INVALID")

    @override_settings(
        EMAIL_BACKEND=SMTP_EMAIL_BACKEND,
        EMAIL_HOST="smtp.example.com",
        EMAIL_PORT=465,
        EMAIL_USE_TLS=False,
        EMAIL_USE_SSL=True,
        EMAIL_HOST_USER="mailer@example.com",
        EMAIL_HOST_PASSWORD="deployment-secret",
        DEFAULT_FROM_EMAIL="HyperFileLens <mailer@example.com>",
    )
    def test_deployment_managed_smtp_is_read_only(self):
        response = self._get()
        self.assertTrue(response.data["delivery_configured"])
        self.assertTrue(response.data["managed_by_deployment"])
        self.assertEqual(response.data["source"], "deployment")

        response = self._patch({"host": "other.example.com"})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["code"],
            "EMAIL_SETTINGS_MANAGED_BY_DEPLOYMENT",
        )

    @override_settings(
        EMAIL_BACKEND=SMTP_EMAIL_BACKEND,
        EMAIL_HOST="smtp.example.com",
        EMAIL_PORT=465,
        EMAIL_USE_TLS=False,
        EMAIL_USE_SSL=True,
        EMAIL_HOST_USER="mailer@example.com",
        EMAIL_HOST_PASSWORD="deployment-secret",
        DEFAULT_FROM_EMAIL="HyperFileLens <mailer@example.com>",
    )
    @patch(
        "apps.instance_settings.api.views.settings.EmailMessage.send",
        side_effect=RuntimeError("sensitive SMTP response"),
    )
    def test_email_test_masks_provider_errors(self, _send):
        response = self.client.post(
            self.test_path,
            {"recipient": "recipient@example.com"},
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "EMAIL_SERVICE_UNAVAILABLE")
        self.assertNotIn("sensitive SMTP response", str(response.data))
