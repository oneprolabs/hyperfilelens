"""Platform-org AI model APIs for the Admin Console (OSS essential).

These endpoints always target the hidden ``__platform_lens__`` organization so
community Admin can configure models used by the local public Data Gateway.
Mutations require an existing platform-org model link (same ownership gate as
tenant lens APIs).
"""

from __future__ import annotations

import uuid

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.models import Organization
from apps.instance_settings.permissions import HasPlatformPermission
from apps.lens_bridge.api.serializers import LensOrgSettingsSerializer
from apps.lens_bridge.models import LensOrgModelLink
from apps.lens_bridge.services import org_models, platform_lens, provisioning, sl_client
from common.platform_authz import INFRA_AI_MODELS_MANAGE


def _platform_org() -> Organization:
    return platform_lens.get_or_create_platform_org()


def _is_deployment_managed(link: LensOrgModelLink) -> bool:
    return bool(link.management_key)


def _deployment_managed_model_error() -> Response:
    return Response(
        {
            "code": "AI_MODEL_MANAGED_BY_DEPLOYMENT",
            "detail": (
                "This AI model is managed by deployment configuration. "
                "Connection settings are read-only."
            ),
        },
        status=status.HTTP_409_CONFLICT,
    )


def _set_platform_default_model_ref(
    org: Organization,
    config_uuid: uuid.UUID | None,
    *,
    role: str = "agent",
) -> None:
    link = provisioning.get_or_create_org_link(org)
    field_name = (
        "default_agent_model_ref" if role == "agent" else "default_multimodal_model_ref"
    )
    if getattr(link, field_name) == config_uuid:
        return
    setattr(link, field_name, config_uuid)
    link.save(update_fields=[field_name, "updated_at"])


class PlatformOpsLensSettingsView(APIView):
    """Read and update platform-wide Agent and multimodal defaults."""

    permission_classes = [HasPlatformPermission.for_actions(INFRA_AI_MODELS_MANAGE)]

    def get(self, request):
        link = provisioning.get_or_create_org_link(_platform_org())
        return Response(
            LensOrgSettingsSerializer(
                {
                    "default_agent_model_ref": link.default_agent_model_ref,
                    "default_multimodal_model_ref": link.default_multimodal_model_ref,
                }
            ).data
        )

    def patch(self, request):
        body = LensOrgSettingsSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        org = _platform_org()
        for role, field_name in (
            ("agent", "default_agent_model_ref"),
            ("multimodal", "default_multimodal_model_ref"),
        ):
            if field_name not in body.validated_data:
                continue
            model_ref = body.validated_data[field_name]
            org_models.validate_default_model_ref(
                org,
                model_ref,
                field_name=field_name,
            )
            _set_platform_default_model_ref(
                org,
                model_ref,
                role=role,
            )
        return self.get(request)


class PlatformOpsLensModelProxyView(APIView):
    """Admin Console: SL admin LLM config for the platform organization."""

    permission_classes = [HasPlatformPermission.for_actions(INFRA_AI_MODELS_MANAGE)]

    def get(self, request, config_uuid=None):
        org = _platform_org()
        url_name = getattr(request.resolver_match, "url_name", "")
        if url_name == "platform-ops-lens-models-providers":
            data = sl_client.request_json("GET", "/api/v1/admin/llm-config/providers/")
        elif url_name == "platform-ops-lens-models-catalog":
            data = sl_client.request_json("GET", "/api/v1/admin/llm-config/models/")
        elif config_uuid:
            link = org_models.require_org_model(org, config_uuid)
            data = sl_client.request_json(
                "GET", f"/api/v1/admin/llm-config/{config_uuid}/"
            )
            data = org_models.merge_model_display_name(data, link)
        else:
            data = org_models.list_org_model_configs(org)
        return Response(data)

    def post(self, request, config_uuid=None):
        org = _platform_org()
        url_name = getattr(request.resolver_match, "url_name", "")
        if url_name == "platform-ops-lens-models-test":
            data = sl_client.request_json(
                "POST",
                "/api/v1/admin/llm-config/test/",
                json_body=request.data,
            )
            return Response(data)
        if url_name == "platform-ops-lens-models-test-call" and config_uuid:
            org_models.require_org_model(org, config_uuid)
            data = sl_client.request_json(
                "POST",
                f"/api/v1/admin/llm-config/{config_uuid}/test-call/",
                json_body=request.data,
            )
            return Response(data)
        body = dict(request.data)
        display_name = body.pop("name", None)
        make_agent_default = body.pop("is_default", None) is True
        data = sl_client.request_json(
            "POST", "/api/v1/admin/llm-config/", json_body=body
        )
        config_uuid_created = data.get("uuid")
        if config_uuid_created:
            link = org_models.register_org_model(
                org=org,
                sl_config_uuid=uuid.UUID(str(config_uuid_created)),
                created_by=request.user,
            )
            org_models.set_model_display_name(link, display_name)
            if make_agent_default:
                _set_platform_default_model_ref(
                    org,
                    uuid.UUID(str(config_uuid_created)),
                )
            data = org_models.merge_model_display_name(data, link)
        return Response(data, status=status.HTTP_201_CREATED)

    def put(self, request, config_uuid):
        org = _platform_org()
        link = org_models.require_org_model(org, config_uuid)
        if _is_deployment_managed(link) and (
            link.is_deployment_history or set(request.data) - {"is_default"}
        ):
            return _deployment_managed_model_error()
        body = dict(request.data)
        display_name = body.pop("name", None)
        make_agent_default = body.pop("is_default", None) is True
        if body:
            data = sl_client.request_json(
                "PUT",
                f"/api/v1/admin/llm-config/{config_uuid}/",
                json_body=body,
            )
        else:
            data = sl_client.request_json(
                "GET",
                f"/api/v1/admin/llm-config/{config_uuid}/",
            )
        org_models.set_model_display_name(link, display_name)
        link.refresh_from_db(fields=["display_name"])
        if make_agent_default:
            _set_platform_default_model_ref(org, config_uuid)
        return Response(org_models.merge_model_display_name(data, link))

    def patch(self, request, config_uuid):
        return self.put(request, config_uuid)

    def delete(self, request, config_uuid):
        org = _platform_org()
        link = org_models.require_org_model(org, config_uuid)
        if _is_deployment_managed(link):
            return _deployment_managed_model_error()
        sl_client.request_json("DELETE", f"/api/v1/admin/llm-config/{config_uuid}/")
        LensOrgModelLink.objects.filter(
            organization=org,
            sl_config_uuid=config_uuid,
        ).update(is_deleted=True)
        platform_defaults = provisioning.get_or_create_org_link(org)
        if platform_defaults.default_agent_model_ref == config_uuid:
            _set_platform_default_model_ref(org, None)
        if platform_defaults.default_multimodal_model_ref == config_uuid:
            _set_platform_default_model_ref(
                org,
                None,
                role="multimodal",
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
