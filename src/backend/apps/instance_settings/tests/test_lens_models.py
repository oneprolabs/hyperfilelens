from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Organization
from apps.instance_settings.tests.helpers import ensure_ops_staff_role
from apps.lens_bridge.models import LensOrgModelLink
from apps.lens_bridge.services import deployment_ai_model, platform_lens


@override_settings(HFL_PLATFORM_OPS_ENABLED=True)
class HostPlatformLensModelTests(TestCase):
    """Community Admin AI Models must hit Host platform-org APIs without EE."""

    model_uuid = uuid.UUID("68c7f764-561c-475a-9cc4-50f6f9457b5c")
    foreign_uuid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    path = f"/api/v1/platform-ops/lens/models/{model_uuid}"

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="model-admin@example.com",
            email="model-admin@example.com",
            password="Pass1234",
            is_staff=True,
        )
        ensure_ops_staff_role(self.staff)
        self.client.force_authenticate(user=self.staff)
        self.client.defaults["HTTP_X_HFL_SITE_ROLE"] = "ops"
        self.platform_org = platform_lens.get_or_create_platform_org()
        self.link = LensOrgModelLink.objects.create(
            organization=self.platform_org,
            sl_config_uuid=self.model_uuid,
            display_name="Deployment Model",
            management_key=deployment_ai_model.DEPLOYMENT_MODEL_MANAGEMENT_KEY,
        )

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_list_models_on_host_without_extension(self, request_json):
        request_json.return_value = [
            {
                "uuid": str(self.model_uuid),
                "provider": "openai_compatible",
                "is_active": True,
            },
            {
                "uuid": str(self.foreign_uuid),
                "provider": "openai_compatible",
                "is_active": True,
            },
        ]
        response = self.client.get("/api/v1/platform-ops/lens/models")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        uuids = {str(row["uuid"]) for row in response.data}
        self.assertEqual(uuids, {str(self.model_uuid)})

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_detail_marks_deployment_managed_model(self, request_json):
        request_json.return_value = {
            "uuid": str(self.model_uuid),
            "provider": "openai_compatible",
            "config": {"model": "model/one", "api_key": "********"},
            "is_active": True,
            "is_default": True,
        }

        response = self.client.get(self.path)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["deployment_managed"])
        self.assertEqual(response.data["name"], "Deployment Model")

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_connection_fields_are_read_only(self, request_json):
        response = self.client.patch(
            self.path,
            {"config": {"model": "other"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "AI_MODEL_MANAGED_BY_DEPLOYMENT")
        request_json.assert_not_called()

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_foreign_model_uuid_is_rejected(self, request_json):
        foreign_org = Organization.objects.create(
            key="tenant-org",
            name="Tenant Org",
        )
        LensOrgModelLink.objects.create(
            organization=foreign_org,
            sl_config_uuid=self.foreign_uuid,
            display_name="Tenant Model",
        )
        foreign_path = f"/api/v1/platform-ops/lens/models/{self.foreign_uuid}"

        get_response = self.client.get(foreign_path)
        self.assertEqual(get_response.status_code, status.HTTP_404_NOT_FOUND)

        patch_response = self.client.patch(
            foreign_path,
            {"name": "Hijacked"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_404_NOT_FOUND)

        delete_response = self.client.delete(foreign_path)
        self.assertEqual(delete_response.status_code, status.HTTP_404_NOT_FOUND)

        test_response = self.client.post(f"{foreign_path}/test-call", {}, format="json")
        self.assertEqual(test_response.status_code, status.HTTP_404_NOT_FOUND)
        request_json.assert_not_called()
