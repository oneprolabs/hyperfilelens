"""The Community settings socket never exposes EE storage controls."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.configuration.services import runtime_settings
from apps.instance_settings.api.views.settings import (
    PlatformOpsSettingsConnectionCompatibilityView,
    _connection_compatibility_payload,
)


class StorageCompatibilityCommunityTests(SimpleTestCase):
    def test_missing_extension_hides_and_rejects_control_without_database_access(self):
        with patch(
            "apps.instance_settings.api.views.settings.get_repository_provider", return_value=None,
        ):
            view = PlatformOpsSettingsConnectionCompatibilityView()
            self.assertEqual(_connection_compatibility_payload(), {})
            self.assertEqual(view.get(SimpleNamespace()).status_code, 403)
            response = view.patch(SimpleNamespace(
                data={"aliyun_sts_proxy_compatibility_enabled": True},
            ))
        self.assertEqual(response.status_code, 403)

    def test_default_off_has_no_environment_override(self):
        with patch.object(runtime_settings, "_row", return_value=None), patch.dict(
            "os.environ", {"HFL_ALIYUN_STS_PROXY_COMPATIBILITY_ENABLED": "true"},
        ):
            self.assertFalse(runtime_settings.aliyun_sts_proxy_compatibility_enabled())
