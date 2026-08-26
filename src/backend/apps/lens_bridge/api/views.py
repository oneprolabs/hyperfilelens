import uuid
from typing import Any
from urllib.parse import urlencode

from django.core import signing
from django.db import transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.urls import reverse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.permissions_org import (
    IsOrgOperator,
    IsOrgReader,
    IsOrgStaffReader,
    IsOrgWriter,
    get_membership,
)
from apps.lens_bridge.api.serializers import (
    LensAdmissionPreviewSerializer,
    LensChatBindingEnsureSerializer,
    LensCopilotGatewayOptionSerializer,
    LensGatewayChatWorkloadSerializer,
    LensGatewayEnableAiSerializer,
    LensKnowledgeSourceCreateSerializer,
    LensKnowledgeSourceSerializer,
    LensKnowledgeSourceUpdateSerializer,
    LensOrgSettingsSerializer,
    LensRunCreateSerializer,
    LensRunFeedbackSerializer,
    LensShareTitleSerializer,
    LensSessionCreateSerializer,
    LensSnapshotBrowseCreateSerializer,
    LensSessionLinkSerializer,
    LensSessionTitleSerializer,
    LensSessionUpdateSerializer,
    LensScopePreviewCreateSerializer,
)
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
)
from apps.lens_bridge.services import (
    gateway_chat_queue,
    knowledge_source_sync,
    org_models,
    provisioning,
    sl_client,
    usage,
)
from apps.lens_bridge.services import (
    assistant_access,
    chat_binding as chat_binding_service,
    copilot as copilot_service,
)
from apps.lens_bridge.services.assistants import (
    assistant_form_options,
    create_org_assistant,
    delete_org_assistant,
    get_org_assistant,
    list_org_assistants,
    update_org_assistant,
)
from apps.lens_bridge.services.org_mcp_servers import (
    create_org_mcp_server,
    delete_org_mcp_server,
    get_org_mcp_server,
    list_org_mcp_servers,
    update_org_mcp_server,
)
from apps.lens_bridge.services.skills import beautify_skill
from apps.lens_bridge.services.org_skills import (
    create_org_skill,
    delete_org_skill,
    get_org_skill,
    list_org_skills,
    update_org_skill,
)
from apps.lens_bridge.services.chat_lifecycle_errors import (
    classify_chat_lifecycle_error,
)
from common.drf.org_scoped import OrgScopedMixin
from common.drf.renderers import ServerSentEventsRenderer


_ATTACHMENT_PROXY_SIGNING_SALT = "lens_bridge.copilot_attachment"
_OUTPUT_FILE_PROXY_SIGNING_SALT = "lens_bridge.copilot_output_file"


class SourceLensMaintenanceUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "AI services are temporarily unavailable during maintenance."
    default_code = "sourcelens_maintenance"


class CopilotRunConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This chat already has an active response."
    default_code = "copilot_run_active"


def _source_lens_contract_error(resource: str) -> sl_client.LensBridgeError:
    return sl_client.LensBridgeError(
        f"SourceLens returned an invalid {resource} response."
    )


class LensCopilotSnapshotBrowseView(OrgScopedMixin, APIView):
    """Dispatch and inspect snapshot browsing for Insight without blocking API."""

    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        body = LensSnapshotBrowseCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        from apps.lens_bridge.services import snapshot_scope_tasks

        task = snapshot_scope_tasks.dispatch_snapshot_browse(
            organization_id=self.org.id,
            directory_id=body.validated_data["directory_id"],
            backup_source_snapshot_id=body.validated_data[
                "backup_source_snapshot_id"
            ],
            gateway_link_id=body.validated_data["gateway_link_id"],
            requesting_user_id=request.user.id,
            path=body.validated_data.get("path", ""),
            limit=body.validated_data["limit"],
            correlation_id=f"user:{request.user.id}:{uuid.uuid4()}",
        )
        return Response(
            {"task_id": str(task.id), "status": str(task.status)},
            status=status.HTTP_202_ACCEPTED,
        )


class LensCopilotSnapshotBrowseTaskView(OrgScopedMixin, APIView):
    """Return only Insight browse tasks owned by the active organization."""

    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request, task_id):
        from apps.lens_bridge.services import snapshot_scope_tasks
        from apps.node.models import NodeTask

        try:
            task = snapshot_scope_tasks.task_for_org(
                organization=self.org,
                task_id=str(task_id),
            )
        except ValidationError as exc:
            raise NotFound("Insight snapshot operation was not found.") from exc
        if (
            task.correlation_type != snapshot_scope_tasks.BROWSE_CORRELATION_TYPE
            or task.kind != "lens.snapshot.browse"
            or not str(task.correlation_id or "").startswith(f"user:{request.user.id}:")
        ):
            raise NotFound("Insight snapshot operation was not found.")
        terminal_failure = task.status in {
            NodeTask.Status.FAILED,
            NodeTask.Status.TIMEOUT,
            NodeTask.Status.CANCELED,
        }
        failure = (
            snapshot_scope_tasks.snapshot_task_failure(
                task,
                default="Unable to browse the selected snapshot. Try again.",
            )
            if terminal_failure
            else None
        )
        payload = {
            "task_id": str(task.id),
            "status": str(task.status),
            "error": failure.message if failure else "",
        }
        if failure:
            payload["error_code"] = failure.code
            payload["retryable"] = failure.retryable
        if task.status == NodeTask.Status.SUCCESS:
            payload["entries"] = snapshot_scope_tasks.normalized_browse_entries(task)
            result = task.result if isinstance(task.result, dict) else {}
            payload["has_more"] = bool(result.get("has_more"))
            payload["skipped_special_count"] = (
                snapshot_scope_tasks.browse_skipped_special_count(task)
            )
        return Response(payload)


class LensCopilotScopePreviewView(OrgScopedMixin, APIView):
    """Summarize one selected snapshot path without blocking the API worker."""

    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        body = LensScopePreviewCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        from apps.lens_bridge.services import chat_selection_preview

        payload = chat_selection_preview.start_scope_preview(
            organization=self.org,
            user=request.user,
            snapshot_id=body.validated_data["backup_source_snapshot_id"],
            directory_id=body.validated_data["directory_id"],
            source_path=body.validated_data["source_path"],
            gateway_link_id=body.validated_data["gateway_link_id"],
            request_token=str(body.validated_data["request_token"]),
            attempt=body.validated_data["attempt"],
        )
        response_status = (
            status.HTTP_200_OK
            if payload["status"] == "success"
            else status.HTTP_202_ACCEPTED
        )
        return Response(payload, status=response_status)


class LensCopilotScopePreviewTaskView(OrgScopedMixin, APIView):
    """Inspect or cancel one current-user selection summary task."""

    permission_classes = [IsAuthenticated, IsOrgOperator]

    def get(self, request, task_id):
        from apps.lens_bridge.services import chat_selection_preview

        task = chat_selection_preview.get_scope_preview_task(
            organization=self.org,
            user=request.user,
            task_id=str(task_id),
        )
        return Response(chat_selection_preview.scope_task_payload(task))

    def delete(self, request, task_id):
        from apps.lens_bridge.services import chat_selection_preview

        task = chat_selection_preview.cancel_scope_preview_task(
            organization=self.org,
            user=request.user,
            task_id=str(task_id),
        )
        return Response(chat_selection_preview.scope_task_payload(task))


