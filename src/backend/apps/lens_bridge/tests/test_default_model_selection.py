from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase

from apps.iam.models import Organization
from apps.lens_bridge.models import LensOrgLink, LensOrgModelLink
from apps.lens_bridge.services import org_models, platform_lens, provisioning


class DefaultModelSelectionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="tenant-one", name="Tenant One")

    @patch("apps.lens_bridge.services.org_models.active_llm_configs")
    def test_source_lens_global_default_is_not_an_hfl_role_default(self, active_configs):
        active_configs.return_value = [
            {"uuid": "first", "is_active": True},
            {"uuid": "selected", "is_active": True, "is_default": True},
        ]
        self.assertIsNone(provisioning.default_model_ref_for_org(self.org))

    @patch("apps.lens_bridge.services.org_models.active_llm_configs")
    def test_tenant_explicit_default_wins_over_platform_default(self, active_configs):
        tenant_uuid = uuid.uuid4()
        LensOrgModelLink.objects.create(
            organization=self.org,
            sl_config_uuid=tenant_uuid,
        )
        LensOrgLink.objects.create(
            organization=self.org,
            default_agent_model_ref=tenant_uuid,
        )
        active_configs.return_value = [
            {"uuid": "platform", "is_active": True, "is_default": True},
            {"uuid": str(tenant_uuid), "is_active": True},
        ]
        self.assertEqual(
            provisioning.default_model_ref_for_org(self.org),
            str(tenant_uuid),
        )

    @patch("apps.lens_bridge.services.org_models.active_llm_configs")
    def test_models_are_not_excluded_from_agent_default_by_origin(
        self, active_configs
    ):
        multimodal_uuid = uuid.uuid4()
        platform_org = platform_lens.get_or_create_platform_org()
        LensOrgModelLink.objects.create(
            organization=platform_org,
            sl_config_uuid=multimodal_uuid,
        )
        LensOrgLink.objects.create(
            organization=platform_org,
            default_multimodal_model_ref=multimodal_uuid,
        )
        active_configs.return_value = [{"uuid": str(multimodal_uuid), "is_active": True}]
        self.assertEqual(
            provisioning.default_model_ref_for_org(self.org),
            str(multimodal_uuid),
        )
        self.assertEqual(
            provisioning.default_multimodal_model_ref_for_org(self.org),
            str(multimodal_uuid),
        )

    @patch("apps.lens_bridge.services.org_models.active_llm_configs")
    def test_first_active_model_becomes_agent_default_when_unset(self, active_configs):
        first_uuid = uuid.uuid4()
        second_uuid = uuid.uuid4()
        platform_org = platform_lens.get_or_create_platform_org()
        LensOrgModelLink.objects.create(
            organization=platform_org,
            sl_config_uuid=first_uuid,
        )
        LensOrgModelLink.objects.create(
            organization=platform_org,
            sl_config_uuid=second_uuid,
        )
        active_configs.return_value = [
            {"uuid": str(first_uuid), "is_active": True},
            {"uuid": str(second_uuid), "is_active": True},
        ]
        self.assertEqual(
            provisioning.default_model_ref_for_org(self.org),
            str(first_uuid),
        )

    @patch("apps.lens_bridge.services.org_models.sl_client.request_json")
    def test_org_model_catalog_returns_normal_models(self, request_json):
        model_uuid = uuid.uuid4()
        org_models.register_org_model(org=self.org, sl_config_uuid=model_uuid)
        request_json.return_value = [
            {
                "uuid": str(model_uuid),
                "provider": "openai_compatible",
                "config": {"model": "model/one"},
                "is_active": True,
            }
        ]
        rows = org_models.list_org_model_configs(self.org)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("deployment_managed", rows[0])
