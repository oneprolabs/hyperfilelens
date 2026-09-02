"""Instance defaults API contract tests."""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.instance_settings.api.views.settings import PlatformOpsSettingsDefaultsView


class PlatformOpsSettingsDefaultsTests(SimpleTestCase):
    @patch(
        "apps.instance_settings.api.views.settings.get_config",
        side_effect=[{"daily": 7}, {"exclude": ["*.tmp"]}],
    )
    def test_get_omits_removed_backup_concurrency(self, mocked_get_config):
        response = PlatformOpsSettingsDefaultsView().get(request=None)

        self.assertEqual(
            response.data,
            {
                "retention_default": {"daily": 7},
                "filters_default": {"exclude": ["*.tmp"]},
            },
        )
        self.assertEqual(mocked_get_config.call_count, 2)