class LensCopilotAdmissionPreviewView(OrgScopedMixin, APIView):
    """Return the current organization's safe Chat quota projection."""

    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        body = LensAdmissionPreviewSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        from apps.lens_bridge.services import chat_selection_preview

        return Response(
            chat_selection_preview.admission_preview(
                organization=self.org,
                user=request.user,
                **body.validated_data,
            )
        )


def _lens_error_response(exc: sl_client.LensBridgeError) -> Response:
    """Preserve retryable SourceLens status codes at the HFL API boundary."""
    response_status = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if isinstance(exc, sl_client.LensBridgeUnavailable)
        else status.HTTP_502_BAD_GATEWAY
    )
    return Response(
        {"detail": str(exc)},
        status=response_status,
    )


def _source_lens_list_rows(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return [row for row in data["results"] if isinstance(row, dict)]
    return []


def _source_lens_session_meta(
    *,
    hfl_user,
    session_uuids: set[str],
) -> dict[str, dict]:
    """Load SourceLens session metadata without imposing a total row limit."""

    remaining = set(session_uuids)
    metadata: dict[str, dict] = {}
    page = 1
    while remaining:
        payload = sl_client.request_json(
            "GET",
            "/api/lens/sessions/",
            params={"page": page, "page_size": 500},
            hfl_user=hfl_user,
        )
        rows = _source_lens_list_rows(payload)
        for row in rows:
            session_uuid = str(row.get("uuid") or "")
            if session_uuid in remaining:
                metadata[session_uuid] = row
                remaining.remove(session_uuid)
        if not rows or not isinstance(payload, dict) or not payload.get("next"):
            break
        page += 1
    return metadata


def _canonical_attachment_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise _source_lens_contract_error("attachment") from exc


def _attachment_proxy_url(session_id: int, attachment_uuid: str) -> str:
    """Build a tamper-evident URL; normal HFL auth still gates every request."""

    attachment_uuid = _canonical_attachment_uuid(attachment_uuid)
    path = reverse(
        "lens-copilot-session-attachment-content",
        kwargs={
            "pk": session_id,
            "attachment_uuid": attachment_uuid,
        },
    )
    token = signing.dumps(
        {
            "session_id": session_id,
            "attachment_uuid": attachment_uuid,
        },
        salt=_ATTACHMENT_PROXY_SIGNING_SALT,
        compress=True,
    )
    return f"{path}?{urlencode({'token': token})}"


def _require_attachment_proxy_token(
    request,
    *,
    session_id: int,
    attachment_uuid: uuid.UUID,
) -> None:
    token = request.query_params.get("token", "")
    try:
        payload = signing.loads(
            token,
            salt=_ATTACHMENT_PROXY_SIGNING_SALT,
        )
    except signing.BadSignature as exc:
        raise NotFound() from exc
    if not isinstance(payload, dict) or payload != {
        "session_id": session_id,
        "attachment_uuid": str(attachment_uuid),
    }:
        raise NotFound()


def _output_file_proxy_url(session_id: int, file_uuid: str) -> str:
    """Build a tamper-evident URL for a run output file; HFL auth still gates it."""

    file_uuid = _canonical_attachment_uuid(file_uuid)
    path = reverse(
        "lens-copilot-session-output-file-content",
        kwargs={
            "pk": session_id,
            "file_uuid": file_uuid,
        },
    )
    token = signing.dumps(
        {
            "session_id": session_id,
            "file_uuid": file_uuid,
        },
        salt=_OUTPUT_FILE_PROXY_SIGNING_SALT,
        compress=True,
    )
    return f"{path}?{urlencode({'token': token})}"


def _require_output_file_proxy_token(
    request,
    *,
    session_id: int,
    file_uuid: uuid.UUID,
) -> None:
    token = request.query_params.get("token", "")
    try:
        payload = signing.loads(
            token,
            salt=_OUTPUT_FILE_PROXY_SIGNING_SALT,
        )
    except signing.BadSignature as exc:
        raise NotFound() from exc
    if not isinstance(payload, dict) or payload != {
        "session_id": session_id,
        "file_uuid": str(file_uuid),
    }:
        raise NotFound()


def _rewrite_attachment_urls(messages, *, session_id: int):
    if not isinstance(messages, list):
        return messages
    for message in messages:
        if not isinstance(message, dict):
            continue
        attachments = message.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict) or not attachment.get("uuid"):
                    continue
                attachment["url"] = _attachment_proxy_url(
                    session_id,
                    str(attachment["uuid"]),
                )
        output_files = message.get("output_files")
        if isinstance(output_files, list):
            for output_file in output_files:
                if not isinstance(output_file, dict) or not output_file.get("uuid"):
                    continue
                output_file["url"] = _output_file_proxy_url(
                    session_id,
                    str(output_file["uuid"]),
                )
    return messages


def _tenant_model_payload(data) -> tuple[dict, str | None]:
    """Remove SourceLens-global fields from a tenant model mutation."""

    body = dict(data)
    display_name = body.pop("name", None)
    body.pop("is_default", None)
    return body, display_name


def health(request):
    ping = sl_client.ping()
    return JsonResponse({"app": "lens_bridge", "status": "ok", "lens": ping})


