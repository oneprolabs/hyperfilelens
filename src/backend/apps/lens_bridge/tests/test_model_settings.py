from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.lens_bridge.models import LensOrgLink, LensOrgModelLink
from apps.lens_bridge.services import platform_lens


class LensOrgModelSettingsApiTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="model-settings",
            name="Model Settings",
        )
        self.user = get_user_model().objects.create_user(
            username="model-settings@example.test",
            email="model-settings@example.test",
        )
        Membership.objects.create(
            organization=self.organization,
            user=self.user,
            role=Membership.Role.OWNER,
        )
        self.agent_uuid = uuid.uuid4()
        self.multimodal_uuid = uuid.uuid4()
        for model_uuid in (self.agent_uuid, self.multimodal_uuid):
            LensOrgModelLink.objects.create(
                organization=self.organization,
                sl_config_uuid=model_uuid,
            )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_patch_persists_independent_agent_and_multimodal_defaults(self):
        response = self.client.patch(
            reverse("lens-org-settings"),
            {
                "default_agent_model_ref": str(self.agent_uuid),
                "default_multimodal_model_ref": str(self.multimodal_uuid),
            },
            format="json",
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 200)
        defaults = LensOrgLink.objects.get(organization=self.organization)
        self.assertEqual(defaults.default_agent_model_ref, self.agent_uuid)
        self.assertEqual(
            defaults.default_multimodal_model_ref,
            self.multimodal_uuid,
        )

    def test_multimodal_default_must_belong_to_organization(self):
        response = self.client.patch(
            reverse("lens-org-settings"),
            {"default_multimodal_model_ref": str(uuid.uuid4())},
            format="json",
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 400)

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_tenant_model_create_cannot_change_source_lens_global_default(
        self,
        request_json,
    ):
        created_uuid = uuid.uuid4()
        request_json.return_value = {
            "uuid": str(created_uuid),
            "provider": "openai_compatible",
            "config": {"model": "tenant/model"},
            "is_active": True,
        }

        response = self.client.post(
            reverse("lens-models-list"),
            {
                "name": "Tenant Model",
                "provider": "openai_compatible",
                "config": {"model": "tenant/model"},
                "is_default": True,
            },
            format="json",
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 201)
        request_json.assert_called_once_with(
            "POST",
            "/api/v1/admin/llm-config/",
            json_body={
                "provider": "openai_compatible",
                "config": {"model": "tenant/model"},
            },
        )

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_config_test_cannot_reuse_stored_model_credentials(self, request_json):
        for field, value in (
            ("config_uuid", str(uuid.uuid4())),
            ("config_id", 42),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    reverse("lens-models-test"),
                    {
                        "provider": "openai_compatible",
                        "config": {"model": "tenant/model"},
                        field: value,
                    },
                    format="json",
                    HTTP_X_ORG_KEY=self.organization.key,
                )
                self.assertEqual(response.status_code, 400)
        request_json.assert_not_called()

    @patch("apps.lens_bridge.services.org_models.active_llm_configs")
    def test_readiness_exposes_inherited_platform_defaults(
        self,
        active_configs,
    ):
        platform_org = platform_lens.get_or_create_platform_org()
        platform_agent = uuid.uuid4()
        platform_multimodal = uuid.uuid4()
        for model_uuid in (platform_agent, platform_multimodal):
            LensOrgModelLink.objects.create(
                organization=platform_org,
                sl_config_uuid=model_uuid,
            )
        LensOrgLink.objects.create(
            organization=platform_org,
            default_agent_model_ref=platform_agent,
            default_multimodal_model_ref=platform_multimodal,
        )
        active_configs.return_value = [
            {"uuid": str(platform_agent), "is_active": True},
            {"uuid": str(platform_multimodal), "is_active": True},
        ]

        response = self.client.get(
            reverse("lens-copilot-readiness"),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["default_agent_model_ref"],
            str(platform_agent),
        )
        self.assertEqual(
            response.data["default_multimodal_model_ref"],
            str(platform_multimodal),
        )
