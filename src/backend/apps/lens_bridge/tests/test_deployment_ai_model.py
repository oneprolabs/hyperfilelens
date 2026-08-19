from __future__ import annotations

import uuid
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase

from apps.lens_bridge.models import LensOrgLink, LensOrgModelLink
from apps.lens_bridge.services import deployment_ai_model, platform_lens, sl_client


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

    def test_normalizes_api_base_without_changing_path(self):
        config = deployment_ai_model.DeploymentAiModelConfig.from_mapping(
            {
                "provider": "OPENAI_COMPATIBLE",
                "model_id": "deepseek/DeepSeek-V4-Flash/8f94e",
                "display_name": "DeepSeek V4 Flash",
                "api_base": "https://models.example/custom/api/",
                "api_key": "secret",
            }
        )

        self.assertEqual(config.provider, "openai_compatible")
        self.assertEqual(config.api_base, "https://models.example/custom/api")

    def test_supports_vision_absent_defaults_to_false(self):
        config = deployment_ai_model.DeploymentAiModelConfig.from_mapping(
            {
                "provider": "openai_compatible",
                "model_id": "model/one",
                "display_name": "Model One",
                "api_base": "https://models.example/v1",
                "api_key": "secret",
            }
        )

        self.assertFalse(config.supports_vision)

    def test_coerces_supports_vision_boolean_and_string_forms(self):
        base = {
            "provider": "openai_compatible",
            "model_id": "model/one",
            "display_name": "Model One",
            "api_base": "https://models.example/v1",
            "api_key": "secret",
        }
        expected = {
            True: True,
            False: False,
            "true": True,
            "1": True,
            "yes": True,
            "on": True,
            "false": False,
            "0": False,
            None: False,
        }
        for raw, want in expected.items():
            values = dict(base, supports_vision=raw)
            with self.subTest(raw=raw):
                config = deployment_ai_model.DeploymentAiModelConfig.from_mapping(
                    values
                )
                self.assertEqual(config.supports_vision, want)

    def test_payload_includes_vision_declaration_without_is_default(self):
        config = deployment_ai_model.DeploymentAiModelConfig(
            provider="openai_compatible",
            model_id="deepseek/DeepSeek-V4-Flash/8f94e",
            display_name="DeepSeek V4 Flash",
            api_base="https://models.example/custom/api",
            api_key="deployment-secret",
            supports_vision=True,
        )

        payload = deployment_ai_model._source_lens_payload(config)

        self.assertTrue(payload["config"]["supports_vision"])
        self.assertNotIn("is_default", payload)
        payload = deployment_ai_model._source_lens_payload(
            config, make_default=False
        )
        self.assertFalse(payload["is_default"])

    def test_vision_declaration_changes_deployment_fingerprint(self):
        base = deployment_ai_model.DeploymentAiModelConfig(
            provider="openai_compatible",
            model_id="deepseek/DeepSeek-V4-Flash/8f94e",
            display_name="DeepSeek V4 Flash",
            api_base="https://models.example/custom/api",
            api_key="deployment-secret",
        )
        vision = deployment_ai_model.DeploymentAiModelConfig(
            provider=base.provider,
            model_id=base.model_id,
            display_name=base.display_name,
            api_base=base.api_base,
            api_key=base.api_key,
            supports_vision=True,
        )

        self.assertNotEqual(
            deployment_ai_model._deployment_fingerprint(base),
            deployment_ai_model._deployment_fingerprint(vision),
        )