class LensModelProxyView(OrgScopedMixin, APIView):
    """Proxy SourceLens LLM config admin API (organization-scoped ownership)."""

    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.request.method not in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), IsOrgWriter()]
        return super().get_permissions()

    def get(self, request, config_uuid=None):
        url_name = getattr(request.resolver_match, "url_name", "")
        if url_name == "lens-models-providers":
            data = sl_client.request_json("GET", "/api/v1/admin/llm-config/providers/")
        elif url_name == "lens-models-catalog":
            data = sl_client.request_json("GET", "/api/v1/admin/llm-config/models/")
        elif config_uuid:
            link = org_models.require_org_model(self.org, config_uuid)
            data = sl_client.request_json(
                "GET", f"/api/v1/admin/llm-config/{config_uuid}/"
            )
            data = org_models.merge_model_display_name(data, link)
        else:
            data = org_models.list_org_model_configs(self.org)
        return Response(data)

    def post(self, request, config_uuid=None):
        url_name = getattr(request.resolver_match, "url_name", "")
        if url_name == "lens-models-test":
            data = sl_client.request_json(
                "POST",
                "/api/v1/admin/llm-config/test/",
                json_body=request.data,
            )
            return Response(data)
        if url_name == "lens-models-test-call" and config_uuid:
            org_models.require_org_model(self.org, config_uuid)
            data = sl_client.request_json(
                "POST",
                f"/api/v1/admin/llm-config/{config_uuid}/test-call/",
                json_body=request.data,
            )
            return Response(data)
        body, display_name = _tenant_model_payload(request.data)
        data = sl_client.request_json(
            "POST", "/api/v1/admin/llm-config/", json_body=body
        )
        config_uuid_created = data.get("uuid")
        if config_uuid_created:
            link = org_models.register_org_model(
                org=self.org,
                sl_config_uuid=uuid.UUID(str(config_uuid_created)),
                created_by=request.user,
            )
            org_models.set_model_display_name(link, display_name)
            data = org_models.merge_model_display_name(data, link)
        return Response(data, status=status.HTTP_201_CREATED)

    def put(self, request, config_uuid):
        link = org_models.require_org_model(self.org, config_uuid)
        body, display_name = _tenant_model_payload(request.data)
        data = sl_client.request_json(
            "PUT",
            f"/api/v1/admin/llm-config/{config_uuid}/",
            json_body=body,
        )
        org_models.set_model_display_name(link, display_name)
        return Response(org_models.merge_model_display_name(data, link))

    def patch(self, request, config_uuid):
        """Partial update — SourceLens admin API accepts PUT, not PATCH."""
        link = org_models.require_org_model(self.org, config_uuid)
        body, display_name = _tenant_model_payload(request.data)
        if body:
            data = sl_client.request_json(
                "PUT",
                f"/api/v1/admin/llm-config/{config_uuid}/",
                json_body=body,
            )
        else:
            data = sl_client.request_json(
                "GET", f"/api/v1/admin/llm-config/{config_uuid}/"
            )
        org_models.set_model_display_name(link, display_name)
        link.refresh_from_db(fields=["display_name"])
        return Response(org_models.merge_model_display_name(data, link))

    def delete(self, request, config_uuid):
        org_models.delete_org_model(self.org, config_uuid)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LensOrgSettingsView(OrgScopedMixin, APIView):
    """Per-organization SourceLens defaults (e.g. default agent model for new Assistants)."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), IsOrgStaffReader()]
        return [IsAuthenticated(), IsOrgWriter()]

    def get(self, request):
        link = provisioning.get_or_create_org_link(self.org)
        return Response(
            LensOrgSettingsSerializer(
                {
                    "default_agent_model_ref": link.default_agent_model_ref,
                    "default_multimodal_model_ref": (
                        link.default_multimodal_model_ref
                    ),
                }
            ).data
        )

    def patch(self, request):
        body = LensOrgSettingsSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        link = provisioning.get_or_create_org_link(self.org)
        if "default_agent_model_ref" in body.validated_data:
            ref = body.validated_data["default_agent_model_ref"]
            org_models.validate_default_model_ref(self.org, ref)
            link.default_agent_model_ref = ref
        if "default_multimodal_model_ref" in body.validated_data:
            ref = body.validated_data["default_multimodal_model_ref"]
            org_models.validate_default_model_ref(
                self.org,
                ref,
                field_name="default_multimodal_model_ref",
            )
            link.default_multimodal_model_ref = ref
        link.save(
            update_fields=[
                "default_agent_model_ref",
                "default_multimodal_model_ref",
                "updated_at",
            ]
        )
        return Response(
            LensOrgSettingsSerializer(
                {
                    "default_agent_model_ref": link.default_agent_model_ref,
                    "default_multimodal_model_ref": (
                        link.default_multimodal_model_ref
                    ),
                }
            ).data
        )


class LensCopilotReadinessView(OrgScopedMixin, APIView):
    """Return sanitized platform AI models available to Copilot."""

    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        active_models = org_models.active_llm_configs_available_to_org(self.org)
        agent_ref, multimodal_ref = provisioning.default_model_refs_for_org(
            self.org,
            tenant_rows=active_models,
        )
        rows = []
        for model in active_models:
            config = (
                model.get("config") if isinstance(model.get("config"), dict) else {}
            )
            rows.append(
                {
                    "uuid": str(model["uuid"]),
                    "name": str(model.get("name") or ""),
                    "provider": str(model.get("provider") or ""),
                    "config": {"model": str(config.get("model") or "")},
                    "is_active": True,
                    "deployment_role": str(model.get("deployment_role") or ""),
                    "is_deployment_history": bool(
                        model.get("is_deployment_history")
                    ),
                    "is_default_agent": bool(
                        model.get("is_default_agent")
                    ),
                    "is_default_multimodal": bool(
                        model.get("is_default_multimodal")
                    ),
                }
            )
        return Response(
            {
                "active_models": rows,
                "default_agent_model_ref": agent_ref,
                "default_multimodal_model_ref": multimodal_ref,
            }
        )


class LensKnowledgeSourceViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    queryset = LensKnowledgeSource.objects.select_related("gateway").all()
    permission_classes = [IsAuthenticated, IsOrgWriter]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsOrgStaffReader()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "create":
            return LensKnowledgeSourceCreateSerializer
        if self.action in ("update", "partial_update"):
            return LensKnowledgeSourceUpdateSerializer
        return LensKnowledgeSourceSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["org"] = self.org
        ctx["gateway_scope"] = LensGatewayLink.GatewayScope.USER
        ctx["gateway_owner_user_id"] = self.request.user.id
        return ctx

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        for ks in queryset:
            knowledge_source_sync.maybe_refresh_degraded_status(ks=ks)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        ks = self.get_object()
        knowledge_source_sync.maybe_refresh_degraded_status(ks=ks)
        serializer = self.get_serializer(ks)
        return Response(serializer.data)

    def perform_create(self, serializer):
        from apps.lens_bridge.services.gateway_execution import (
            require_user_gateway_link,
        )

        with transaction.atomic():
            gateway_link = require_user_gateway_link(
                tenant_organization=self.org,
                gateway_id=serializer.validated_data["gateway"].id,
                owner_user_id=self.request.user.id,
                lock=True,
            )
            ks = serializer.save(
                organization=self.org,
                created_by=self.request.user,
                gateway_link=gateway_link,
                sl_lensnode_uuid=gateway_link.sl_lensnode_uuid,
            )
            ks = knowledge_source_sync.prepare_new_knowledge_source(
                org=self.org,
                ks=ks,
            )
            transaction.on_commit(
                lambda organization_id=self.org.id, knowledge_source_id=ks.id: (
                    knowledge_source_sync.enqueue_knowledge_source_sync(
                        organization_id=organization_id,
                        knowledge_source_id=knowledge_source_id,
                        mode="full",
                    )
                )
            )

    def perform_update(self, serializer):
        if (
            serializer.instance.lifecycle_status
            != LensKnowledgeSource.LifecycleStatus.READY
        ):
            raise ValidationError(
                {"lifecycle_status": "Knowledge source is being deleted."}
            )
        scan_changed = "scan_enabled" in serializer.validated_data
        ks = serializer.save()
        if scan_changed and not ks.scan_enabled:
            ks.status = LensKnowledgeSource.Status.PAUSED
            ks.save(update_fields=["status", "updated_at"])

    def destroy(self, request, *args, **kwargs):
        from apps.lens_bridge.services.knowledge_source_teardown import (
            request_knowledge_source_teardown,
        )

        instance = self.get_object()
        request_knowledge_source_teardown(instance)
        return Response(
            {"id": instance.id, "lifecycle_status": "deleting"},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        ks = self.get_object()
        try:
            ks = knowledge_source_sync.request_knowledge_source_sync(
                org=self.org,
                ks=ks,
                mode="resume",
            )
        except Exception as exc:
            from rest_framework.exceptions import ValidationError as DRFValidationError

            if isinstance(exc, DRFValidationError):
                raise
            ks.status = LensKnowledgeSource.Status.ERROR
            ks.status_detail = str(exc)
            ks.save(update_fields=["status", "status_detail", "updated_at"])
            raise
        return Response(
            LensKnowledgeSourceSerializer(
                ks, context=self.get_serializer_context()
            ).data
        )


class LensGatewayViewSet(OrgScopedMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.action in ("enable_ai", "ai_status", "chat_workload"):
            return [IsAuthenticated(), IsOrgWriter()]
        return super().get_permissions()

    def list(self, request):
        from apps.lens_bridge.services.gateway_insights import (
            list_user_gateway_insight_rows,
        )

        return Response(list_user_gateway_insight_rows(user=request.user))

    @action(detail=True, methods=["post"], url_path="enable-ai")
    def enable_ai(self, request, pk=None):
        gateway = provisioning.require_gateway_node(self.org, int(pk))
        body = LensGatewayEnableAiSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        link = LensGatewayLink.objects.filter(
            organization=self.org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.USER,
            owner_user=request.user,
        ).first()
        if link and link.sl_lensnode_uuid:
            provisioning.sync_gateway_lensnode_status(link)
        link = provisioning.enable_ai_on_gateway(
            org=self.org,
            gateway=gateway,
            name=body.validated_data.get("name") or None,
            owner_user=request.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        payload = provisioning.build_gateway_ai_payload(
            gateway=gateway,
            link=link,
            include_token=True,
        )
        return Response(payload, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="ai")
    def ai_status(self, request, pk=None):
        gateway = provisioning.require_gateway_node(self.org, int(pk))
        from apps.lens_bridge.services.gateway_execution import (
            require_user_gateway_link,
        )

        link = require_user_gateway_link(
            tenant_organization=self.org,
            gateway_id=gateway.id,
            owner_user_id=request.user.id,
            require_ready=False,
        )
        if link and link.sl_lensnode_uuid:
            provisioning.sync_gateway_lensnode_status(link)
        return Response(
            provisioning.build_gateway_ai_payload(
                gateway=gateway,
                link=link,
                include_token=False,
            )
        )

    @action(detail=True, methods=["get", "patch"], url_path="chat-workload")
    def chat_workload(self, request, pk=None):
        gateway = provisioning.require_gateway_node(self.org, int(pk))
        from apps.lens_bridge.services.gateway_execution import (
            require_user_gateway_link,
        )

        link = require_user_gateway_link(
            tenant_organization=self.org,
            gateway_id=gateway.id,
            owner_user_id=request.user.id,
            require_ready=False,
        )
        if request.method == "PATCH":
            body = LensGatewayChatWorkloadSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            from apps.audit.services.interface import write_audit_log_from_request

            with transaction.atomic():
                link = gateway_chat_queue.set_chat_workload_settings(
                    gateway_link=link,
                    **body.validated_data,
                )
                write_audit_log_from_request(
                    request,
                    organization=self.org,
                    action="lens.gateway.chat_workload.update",
                    resource_type="lens_gateway_link",
                    resource_id=str(link.id),
                    resource_name=gateway.name,
                    changes={
                        "chat_prepare_concurrency": int(
                            link.chat_prepare_concurrency
                        ),
                        "chat_queue_capacity": int(link.chat_queue_capacity),
                    },
                )
        return Response(gateway_chat_queue.chat_workload_payload(gateway_link=link))

    @action(detail=True, methods=["get"], url_path="browse")
    def browse(self, request, pk=None):
        path = str(request.query_params.get("path") or "").strip()
        try:
            data = provisioning.browse_gateway_directory(
                org=self.org,
                gateway_id=int(pk),
                path=path,
                expected_scope=LensGatewayLink.GatewayScope.USER,
                expected_owner_user_id=request.user.id,
            )
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response(data)


class LensCopilotBindingView(OrgScopedMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.request.method in ("POST",):
            return [IsAuthenticated(), IsOrgOperator()]
        return super().get_permissions()

    def get(self, request):
        binding = chat_binding_service.get_active_chat_binding(
            self.org, user=request.user
        )
        if binding is None:
            return Response({"binding": None})
        return Response(
            {"binding": chat_binding_service.serialize_chat_binding(binding)},
        )

    def post(self, request):
        body = LensChatBindingEnsureSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            binding = chat_binding_service.ensure_chat_binding(
                self.org,
                user=request.user,
                backup_config_id=body.validated_data["backup_config_id"],
                backup_source_snapshot_id=body.validated_data[
                    "backup_source_snapshot_id"
                ],
                backup_snapshot_directory_id=body.validated_data.get(
                    "backup_snapshot_directory_id"
                ),
                source_path=body.validated_data.get("source_path") or "",
                gateway_link_id=body.validated_data.get("gateway_link_id"),
            )
        except ValidationError:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(
            {"binding": chat_binding_service.serialize_chat_binding(binding)},
            status=status.HTTP_201_CREATED,
        )


class LensCopilotGatewayOptionsView(OrgScopedMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        rows = chat_binding_service.list_gateway_options(self.org, user=request.user)
        return Response(LensCopilotGatewayOptionSerializer(rows, many=True).data)


class LensCopilotAssistantView(OrgScopedMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        membership = get_membership(request)
        try:
            rows = copilot_service.list_copilot_assistants(
                self.org,
                user=request.user,
                membership=membership,
            )
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(rows)


class LensCopilotKnowledgeSourceView(OrgScopedMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        qs = LensKnowledgeSource.objects.filter(
            organization=self.org,
            scan_enabled=True,
            status__in=[
                LensKnowledgeSource.Status.READY,
                LensKnowledgeSource.Status.DEGRADED,
            ],
        ).select_related("gateway")
        return Response(
            LensKnowledgeSourceSerializer(qs, many=True, context={"view": self}).data
        )


class LensAssistantViewSet(OrgScopedMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.action in ("create", "partial_update", "destroy"):
            return [IsAuthenticated(), IsOrgWriter()]
        return super().get_permissions()

    def list(self, request):
        membership = get_membership(request)
        can_manage_all = assistant_access.can_manage_all_assistants(membership)
        try:
            rows = list_org_assistants(
                self.org,
                user=request.user,
                can_manage_all=can_manage_all,
            )
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(rows)

    def retrieve(self, request, pk=None):
        membership = get_membership(request)
        can_manage_all = assistant_access.can_manage_all_assistants(membership)
        try:
            data = get_org_assistant(
                self.org,
                uuid.UUID(str(pk)),
                user=request.user,
                can_manage_all=can_manage_all,
            )
        except NotFound:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)

    def create(self, request):
        try:
            data = create_org_assistant(self.org, dict(request.data), user=request.user)
        except ValidationError:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        membership = get_membership(request)
        can_manage_all = assistant_access.can_manage_all_assistants(membership)
        try:
            data = update_org_assistant(
                self.org,
                uuid.UUID(str(pk)),
                dict(request.data),
                user=request.user,
                can_manage_all=can_manage_all,
            )
        except NotFound:
            raise
        except ValidationError:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)

    def destroy(self, request, pk=None):
        membership = get_membership(request)
        can_manage_all = assistant_access.can_manage_all_assistants(membership)
        try:
            delete_org_assistant(
                self.org,
                uuid.UUID(str(pk)),
                user=request.user,
                can_manage_all=can_manage_all,
            )
        except NotFound:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path="form-options")
    def form_options(self, request):
        try:
            data = assistant_form_options(self.org, user=request.user)
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)


class LensSkillViewSet(OrgScopedMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.action in ("create", "partial_update", "destroy", "beautify"):
            return [IsAuthenticated(), IsOrgWriter()]
        return super().get_permissions()

    def list(self, request):
        try:
            rows = list_org_skills(self.org)
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(rows)

    def retrieve(self, request, pk=None):
        try:
            data = get_org_skill(self.org, uuid.UUID(str(pk)))
        except NotFound:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)

    def create(self, request):
        try:
            data = create_org_skill(
                self.org, dict(request.data), created_by=request.user
            )
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        try:
            data = update_org_skill(self.org, uuid.UUID(str(pk)), dict(request.data))
        except NotFound:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)

    def destroy(self, request, pk=None):
        try:
            delete_org_skill(self.org, uuid.UUID(str(pk)))
        except NotFound:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="beautify")
    def beautify(self, request):
        try:
            data = beautify_skill(dict(request.data))
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)


class LensMcpServerViewSet(OrgScopedMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.action in ("create", "partial_update", "destroy"):
            return [IsAuthenticated(), IsOrgWriter()]
        return super().get_permissions()

    def list(self, request):
        try:
            rows = list_org_mcp_servers(self.org)
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(rows)

    def retrieve(self, request, pk=None):
        try:
            data = get_org_mcp_server(self.org, uuid.UUID(str(pk)))
        except NotFound:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)

    def create(self, request):
        try:
            data = create_org_mcp_server(
                self.org, dict(request.data), created_by=request.user
            )
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        try:
            data = update_org_mcp_server(
                self.org, uuid.UUID(str(pk)), dict(request.data)
            )
        except NotFound:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)

    def destroy(self, request, pk=None):
        try:
            delete_org_mcp_server(self.org, uuid.UUID(str(pk)))
        except NotFound:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LensCopilotSessionViewSet(OrgScopedMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.action in (
            "create",
            "destroy",
            "create_run",
            "feedback",
            "set_model",
            "set_execution",
            "set_title",
            "mark_viewed",
            "cancel_run",
            "retry",
            "pin",
            "unpin",
            "share",
            "share_detail",
            "upload_attachment",
        ):
            return [IsAuthenticated(), IsOrgOperator()]
        if self.action == "attachment_content" and self.request.method == "DELETE":
            return [IsAuthenticated(), IsOrgOperator()]
        return super().get_permissions()

    def _user_sessions(self):
        return LensSessionLink.objects.filter(
            organization=self.org,
            hfl_user=self.request.user,
            status=LensSessionLink.Status.ACTIVE,
        ).select_related("knowledge_source", "gateway_link__gateway")

    def list(self, request):
        rows = list(self._user_sessions().order_by("-created_at", "-id"))
        membership = get_membership(request)
        assistant_meta: dict[str, dict[str, str]] = {}
        try:
            for row in copilot_service.list_copilot_assistants(
                self.org,
                user=request.user,
                membership=membership,
            ):
                uuid_str = str(row.get("uuid") or "")
                if uuid_str:
                    assistant_meta[uuid_str] = {
                        "name": str(row.get("name") or ""),
                        "task": str(row.get("selected_task") or ""),
                    }
        except sl_client.LensBridgeError:
            assistant_meta = {}
        sl_session_meta: dict[str, dict] = {}
        try:
            sl_session_meta = _source_lens_session_meta(
                hfl_user=request.user,
                session_uuids={
                    str(row.sl_session_uuid) for row in rows if row.sl_session_uuid
                },
            )
        except sl_client.LensBridgeError:
            sl_session_meta = {}
        context = {
            "assistant_names": {k: v["name"] for k, v in assistant_meta.items()},
            "assistant_tasks": {k: v["task"] for k, v in assistant_meta.items()},
        }
        payload = list(LensSessionLinkSerializer(rows, many=True, context=context).data)
        for row in payload:
            session_uuid = str(row.get("sl_session_uuid") or "")
            source_row = sl_session_meta.get(session_uuid)
            if isinstance(source_row, dict) and "pinned_at" in source_row:
                pinned_at = source_row.get("pinned_at")
                row["pinned_at"] = (
                    pinned_at if isinstance(pinned_at, str) and pinned_at else None
                )
            if isinstance(source_row, dict) and "has_shareable_answer" in source_row:
                row["has_shareable_answer"] = bool(
                    source_row.get("has_shareable_answer")
                )
        payload.sort(
            key=lambda row: (
                bool(row.get("pinned_at")),
                str(row.get("pinned_at") or ""),
                str(row.get("created_at") or ""),
                int(row.get("id") or 0),
            ),
            reverse=True,
        )
        return Response(payload)

    def _share_response(
        self,
        link: LensSessionLink,
        share: dict[str, Any],
    ) -> dict[str, Any]:
        from apps.lens_bridge.services import copilot_sharing

        access = copilot_sharing.make_share_access_token(link, share)
        payload = {
            field: share[field]
            for field in (
                "uuid",
                "run_uuid",
                "title",
                "question",
                "answer",
                "assistant_name",
                "assistant_slug",
                "published_at",
                "view_count",
            )
            if field in share
        }
        payload["share_path"] = (
            "/insight/copilot/shared?" + urlencode({"access": access})
        )
        return payload

    @action(detail=True, methods=["get", "post"], url_path="share")
    def share(self, request, pk=None):
        """Inspect or publish the latest completed Q&A through SourceLens."""

        from apps.lens_bridge.services import copilot_sharing

        link = self._get_user_link(pk)
        self._require_ready_session(link)
        try:
            if request.method == "GET":
                payload = copilot_sharing.get_share_candidate(link)
                if isinstance(payload.get("share"), dict):
                    payload["share"] = self._share_response(
                        link,
                        payload["share"],
                    )
                return Response(payload)
            body = LensShareTitleSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            share = copilot_sharing.create_share(
                link,
                title=body.validated_data["title"],
            )
            return Response(
                self._share_response(link, share),
                status=status.HTTP_201_CREATED,
            )
        except copilot_sharing.CopilotShareNotFoundError as exc:
            raise NotFound("No completed answer is available to share.") from exc
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"shares/(?P<share_uuid>[0-9a-fA-F-]+)",
    )
    def share_detail(self, request, pk=None, share_uuid=None):
        """Rename or revoke a SourceLens Q&A owned by this HFL Chat."""

        from apps.lens_bridge.services import copilot_sharing

        link = self._get_user_link(pk)
        self._require_ready_session(link)
        try:
            if request.method == "DELETE":
                copilot_sharing.revoke_share(link, str(share_uuid))
                return Response(status=status.HTTP_204_NO_CONTENT)
            body = LensShareTitleSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            share = copilot_sharing.update_share_title(
                link,
                str(share_uuid),
                title=body.validated_data["title"],
            )
            return Response(self._share_response(link, share))
        except (ValueError, copilot_sharing.CopilotShareNotFoundError) as exc:
            raise NotFound() from exc
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)

    def create(self, request):
        body = LensSessionCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            from apps.lens_bridge.services import chat_lifecycle

            link = chat_lifecycle.create_copilot_chat(
                self.org,
                user=request.user,
                backup_config_id=body.validated_data["backup_config_id"],
                backup_source_snapshot_id=body.validated_data[
                    "backup_source_snapshot_id"
                ],
                source_scopes=body.validated_data["source_scopes"],
                gateway_mode=body.validated_data["gateway_mode"],
                gateway_link_id=body.validated_data.get("gateway_link_id"),
                idempotency_key=body.validated_data["idempotency_key"],
                title=body.validated_data.get("title"),
                analysis_type=body.validated_data.get("analysis_type"),
                analysis_mode=body.validated_data.get("analysis_mode"),
                agent_model_ref=body.validated_data.get("agent_model_ref"),
            )
        except ValidationError:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(
            LensSessionLinkSerializer(link).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["patch"], url_path="model")
    def set_model(self, request, pk=None):
        link = self._get_user_link(pk)
        body = LensSessionUpdateSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        model_ref = body.validated_data.get("agent_model_ref")
        if model_ref is None:
            return Response(LensSessionLinkSerializer(link).data)
        org_models.validate_agent_model_ref(self.org, model_ref)
        ks = link.knowledge_source
        if ks is None:
            return Response(
                {"agent_model_ref": "Knowledge source is not ready."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        provisioning.sync_assistant_agent_model(
            ks=ks,
            model_ref=model_ref,
            assistant_uuid=link.sl_assistant_uuid,
        )
        link.agent_model_ref = model_ref
        link.save(update_fields=["agent_model_ref", "updated_at"])
        return Response(LensSessionLinkSerializer(link).data)

    @action(detail=True, methods=["patch"], url_path="execution")
    def set_execution(self, request, pk=None):
        """Update Chat-owned analysis mode and Agent model together."""

        link = self._get_user_link(pk)
        self._require_ready_session(link)
        body = LensSessionUpdateSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        values = body.validated_data
        model_ref = values.get("agent_model_ref")
        analysis_mode = values.get("analysis_mode")
        if model_ref is None and analysis_mode is None:
            return Response(LensSessionLinkSerializer(link).data)
        if model_ref is not None:
            org_models.validate_agent_model_ref(self.org, model_ref)
        ks = link.knowledge_source
        if ks is None or link.sl_assistant_uuid is None:
            return Response(
                {"execution": "Chat is not ready for execution settings."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            provisioning.sync_assistant_execution_config(
                ks=ks,
                model_ref=model_ref,
                analysis_mode=analysis_mode,
                assistant_uuid=link.sl_assistant_uuid,
            )
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        update_fields = ["updated_at"]
        if model_ref is not None:
            link.agent_model_ref = model_ref
            update_fields.append("agent_model_ref")
        if analysis_mode is not None:
            link.analysis_mode = analysis_mode
            update_fields.append("analysis_mode")
        link.save(update_fields=update_fields)
        return Response(LensSessionLinkSerializer(link).data)

    @action(detail=True, methods=["patch"], url_path="title")
    def set_title(self, request, pk=None):
        link = self._get_user_link(pk)
        body = LensSessionTitleSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        title = body.validated_data["title"]
        if link.sl_session_uuid:
            self._require_ready_session(link)
            sl_client.request_json(
                "PATCH",
                f"/api/lens/sessions/{link.sl_session_uuid}/",
                json_body={"title": title},
                hfl_user=request.user,
            )
        link.title = title
        link.save(update_fields=["title", "updated_at"])
        return Response(LensSessionLinkSerializer(link).data)

    @action(detail=True, methods=["post"], url_path="viewed")
    def mark_viewed(self, request, pk=None):
        from django.utils import timezone

        link = self._get_user_link(pk)
        link.last_viewed_at = timezone.now()
        link.save(update_fields=["last_viewed_at", "updated_at"])
        return Response(LensSessionLinkSerializer(link).data)

    @action(detail=True, methods=["post"], url_path="pin")
    def pin(self, request, pk=None):
        return self._set_pinned(request, pk, pinned=True)

    @action(detail=True, methods=["post"], url_path="unpin")
    def unpin(self, request, pk=None):
        return self._set_pinned(request, pk, pinned=False)

    def _set_pinned(self, request, pk, *, pinned: bool):
        link = self._get_user_link(pk)
        self._require_ready_session(link)
        action_name = "pin" if pinned else "unpin"
        data = sl_client.request_json(
            "POST",
            f"/api/lens/sessions/{link.sl_session_uuid}/{action_name}/",
            hfl_user=request.user,
        )
        if (
            not isinstance(data, dict)
            or "pinned_at" not in data
            or (
                pinned
                and (
                    not isinstance(data["pinned_at"], str)
                    or not data["pinned_at"]
                )
            )
            or (not pinned and data["pinned_at"] is not None)
        ):
            raise _source_lens_contract_error("session pin")
        payload = LensSessionLinkSerializer(link).data
        payload["pinned_at"] = data["pinned_at"]
        return Response(payload)

    def retrieve(self, request, pk=None):
        link = self._get_user_link(pk)
        return Response(LensSessionLinkSerializer(link).data)

    def destroy(self, request, pk=None):
        link = self._get_user_link(pk)
        from apps.lens_bridge.services import chat_lifecycle

        link = chat_lifecycle.request_copilot_chat_teardown(link)
        return Response(
            LensSessionLinkSerializer(link).data, status=status.HTTP_202_ACCEPTED
        )

    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        link = self._get_user_link(pk)
        from apps.lens_bridge.services import chat_lifecycle

        link = chat_lifecycle.retry_copilot_chat_provision(link)
        link.refresh_from_db()
        return Response(
            LensSessionLinkSerializer(link).data, status=status.HTTP_202_ACCEPTED
        )

    @action(detail=True, methods=["get"], url_path="messages")
    def messages(self, request, pk=None):
        link = self._get_user_link(pk)
        if (
            link.lifecycle_status != LensSessionLink.LifecycleStatus.READY
            or not link.sl_session_uuid
        ):
            return Response([])
        data = sl_client.request_json(
            "GET",
            f"/api/lens/sessions/{link.sl_session_uuid}/messages/",
            hfl_user=request.user,
        )
        return Response(_rewrite_attachment_urls(data, session_id=link.id))

    @action(
        detail=True,
        methods=["post"],
        url_path="attachments",
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_attachment(self, request, pk=None):
        link = self._get_user_link(pk)
        self._require_ready_session(link)
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            raise ValidationError({"file": "No file provided."})
        data = sl_client.request_multipart(
            f"/api/lens/sessions/{link.sl_session_uuid}/attachments/",
            uploaded_file=uploaded_file,
            hfl_user=request.user,
        )
        if not isinstance(data, dict):
            raise _source_lens_contract_error("attachment")
        attachment_uuid = _canonical_attachment_uuid(data.get("uuid"))
        if data.get("kind") not in {"image", "document"}:
            raise _source_lens_contract_error("attachment")
        data["uuid"] = attachment_uuid
        data["url"] = _attachment_proxy_url(link.id, attachment_uuid)
        return Response(data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["get", "delete"],
        url_path=r"attachments/(?P<attachment_uuid>[0-9a-fA-F-]+)",
    )
    def attachment_content(
        self,
        request,
        pk=None,
        attachment_uuid=None,
    ):
        link = self._get_user_link(pk)
        self._require_ready_session(link)
        try:
            attachment_id = uuid.UUID(str(attachment_uuid))
        except (TypeError, ValueError) as exc:
            raise NotFound() from exc
        _require_attachment_proxy_token(
            request,
            session_id=link.id,
            attachment_uuid=attachment_id,
        )
        path = f"/api/lens/attachments/{attachment_id}/"
        if request.method == "DELETE":
            sl_client.request_json(
                "DELETE",
                path,
                hfl_user=request.user,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        upstream = sl_client.stream_binary(path, hfl_user=request.user)
        try:
            response = StreamingHttpResponse(
                upstream.body,
                content_type=upstream.content_type,
            )
            if upstream.content_length:
                response["Content-Length"] = upstream.content_length
            if upstream.content_disposition:
                response["Content-Disposition"] = upstream.content_disposition
            response["Cache-Control"] = upstream.cache_control
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception:
            upstream.body.close()
            raise

    @action(
        detail=True,
        methods=["get"],
        url_path=r"output-files/(?P<file_uuid>[0-9a-fA-F-]+)",
    )
    def output_file_content(
        self,
        request,
        pk=None,
        file_uuid=None,
    ):
        link = self._get_user_link(pk)
        self._require_ready_session(link)
        try:
            output_file_id = uuid.UUID(str(file_uuid))
        except (TypeError, ValueError) as exc:
            raise NotFound() from exc
        _require_output_file_proxy_token(
            request,
            session_id=link.id,
            file_uuid=output_file_id,
        )
        upstream = sl_client.stream_binary(
            f"/api/lens/output-files/{output_file_id}/",
            hfl_user=request.user,
        )
        try:
            response = StreamingHttpResponse(
                upstream.body,
                content_type=upstream.content_type,
            )
            if upstream.content_length:
                response["Content-Length"] = upstream.content_length
            if upstream.content_disposition:
                response["Content-Disposition"] = upstream.content_disposition
            response["Cache-Control"] = upstream.cache_control
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception:
            upstream.body.close()
            raise

    @action(detail=True, methods=["post"], url_path="runs")
    def create_run(self, request, pk=None):
        from apps.lens_bridge.services.maintenance import (
            sourcelens_maintenance_active,
            sourcelens_run_creation_guard,
        )

        link = self._get_user_link(pk)
        self._require_ready_session(link)
        body = LensRunCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        question = body.validated_data.get("question") or ""
        retry_of_run_uuid = body.validated_data.get("retry_of_run_uuid")
        attachment_uuids = [
            str(value) for value in body.validated_data.get("attachment_uuids", [])
        ]
        idempotency_key = body.validated_data.get("idempotency_key") or uuid.uuid4().hex
        from apps.lens_bridge.services import run_submissions

        if retry_of_run_uuid is not None:
            copilot_service.require_assistant_run(link, retry_of_run_uuid)

        with sourcelens_run_creation_guard():
            if sourcelens_maintenance_active():
                raise SourceLensMaintenanceUnavailable()
            try:
                submission, existing_run = run_submissions.prepare_submission(
                    link,
                    question=question,
                    idempotency_key=idempotency_key,
                    attachment_uuids=attachment_uuids,
                    retry_of_run_uuid=retry_of_run_uuid,
                )
            except run_submissions.RunSubmissionConflictError as exc:
                raise CopilotRunConflict() from exc
        if existing_run is not None:
            return Response(existing_run, status=status.HTTP_201_CREATED)

        try:
            with sourcelens_run_creation_guard():
                if sourcelens_maintenance_active():
                    raise SourceLensMaintenanceUnavailable()
                data = run_submissions.execute_submission(submission.id)
        except SourceLensMaintenanceUnavailable:
            raise
        except run_submissions.RunSubmissionContractError as exc:
            run_submissions.record_submission_error(
                submission.id,
                exc,
                retryable=False,
            )
            raise
        except sl_client.LensBridgeError as exc:
            retryable = exc.status_code >= 500 or exc.status_code in {
                408,
                409,
                425,
                429,
            }
            run_submissions.record_submission_error(
                submission.id,
                exc,
                retryable=retryable,
            )
            raise
        except (
            run_submissions.RunSubmissionConflictError,
            run_submissions.RunSubmissionInvalidError,
        ) as exc:
            run_submissions.record_submission_error(
                submission.id,
                exc,
                retryable=False,
            )
            raise CopilotRunConflict() from exc
        return Response(data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"runs/(?P<run_uuid>[0-9a-fA-F-]+)/pdf",
    )
    def run_pdf(self, request, pk=None, run_uuid=None):
        """Stream SourceLens' PDF for an answer owned by this HFL chat."""

        link = self._get_user_link(pk)
        self._require_ready_session(link)
        try:
            answer_run_uuid = uuid.UUID(str(run_uuid))
        except (TypeError, ValueError) as exc:
            raise NotFound() from exc
        copilot_service.require_assistant_run(link, answer_run_uuid)
        upstream = sl_client.stream_binary(
            f"/api/lens/runs/{answer_run_uuid}/pdf/",
            hfl_user=request.user,
        )
        try:
            response = StreamingHttpResponse(
                upstream.body,
                content_type=upstream.content_type or "application/pdf",
            )
            if upstream.content_length:
                response["Content-Length"] = upstream.content_length
            if upstream.content_disposition:
                response["Content-Disposition"] = upstream.content_disposition
            response["Cache-Control"] = "private, max-age=0, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception:
            upstream.body.close()
            raise

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"runs/(?P<run_uuid>[0-9a-fA-F-]+)/feedback",
    )
    def feedback(self, request, pk=None, run_uuid=None):
        """Persist answer feedback through the owning SourceLens Run."""

        link = self._get_user_link(pk)
        self._require_ready_session(link)
        body = LensRunFeedbackSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            data = copilot_service.update_run_feedback(
                link,
                uuid.UUID(str(run_uuid)),
                body.validated_data["feedback"],
            )
        except (TypeError, ValueError) as exc:
            raise NotFound() from exc
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)

    @action(detail=True, methods=["get"], url_path="sync")
    def sync(self, request, pk=None):
        link = self._get_user_link(pk)
        lifecycle_error = classify_chat_lifecycle_error(
            link.lifecycle_error,
            link.lifecycle_error_state_json,
        )
        if (
            link.lifecycle_status != LensSessionLink.LifecycleStatus.READY
            or not link.sl_session_uuid
        ):
            return Response(
                {
                    "session_id": link.id,
                    "messages": [],
                    "active_run": None,
                    "response_state": {"status": "idle", "started_at": None},
                    "run_outcomes": [],
                    "lifecycle_status": link.lifecycle_status,
                    "lifecycle_error": (
                        lifecycle_error.message if link.lifecycle_error else ""
                    ),
                    "lifecycle_error_code": (
                        lifecycle_error.code if link.lifecycle_error else ""
                    ),
                    "lifecycle_error_message": (
                        lifecycle_error.message if link.lifecycle_error else ""
                    ),
                    "lifecycle_error_retryable": (
                        lifecycle_error.retryable if link.lifecycle_error else False
                    ),
                    "lifecycle_error_meta": (
                        lifecycle_error.meta if link.lifecycle_error else {}
                    ),
                }
            )
        try:
            data = copilot_service.sync_copilot_session(link)
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        data["lifecycle_status"] = link.lifecycle_status
        data["lifecycle_error"] = (
            lifecycle_error.message if link.lifecycle_error else ""
        )
        data["lifecycle_error_code"] = (
            lifecycle_error.code if link.lifecycle_error else ""
        )
        data["lifecycle_error_message"] = (
            lifecycle_error.message if link.lifecycle_error else ""
        )
        data["lifecycle_error_retryable"] = (
            lifecycle_error.retryable if link.lifecycle_error else False
        )
        data["lifecycle_error_meta"] = (
            lifecycle_error.meta if link.lifecycle_error else {}
        )
        data["last_assistant_message_at"] = link.last_assistant_message_at
        data["has_unread"] = bool(
            link.last_assistant_message_at
            and (
                link.last_viewed_at is None
                or link.last_assistant_message_at > link.last_viewed_at
            )
        )
        _rewrite_attachment_urls(data.get("messages"), session_id=link.id)
        return Response(data)

    @action(detail=True, methods=["get"], url_path="active-run")
    def active_run(self, request, pk=None):
        link = self._get_user_link(pk)
        try:
            payload = copilot_service.get_active_run_payload(link)
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response({"active_run": payload})

    @action(
        detail=True,
        methods=["post"],
        url_path=r"runs/(?P<run_uuid>[^/.]+)/cancel",
    )
    def cancel_run(self, request, pk=None, run_uuid=None):
        link = self._get_user_link(pk)
        try:
            data = copilot_service.cancel_copilot_run(link, uuid.UUID(str(run_uuid)))
        except ValidationError:
            raise
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        return Response(data)

    def _get_user_link(self, pk) -> LensSessionLink:
        link = self._user_sessions().filter(pk=pk).first()
        if link is None:
            from rest_framework.exceptions import NotFound

            raise NotFound()
        return link

    def _require_ready_session(self, link: LensSessionLink) -> None:
        if (
            link.lifecycle_status != LensSessionLink.LifecycleStatus.READY
            or not link.sl_session_uuid
        ):
            lifecycle_error = classify_chat_lifecycle_error(
                link.lifecycle_error,
                link.lifecycle_error_state_json,
            )
            raise ValidationError(
                {
                    "lifecycle_status": (
                        lifecycle_error.message
                        if link.lifecycle_error
                        else "Chat is still preparing. Please wait until provisioning finishes."
                    )
                }
            )


