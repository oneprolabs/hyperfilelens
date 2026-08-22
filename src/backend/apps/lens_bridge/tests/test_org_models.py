from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.lens_bridge.services import org_models


class ActiveLlmConfigsTests(SimpleTestCase):
    @patch("apps.lens_bridge.services.org_models.list_org_model_configs")
    def test_returns_organization_models_that_are_active(self, list_configs):
        list_configs.return_value = [
            {"uuid": "first", "is_active": True},
            {"uuid": "inactive", "is_active": False},
            {"uuid": "disabled", "status": "disabled"},
            {"uuid": "second"},
        ]

        rows = org_models.active_llm_configs(org=object())

        self.assertEqual([row["uuid"] for row in rows], ["first", "second"])

    @patch("apps.lens_bridge.services.platform_lens.get_or_create_platform_org")
    @patch("apps.lens_bridge.services.org_models.active_llm_configs")
    def test_includes_platform_models_without_duplicate_rows(
        self,
        active_configs,
        platform_org,
    ):
        tenant = SimpleNamespace(pk=20)
        platform = SimpleNamespace(pk=1)
        platform_org.return_value = platform
        active_configs.side_effect = [
            [{"uuid": "tenant-model"}, {"uuid": "shared-model"}],
            [{"uuid": "platform-model"}, {"uuid": "shared-model"}],
        ]

        rows = org_models.active_llm_configs_available_to_org(tenant)

        self.assertEqual(
            [row["uuid"] for row in rows],
            ["tenant-model", "shared-model", "platform-model"],
        )
