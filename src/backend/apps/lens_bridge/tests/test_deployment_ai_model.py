from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase

from apps.lens_bridge.models import LensOrgLink, LensOrgModelLink
from apps.lens_bridge.services import deployment_ai_model, platform_lens


class DeploymentAiModelConfigTests(TestCase):
    def test_requires_https_api_base(self):
        with self.assertRaises(deployment_ai_model.DeploymentAiModelConfigurationError):
            deployment_ai_model.DeploymentAiModelConfig.from_mapping(
                {
                    "provider": "openai_compatible",
                    "model_id": "model/one",
                    "display_name": "Model One",
                    "api_base": "http://models.example/v1",
                    "api_key": "secret",
                }
            )

    def test_normalizes_api_base_and_provider(self):
        config = deployment_ai_model.DeploymentAiModelConfig.from_mapping(
            {
                "provider": "OPENAI_COMPATIBLE",
                "model_id": "model/one",
                "display_name": "Model One",
                "api_base": "https://models.example/v1/",
                "api_key": "secret",
            }
        )
        self.assertEqual(config.provider, "openai_compatible")
        self.assertEqual(config.api_base, "https://models.example/v1")

    def test_agent_payload_does_not_clear_existing_vision_capability(self):
        config = deployment_ai_model.DeploymentAiModelConfig(
            provider="openai_compatible",
            model_id="model/one",
            display_name="Model One",
            api_base="https://models.example/v1",
            api_key="secret",
        )
        self.assertNotIn(
            "supports_vision",
            deployment_ai_model._source_lens_payload(config)["config"],
        )

    def test_multimodal_payload_declares_vision_capability(self):
        config = deployment_ai_model.DeploymentAiModelConfig(
            provider="openai_compatible",
            model_id="model/one",
            display_name="Model One",
            api_base="https://models.example/v1",
            api_key="secret",
            supports_vision=True,
        )
        self.assertTrue(
            deployment_ai_model._source_lens_payload(config)["config"][
                "supports_vision"
            ]
        )


class DeploymentAiModelServiceTests(TestCase):
    config_uuid = uuid.UUID("876742d4-c3b7-4f6c-84a8-a3c0cc8ac38e")

    def setUp(self):
        self.config = deployment_ai_model.DeploymentAiModelConfig(
            provider="openai_compatible",
            model_id="model/one",
            display_name="Model One",
            api_base="https://models.example/v1",
            api_key="deployment-secret",
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_creates_missing_model_and_sets_default(self, request_json):
        request_json.side_effect = [
            [],
            {"ok": True},
            {"uuid": str(self.config_uuid)},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertEqual(result.action, "created")
        self.assertTrue(result.connectivity_ok)
        link = LensOrgModelLink.objects.get(sl_config_uuid=self.config_uuid)
        defaults = LensOrgLink.objects.get(organization=link.organization)
        self.assertEqual(defaults.default_agent_model_ref, self.config_uuid)
        self.assertEqual(link.display_name, "Model One")
        self.assertEqual(request_json.call_args_list[0].args[:2], ("GET", "/api/v1/admin/llm-config/"))

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_updates_matching_model_including_rotated_api_key(self, request_json):
        org = platform_lens.get_or_create_platform_org()
        LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.config_uuid,
            display_name="Old name",
        )
        LensOrgLink.objects.create(
            organization=org,
            default_agent_model_ref=self.config_uuid,
        )
        request_json.side_effect = [
            [
                {
                    "uuid": str(self.config_uuid),
                    "provider": self.config.provider,
                    "config": {
                        "model": self.config.model_id,
                        "api_base": self.config.api_base,
                        "api_key": "********",
                    },
                }
            ],
            {"ok": True},
            {"uuid": str(self.config_uuid)},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertEqual(result.action, "updated")
        put_call = request_json.call_args_list[2]
        self.assertEqual(put_call.args[:2], ("PUT", f"/api/v1/admin/llm-config/{self.config_uuid}/"))
        self.assertEqual(put_call.kwargs["json_body"]["config"]["api_key"], "deployment-secret")
        self.assertEqual(
            LensOrgModelLink.objects.get(sl_config_uuid=self.config_uuid).display_name,
            "Model One",
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_reuses_soft_deleted_hfl_link_when_model_is_reconciled(
        self, request_json
    ):
        org = platform_lens.get_or_create_platform_org()
        LensOrgModelLink.all_objects.create(
            organization=org,
            sl_config_uuid=self.config_uuid,
            is_deleted=True,
        )
        request_json.side_effect = [
            [
                {
                    "uuid": str(self.config_uuid),
                    "provider": self.config.provider,
                    "config": {
                        "model": self.config.model_id,
                        "api_base": self.config.api_base,
                    },
                }
            ],
            {"ok": True},
            {"uuid": str(self.config_uuid)},
        ]

        deployment_ai_model.ensure_platform_ai_model(self.config)

        link = LensOrgModelLink.objects.get(sl_config_uuid=self.config_uuid)
        self.assertFalse(link.is_deleted)