class LensCopilotSharedQAView(OrgScopedMixin, APIView):
    """Read a SourceLens-owned share through HFL organization authorization."""

    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request):
        from apps.lens_bridge.services import copilot_sharing

        raw_access = str(request.query_params.get("access") or "")
        try:
            _link, access = copilot_sharing.require_active_share_access(
                organization_id=self.org.id,
                raw_token=raw_access,
            )
            payload = sl_client.request_json(
                "GET",
                f"/api/lens/public/qa/{access['share_token']}/",
            )
        except copilot_sharing.CopilotShareNotFoundError as exc:
            raise NotFound() from exc
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        if not isinstance(payload, dict) or str(payload.get("token") or "") != access[
            "share_token"
        ]:
            return _lens_error_response(
                _source_lens_contract_error("shared Q&A")
            )
        result = {
            field: payload[field]
            for field in (
                "title",
                "question",
                "answer",
                "assistant_name",
                "assistant_slug",
                "view_count",
                "published_at",
            )
            if field in payload
        }
        for field in ("input_attachments", "output_files"):
            files = payload.get(field)
            if not isinstance(files, list):
                result[field] = []
                continue
            rewritten = []
            for row in files:
                if not isinstance(row, dict):
                    continue
                try:
                    file_uuid = uuid.UUID(str(row.get("uuid")))
                except (TypeError, ValueError, AttributeError):
                    continue
                item = {
                    key: row[key]
                    for key in (
                        "uuid",
                        "filename",
                        "content_type",
                        "byte_size",
                        "order",
                    )
                    if key in row
                }
                item["url"] = (
                    reverse(
                        "lens-copilot-shared-qa-file",
                        kwargs={"file_uuid": file_uuid},
                    )
                    + "?"
                    + urlencode({"access": raw_access})
                )
                rewritten.append(item)
            result[field] = rewritten
        result["pdf_url"] = (
            reverse("lens-copilot-shared-qa-pdf")
            + "?"
            + urlencode({"access": raw_access})
        )
        return Response(result)


