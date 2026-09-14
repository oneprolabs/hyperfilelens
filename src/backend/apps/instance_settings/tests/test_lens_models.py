from __future__ import annotations

import uuid
from unittest.mock import call, patch

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

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_saved_model_connection_uses_sourcelens_test_call_contract(
        self,
        request_json,
    ):
        request_json.return_value = {"ok": True}

        response = self.client.post(f"{self.path}/test-call", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        request_json.assert_called_once_with(
            "POST",
            "/api/v1/admin/llm-config/test-call/",
            json_body={
                "config_uuid": str(self.model_uuid),
                "prompt": "Hi",
                "max_tokens": 64,
            },
        )

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_active_model_is_tested_before_it_is_created(self, request_json):
        created_uuid = uuid.UUID("11111111-2222-3333-4444-555555555555")
        model_body = {
            "provider": "openai_compatible",
            "config": {
                "model": "example/chat-model",
                "api_key": "secret-value",
                "api_base": "https://models.example.test/v1",
            },
            "is_active": True,
        }
        request_json.side_effect = [
            {"ok": True, "message": "OK"},
            {"uuid": str(created_uuid), **model_body},
        ]

        response = self.client.post(
            "/api/v1/platform-ops/lens/models",
            {"name": "Example model", **model_body},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            request_json.call_args_list,
            [
                call(
                    "POST",
                    "/api/v1/admin/llm-config/test/",
                    json_body=model_body,
                ),
                call(
                    "POST",
                    "/api/v1/admin/llm-config/",
                    json_body=model_body,
                ),
            ],
        )
        created_link = LensOrgModelLink.objects.get(sl_config_uuid=created_uuid)
        self.assertEqual(created_link.organization, self.platform_org)
        self.assertEqual(created_link.display_name, "Example model")

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_active_model_is_not_created_when_connection_test_fails(
        self,
        request_json,
    ):
        request_json.return_value = {
            "ok": False,
            "message": "The API key was rejected.",
        }

        response = self.client.post(
            "/api/v1/platform-ops/lens/models",
            {
                "name": "Unavailable model",
                "provider": "openai_compatible",
                "config": {
                    "model": "example/chat-model",
                    "api_key": "invalid-value",
                },
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["data"]["code"],
            "AI_MODEL.CONNECTION_TEST_FAILED",
        )
        self.assertEqual(request_json.call_count, 1)
        self.assertFalse(
            LensOrgModelLink.objects.filter(display_name="Unavailable model").exists()
        )

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_active_model_rejects_ambiguous_connection_test_result(
        self,
        request_json,
    ):
        request_json.return_value = {"message": "OK"}

        response = self.client.post(
            "/api/v1/platform-ops/lens/models",
            {
                "name": "Ambiguous model",
                "provider": "openai_compatible",
                "config": {
                    "model": "example/chat-model",
                    "api_key": "secret-value",
                },
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(request_json.call_count, 1)

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_inactive_model_can_be_created_without_connection_test(
        self,
        request_json,
    ):
        created_uuid = uuid.UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
        model_body = {
            "provider": "openai_compatible",
            "config": {
                "model": "example/chat-model",
                "api_key": "secret-value",
            },
            "is_active": False,
        }
        request_json.return_value = {"uuid": str(created_uuid), **model_body}

        response = self.client.post(
            "/api/v1/platform-ops/lens/models",
            {"name": "Inactive model", **model_body},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        request_json.assert_called_once_with(
            "POST",
            "/api/v1/admin/llm-config/",
            json_body=model_body,
        )

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_inactive_model_requires_credentials_before_it_is_enabled(
        self,
        request_json,
    ):
        manual_uuid = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        LensOrgModelLink.objects.create(
            organization=self.platform_org,
            sl_config_uuid=manual_uuid,
            display_name="Inactive model",
        )
        response = self.client.patch(
            f"/api/v1/platform-ops/lens/models/{manual_uuid}",
            {"is_active": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["data"]["code"],
            "AI_MODEL.CONNECTION_TEST_REQUIRED",
        )
        request_json.assert_not_called()

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_inactive_model_stays_disabled_when_connection_test_fails(
        self,
        request_json,
    ):
        manual_uuid = uuid.UUID("cccccccc-dddd-eeee-ffff-000000000000")
        LensOrgModelLink.objects.create(
            organization=self.platform_org,
            sl_config_uuid=manual_uuid,
            display_name="Unavailable model",
        )
        request_json.return_value = {
            "success": False,
            "detail": "The provider is unavailable.",
        }
        model_body = {
            "provider": "openai_compatible",
            "config": {
                "model": "example/chat-model",
                "api_key": "invalid-secret-value",
            },
            "is_active": True,
        }

        response = self.client.patch(
            f"/api/v1/platform-ops/lens/models/{manual_uuid}",
            model_body,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["data"]["code"],
            "AI_MODEL.CONNECTION_TEST_FAILED",
        )
        request_json.assert_called_once_with(
            "POST",
            "/api/v1/admin/llm-config/test/",
            json_body=model_body,
        )

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_enabling_with_edited_settings_tests_the_new_configuration(
        self,
        request_json,
    ):
        manual_uuid = uuid.UUID("dddddddd-eeee-ffff-0000-111111111111")
        LensOrgModelLink.objects.create(
            organization=self.platform_org,
            sl_config_uuid=manual_uuid,
            display_name="Inactive model",
        )
        model_body = {
            "provider": "openai_compatible",
            "config": {
                "model": "example/new-model",
                "api_key": "new-secret-value",
            },
            "is_active": True,
        }
        request_json.side_effect = [
            {"ok": True},
            {"uuid": str(manual_uuid), **model_body},
        ]

        response = self.client.patch(
            f"/api/v1/platform-ops/lens/models/{manual_uuid}",
            {"name": "Updated model", **model_body},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            request_json.call_args_list,
            [
                call(
                    "POST",
                    "/api/v1/admin/llm-config/test/",
                    json_body=model_body,
                ),
                call(
                    "PUT",
                    f"/api/v1/admin/llm-config/{manual_uuid}/",
                    json_body=model_body,
                ),
            ],
        )

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_connection_update_without_active_flag_is_still_tested(
        self,
        request_json,
    ):
        manual_uuid = uuid.UUID("eeeeeeee-ffff-0000-1111-222222222222")
        LensOrgModelLink.objects.create(
            organization=self.platform_org,
            sl_config_uuid=manual_uuid,
            display_name="Active model",
        )
        model_body = {
            "provider": "openai_compatible",
            "config": {
                "model": "example/new-model",
                "api_key": "new-secret-value",
            },
        }
        request_json.side_effect = [
            {"uuid": str(manual_uuid), "is_active": True},
            {
                "ok": False,
                "detail": "The provider rejected the credentials.",
            },
        ]

        response = self.client.patch(
            f"/api/v1/platform-ops/lens/models/{manual_uuid}",
            model_body,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            request_json.call_args_list,
            [
                call(
                    "GET",
                    f"/api/v1/admin/llm-config/{manual_uuid}/",
                ),
                call(
                    "POST",
                    "/api/v1/admin/llm-config/test/",
                    json_body=model_body,
                ),
            ],
        )

    @patch("apps.instance_settings.api.views.lens_models.sl_client.request_json")
    def test_inactive_connection_update_without_active_flag_skips_testing(
        self,
        request_json,
    ):
        manual_uuid = uuid.UUID("ffffffff-0000-1111-2222-333333333333")
        LensOrgModelLink.objects.create(
            organization=self.platform_org,
            sl_config_uuid=manual_uuid,
            display_name="Inactive model",
        )
        model_body = {
            "provider": "openai_compatible",
            "config": {"model": "example/new-model"},
        }
        request_json.side_effect = [
            {"uuid": str(manual_uuid), "is_active": False},
            {"uuid": str(manual_uuid), "is_active": False, **model_body},
        ]

        response = self.client.patch(
            f"/api/v1/platform-ops/lens/models/{manual_uuid}",
            model_body,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            request_json.call_args_list,
            [
                call(
                    "GET",
                    f"/api/v1/admin/llm-config/{manual_uuid}/",
                ),
                call(
                    "PUT",
                    f"/api/v1/admin/llm-config/{manual_uuid}/",
                    json_body=model_body,
                ),
            ],
        )