class DeploymentAiModelServiceTests(TestCase):
    model_uuid = uuid.UUID("876742d4-c3b7-4f6c-84a8-a3c0cc8ac38e")
    replacement_uuid = uuid.UUID("59cd45f4-ddb8-4646-839b-555b1f9f289d")

    def setUp(self):
        self.config = deployment_ai_model.DeploymentAiModelConfig(
            provider="openai_compatible",
            model_id="deepseek/DeepSeek-V4-Flash/8f94e",
            display_name="DeepSeek V4 Flash",
            api_base="https://models.example/custom/api",
            api_key="deployment-secret",
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_first_adoption_creates_and_sets_default(self, request_json):
        request_json.side_effect = [
            {"uuid": str(self.model_uuid)},
            {"ok": True},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertEqual(result.action, "created")
        self.assertTrue(result.connectivity_ok)
        create_payload = request_json.call_args_list[0].kwargs["json_body"]
        self.assertFalse(create_payload["is_default"])
        self.assertEqual(create_payload["config"]["api_key"], "deployment-secret")
        request_json.assert_any_call(
            "POST",
            "/api/v1/admin/llm-config/test-call/",
            json_body={
                "config_uuid": str(self.model_uuid),
                "prompt": "Respond with exactly OK and no explanation.",
                "max_tokens": 512,
            },
            timeout=90,
        )
        link = LensOrgModelLink.objects.get(
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY
        )
        self.assertEqual(link.sl_config_uuid, self.model_uuid)
        self.assertEqual(link.display_name, "DeepSeek V4 Flash")
        self.assertEqual(
            link.deployment_role,
            LensOrgModelLink.DeploymentRole.AGENT,
        )
        self.assertFalse(link.is_deployment_history)
        self.assertEqual(
            LensOrgLink.objects.get(organization=link.organization).default_agent_model_ref,
            self.model_uuid,
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_multimodal_adoption_uses_separate_default_role(self, request_json):
        request_json.side_effect = [
            {"uuid": str(self.model_uuid)},
            {"ok": True},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(
            self.config,
            role="multimodal",
        )

        self.assertEqual(result.action, "created")
        create_payload = request_json.call_args_list[0].kwargs["json_body"]
        self.assertFalse(create_payload["is_default"])
        link = LensOrgModelLink.objects.get(
            management_key=deployment_ai_model.DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY
        )
        self.assertEqual(
            link.deployment_role,
            LensOrgModelLink.DeploymentRole.MULTIMODAL,
        )
        defaults = LensOrgLink.objects.get(organization=link.organization)
        self.assertIsNone(defaults.default_agent_model_ref)
        self.assertEqual(
            defaults.default_multimodal_model_ref,
            self.model_uuid,
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_multimodal_failure_preserves_installed_default(self, request_json):
        org = platform_lens.get_or_create_platform_org()
        link = LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=(
                deployment_ai_model.DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY
            ),
            deployment_role=LensOrgModelLink.DeploymentRole.MULTIMODAL,
            deployment_fingerprint="previous-fingerprint",
        )
        defaults = LensOrgLink.objects.create(
            organization=org,
            default_multimodal_model_ref=self.model_uuid,
        )
        request_json.side_effect = [
            {
                "uuid": str(self.model_uuid),
                "provider": self.config.provider,
                "config": {
                    "model": self.config.model_id,
                    "api_base": self.config.api_base,
                },
            },
            {"uuid": str(self.replacement_uuid)},
            {"ok": False},
            None,
        ]

        result = deployment_ai_model.ensure_platform_ai_model(
            self.config,
            role="multimodal",
        )

        self.assertFalse(result.connectivity_ok)
        self.assertFalse(result.applied)
        link.refresh_from_db()
        defaults.refresh_from_db()
        self.assertEqual(link.sl_config_uuid, self.model_uuid)
        self.assertEqual(defaults.default_multimodal_model_ref, self.model_uuid)
        request_json.assert_any_call(
            "DELETE",
            f"/api/v1/admin/llm-config/{self.replacement_uuid}/",
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_valid_unchanged_multimodal_repairs_missing_default(self, request_json):
        org = platform_lens.get_or_create_platform_org()
        LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=(
                deployment_ai_model.DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY
            ),
            deployment_role=LensOrgModelLink.DeploymentRole.MULTIMODAL,
            deployment_fingerprint=(
                deployment_ai_model._deployment_fingerprint(self.config)
            ),
        )
        defaults = LensOrgLink.objects.create(organization=org)
        request_json.side_effect = [
            {
                "uuid": str(self.model_uuid),
                "provider": self.config.provider,
                "config": {
                    "model": self.config.model_id,
                    "api_base": self.config.api_base,
                },
            },
            {"ok": True},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(
            self.config,
            role="multimodal",
        )

        self.assertTrue(result.connectivity_ok)
        defaults.refresh_from_db()
        self.assertEqual(defaults.default_multimodal_model_ref, self.model_uuid)

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_recheck_patches_vision_capability_without_touching_default(
        self,
        request_json,
    ):
        org = platform_lens.get_or_create_platform_org()
        vision_config = deployment_ai_model.DeploymentAiModelConfig(
            provider=self.config.provider,
            model_id=self.config.model_id,
            display_name=self.config.display_name,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
            supports_vision=True,
        )
        LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=(
                deployment_ai_model.DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY
            ),
            deployment_role=LensOrgModelLink.DeploymentRole.MULTIMODAL,
            deployment_fingerprint=(
                deployment_ai_model._deployment_fingerprint(vision_config)
            ),
        )
        defaults = LensOrgLink.objects.create(
            organization=org,
            default_multimodal_model_ref=self.model_uuid,
        )
        request_json.side_effect = [
            {
                "uuid": str(self.model_uuid),
                "provider": vision_config.provider,
                "is_active": True,
                "config": {
                    "model": vision_config.model_id,
                    "api_base": vision_config.api_base,
                },
            },
            {"ok": True},
            {},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(
            vision_config,
            role="multimodal",
        )

        self.assertEqual(result.action, "updated")
        self.assertTrue(result.connectivity_ok)
        put_call = request_json.call_args_list[2]
        self.assertEqual(
            put_call.args,
            ("PUT", f"/api/v1/admin/llm-config/{self.model_uuid}/"),
        )
        patch_payload = put_call.kwargs["json_body"]
        self.assertTrue(patch_payload["config"]["supports_vision"])
        # The patch must never touch SourceLens's process-wide default.
        self.assertNotIn("is_default", patch_payload)
        defaults.refresh_from_db()
        self.assertEqual(
            defaults.default_multimodal_model_ref,
            self.model_uuid,
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_recheck_skips_patch_when_vision_already_declared(self, request_json):
        org = platform_lens.get_or_create_platform_org()
        vision_config = deployment_ai_model.DeploymentAiModelConfig(
            provider=self.config.provider,
            model_id=self.config.model_id,
            display_name=self.config.display_name,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
            supports_vision=True,
        )
        LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=(
                deployment_ai_model.DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY
            ),
            deployment_role=LensOrgModelLink.DeploymentRole.MULTIMODAL,
            deployment_fingerprint=(
                deployment_ai_model._deployment_fingerprint(vision_config)
            ),
        )
        defaults = LensOrgLink.objects.create(organization=org)
        request_json.side_effect = [
            {
                "uuid": str(self.model_uuid),
                "provider": vision_config.provider,
                "is_active": True,
                "config": {
                    "model": vision_config.model_id,
                    "api_base": vision_config.api_base,
                    "supports_vision": True,
                },
            },
            {"ok": True},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(
            vision_config,
            role="multimodal",
        )

        self.assertTrue(result.connectivity_ok)
        self.assertEqual(request_json.call_count, 2)
        for call in request_json.call_args_list:
            self.assertNotEqual(call.args[0], "PUT")
        defaults.refresh_from_db()
        self.assertEqual(defaults.default_multimodal_model_ref, self.model_uuid)

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_failed_recheck_rebuilds_from_deployment_configuration(
        self,
        request_json,
    ):
        org = platform_lens.get_or_create_platform_org()
        link = LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY,
            deployment_role=LensOrgModelLink.DeploymentRole.AGENT,
            deployment_fingerprint=(
                deployment_ai_model._deployment_fingerprint(self.config)
            ),
        )
        defaults = LensOrgLink.objects.create(
            organization=org,
            default_agent_model_ref=self.model_uuid,
        )
        request_json.side_effect = [
            {"uuid": str(self.model_uuid), "is_active": True},
            {"ok": False},
            {"uuid": str(self.replacement_uuid)},
            {"ok": True},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertTrue(result.applied)
        self.assertEqual(result.action, "recreated")
        link.refresh_from_db()
        defaults.refresh_from_db()
        self.assertTrue(link.is_deployment_history)
        self.assertEqual(
            defaults.default_agent_model_ref,
            self.replacement_uuid,
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_update_preserves_an_administrator_default_selection(self, request_json):
        org = platform_lens.get_or_create_platform_org()
        LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY,
        )
        other_uuid = uuid.UUID("559d6d6e-78a6-4bbf-869c-e9005033d342")
        LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=other_uuid,
        )
        LensOrgLink.objects.create(
            organization=org,
            default_agent_model_ref=other_uuid,
        )
        request_json.side_effect = [
            {
                "uuid": str(self.model_uuid),
                "provider": self.config.provider,
                "config": {
                    "model": self.config.model_id,
                    "api_base": self.config.api_base,
                },
                "is_default": False,
            },
            {"uuid": str(other_uuid), "is_active": True, "is_default": True},
            {"uuid": str(self.replacement_uuid), "is_default": False},
            {"success": True},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertEqual(result.action, "recreated")
        create_payload = request_json.call_args_list[2].kwargs["json_body"]
        self.assertFalse(create_payload["is_default"])
        defaults = LensOrgLink.objects.get(organization=org)
        self.assertEqual(defaults.default_agent_model_ref, other_uuid)

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_model_identity_change_versions_config_for_existing_chats(
        self,
        request_json,
    ):
        org = platform_lens.get_or_create_platform_org()
        link = LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY,
        )
        defaults = LensOrgLink.objects.create(
            organization=org,
            default_agent_model_ref=self.model_uuid,
        )
        request_json.side_effect = [
            {
                "uuid": str(self.model_uuid),
                "provider": self.config.provider,
                "config": {
                    "model": "previous/model",
                    "api_base": self.config.api_base,
                },
            },
            {"uuid": str(self.replacement_uuid)},
            {"ok": True},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertEqual(result.action, "recreated")
        self.assertEqual(
            request_json.call_args_list[1].args,
            ("POST", "/api/v1/admin/llm-config/"),
        )
        defaults.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(link.sl_config_uuid, self.model_uuid)
        self.assertTrue(link.is_deployment_history)
        self.assertEqual(
            link.deployment_role,
            LensOrgModelLink.DeploymentRole.AGENT,
        )
        self.assertTrue(link.management_key.startswith("deploy-agent-history-"))
        replacement_link = LensOrgModelLink.objects.get(
            management_key=(
                deployment_ai_model.DEPLOYMENT_AGENT_MODEL_MANAGEMENT_KEY
            )
        )
        self.assertEqual(replacement_link.sl_config_uuid, self.replacement_uuid)
        self.assertFalse(replacement_link.is_deployment_history)
        self.assertEqual(
            defaults.default_agent_model_ref,
            self.replacement_uuid,
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_agent_candidate_failure_preserves_working_model_and_default(
        self,
        request_json,
    ):
        org = platform_lens.get_or_create_platform_org()
        link = LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY,
            deployment_role=LensOrgModelLink.DeploymentRole.AGENT,
            deployment_fingerprint="previous-fingerprint",
        )
        defaults = LensOrgLink.objects.create(
            organization=org,
            default_agent_model_ref=self.model_uuid,
        )
        request_json.side_effect = [
            {"uuid": str(self.model_uuid), "is_active": True},
            {"uuid": str(self.replacement_uuid)},
            {"ok": False},
            None,
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertFalse(result.applied)
        link.refresh_from_db()
        defaults.refresh_from_db()
        self.assertEqual(link.sl_config_uuid, self.model_uuid)
        self.assertFalse(link.is_deployment_history)
        self.assertEqual(defaults.default_agent_model_ref, self.model_uuid)

    @patch(
        "apps.lens_bridge.services.deployment_ai_model._set_role_default",
        side_effect=DatabaseError("default pointer unavailable"),
    )
    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_link_and_default_roll_back_together_when_promotion_fails(
        self,
        request_json,
        _set_default,
    ):
        org = platform_lens.get_or_create_platform_org()
        link = LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY,
            deployment_role=LensOrgModelLink.DeploymentRole.AGENT,
            deployment_fingerprint="previous-fingerprint",
        )
        LensOrgLink.objects.create(
            organization=org,
            default_agent_model_ref=self.model_uuid,
        )
        request_json.side_effect = [
            {"uuid": str(self.model_uuid), "is_active": True},
            {"uuid": str(self.replacement_uuid)},
            {"ok": True},
            None,
        ]

        with self.assertRaises(DatabaseError):
            deployment_ai_model.ensure_platform_ai_model(self.config)

        link.refresh_from_db()
        self.assertEqual(link.sl_config_uuid, self.model_uuid)
        self.assertFalse(link.is_deployment_history)
        self.assertFalse(
            LensOrgModelLink.objects.filter(
                sl_config_uuid=self.replacement_uuid
            ).exists()
        )
        request_json.assert_any_call(
            "DELETE",
            f"/api/v1/admin/llm-config/{self.replacement_uuid}/",
        )

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_missing_managed_source_lens_record_is_recreated_without_stealing_default(
        self,
        request_json,
    ):
        org = platform_lens.get_or_create_platform_org()
        link = LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY,
        )
        other_uuid = uuid.UUID("3fdd8398-aa17-4817-96b9-6d8698c99dd4")
        LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=other_uuid,
        )
        LensOrgLink.objects.create(
            organization=org,
            default_agent_model_ref=other_uuid,
        )
        not_found = sl_client.LensBridgeError("not found")
        not_found.status_code = 404
        request_json.side_effect = [
            not_found,
            {"uuid": str(other_uuid), "is_active": True, "is_default": True},
            {"uuid": str(self.replacement_uuid)},
            {"ok": False},
            None,
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertEqual(result.action, "recreated")
        self.assertFalse(result.connectivity_ok)
        self.assertFalse(result.applied)
        create_payload = request_json.call_args_list[2].kwargs["json_body"]
        self.assertFalse(create_payload["is_default"])
        link.refresh_from_db()
        self.assertEqual(link.sl_config_uuid, self.model_uuid)

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_missing_managed_default_is_recreated_and_selected(self, request_json):
        org = platform_lens.get_or_create_platform_org()
        link = LensOrgModelLink.objects.create(
            organization=org,
            sl_config_uuid=self.model_uuid,
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY,
        )
        defaults = LensOrgLink.objects.create(
            organization=org,
            default_agent_model_ref=self.model_uuid,
        )
        not_found = sl_client.LensBridgeError("not found")
        not_found.status_code = 404
        request_json.side_effect = [
            not_found,
            {"uuid": str(self.replacement_uuid)},
            {"ok": True},
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertEqual(result.action, "recreated")
        create_payload = request_json.call_args_list[1].kwargs["json_body"]
        self.assertFalse(create_payload["is_default"])
        defaults.refresh_from_db()
        self.assertEqual(defaults.default_agent_model_ref, self.replacement_uuid)
        link.refresh_from_db()
        self.assertEqual(link.sl_config_uuid, self.replacement_uuid)

    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_agent_connectivity_failure_removes_candidate_and_keeps_no_default(
        self,
        request_json,
    ):
        connection_error = sl_client.LensBridgeError("provider failure")
        request_json.side_effect = [
            {"uuid": str(self.model_uuid)},
            connection_error,
            None,
        ]

        result = deployment_ai_model.ensure_platform_ai_model(self.config)

        self.assertFalse(result.connectivity_ok)
        self.assertFalse(result.applied)
        self.assertFalse(
            LensOrgModelLink.objects.filter(sl_config_uuid=self.model_uuid).exists()
        )
        request_json.assert_any_call(
            "DELETE",
            f"/api/v1/admin/llm-config/{self.model_uuid}/",
        )

    @patch(
        "apps.lens_bridge.services.deployment_ai_model._persist_link",
        side_effect=DatabaseError("database unavailable"),
    )
    @patch("apps.lens_bridge.services.deployment_ai_model.sl_client.request_json")
    def test_new_source_lens_model_is_removed_when_link_persistence_fails(
        self,
        request_json,
        _persist_link,
    ):
        request_json.side_effect = [
            {"uuid": str(self.model_uuid)},
            {"ok": True},
            None,
        ]

        with self.assertRaises(DatabaseError):
            deployment_ai_model.ensure_platform_ai_model(self.config)

        request_json.assert_any_call(
            "DELETE",
            f"/api/v1/admin/llm-config/{self.model_uuid}/",
        )