class _LensCopilotSharedQABinaryView(OrgScopedMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]
    upstream_suffix = ""

    def _upstream_path(self, share_token: str, **kwargs) -> str:
        return f"/api/lens/public/qa/{share_token}/{self.upstream_suffix}"

    def get(self, request, **kwargs):
        from apps.lens_bridge.services import copilot_sharing

        raw_access = str(request.query_params.get("access") or "")
        try:
            _link, access = copilot_sharing.require_active_share_access(
                organization_id=self.org.id,
                raw_token=raw_access,
            )
            upstream = sl_client.stream_binary(
                self._upstream_path(access["share_token"], **kwargs),
            )
        except copilot_sharing.CopilotShareNotFoundError as exc:
            raise NotFound() from exc
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)
        try:
            response = StreamingHttpResponse(
                upstream.body,
                content_type=upstream.content_type or "application/octet-stream",
            )
            if upstream.content_length:
                response["Content-Length"] = upstream.content_length
            if upstream.content_disposition:
                response["Content-Disposition"] = upstream.content_disposition
            response["Cache-Control"] = "private, max-age=0, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception:
            upstream.body.close()
            raise


class LensCopilotSharedQAFileView(_LensCopilotSharedQABinaryView):
    def _upstream_path(self, share_token: str, **kwargs) -> str:
        return (
            f"/api/lens/public/qa/{share_token}/files/"
            f"{kwargs['file_uuid']}/"
        )


class LensCopilotSharedQAPdfView(_LensCopilotSharedQABinaryView):
    upstream_suffix = "pdf/"


class LensCopilotUsageView(OrgScopedMixin, APIView):
    """Current user's HFL-contextualized SourceLens usage and Q&A costs."""

    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request, run_uuid=None):
        try:
            if run_uuid is not None:
                return Response(usage.usage_detail(self.org, request.user, run_uuid))
            return Response(
                usage.usage_overview(self.org, request.user, request.query_params)
            )
        except sl_client.LensBridgeError as exc:
            return _lens_error_response(exc)


class LensCopilotRunStreamView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]
    renderer_classes = [ServerSentEventsRenderer]

    def get(self, request, run_uuid, session_id=None):
        from apps.iam.org_context import require_org
        from rest_framework.exceptions import NotFound

        org = require_org(request)
        qs = LensSessionLink.objects.filter(
            organization=org,
            hfl_user=request.user,
            status=LensSessionLink.Status.ACTIVE,
            active_run_uuid=run_uuid,
        )
        if session_id is not None:
            link = qs.filter(pk=session_id).first()
        else:
            link = qs.first()
        if link is None:
            raise NotFound()

        stream = sl_client.stream_sse(
            f"/api/lens/runs/{run_uuid}/stream/",
            hfl_user=request.user,
        )
        response = StreamingHttpResponse(
            stream, content_type="text/event-stream; charset=utf-8"
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        response["Connection"] = "keep-alive"
        return response
