from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator, IsOrgStaffReader
from apps.source.constants import PipelineStep
from common.errors import AppError
from apps.source.services.internal.backup_source_directory import (
    BackupSourceDirectoryError,
    BackupSourceDirectoryForbidden,
    BackupSourceDirectoryInvalid,
    BackupSourceDirectoryNotFound,
    BackupSourceDirectoryTimeout,
    DEFAULT_DIRECTORY_LIMIT,
    get_backup_source_path_info,
    list_backup_source_directories,
)
from apps.source.services.internal.backup_selectable import (
    fetch_backup_selectable_by_ids,
    list_backup_selectable_sources,
)
from apps.source.services.internal.backup_source_delete import (
    BackupSourceDeleteFailed,
    preflight_delete_backup_sources,
    queue_delete_backup_sources,
)
from apps.source.services.internal.backup_source_revert import (
    preflight_revert_backup_sources,
    revert_backup_sources,
)
from apps.source.services.internal.source_pipeline import (
    set_pipeline_steps,
)


def _query_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _optional_query_bool(params, name: str) -> bool | None:
    if name not in params or str(params.get(name) or "").strip() == "":
        return None
    value = str(params.get(name) or "").strip().lower()
    if value not in {"1", "true", "yes", "y", "on", "0", "false", "no", "n", "off"}:
        raise ValidationError({name: "Must be a boolean value."})
    return _query_bool(value)


def _optional_positive_int(params, name: str) -> int | None:
    value = str(params.get(name) or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValidationError({name: "Must be a positive integer."}) from exc
    if parsed <= 0:
        raise ValidationError({name: "Must be a positive integer."})
    return parsed


def _optional_choice(params, name: str, choices: set[str]) -> str | None:
    value = str(params.get(name) or "").strip().lower()
    if not value:
        return None
    if value not in choices:
        raise ValidationError({name: f"Must be one of: {', '.join(sorted(choices))}."})
    return value


def _optional_text(params, name: str, *, max_length: int = 255) -> str | None:
    value = str(params.get(name) or "").strip()
    if not value:
        return None
    if len(value) > max_length:
        raise ValidationError({name: f"Must be at most {max_length} characters."})
    return value


class BackupSelectableListView(APIView):
    """Unified paginated Agent/NAS catalog for Backup Wizard source steps."""

    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        org = require_org(request)
        ids_param = (request.query_params.get("ids") or "").strip()
        expand = (request.query_params.get("expand") or "").strip() or None
        if ids_param:
            ids = [value.strip() for value in ids_param.split(",") if value.strip()]
            results = fetch_backup_selectable_by_ids(organization_id=org.id, ids=ids, expand=expand)
            return Response({"count": len(results), "results": results})

        page_raw = (request.query_params.get("page") or "1").strip()
        page_size_raw = (request.query_params.get("page_size") or "10").strip()
        search = _optional_text(request.query_params, "search")
        search_field = _optional_choice(
            request.query_params,
            "search_field",
            {"source_name", "source_hostname", "source_ip"},
        )
        source_name = _optional_text(request.query_params, "source_name")
        source_hostname = _optional_text(request.query_params, "source_hostname")
        source_ip = _optional_text(request.query_params, "source_ip", max_length=64)
        status_filter = _optional_text(request.query_params, "status", max_length=32)
        source_status = _optional_choice(
            request.query_params,
            "source_status",
            {"active", "error", "inactive", "offline", "online", "reconnecting", "remove_failed", "removing"},
        )
        availability = _optional_choice(request.query_params, "availability", {"online", "offline"})
        running_task = _optional_choice(request.query_params, "running_task", {"backup", "restore"})
        backup_running = _optional_query_bool(request.query_params, "backup_running")
        restore_running = _optional_query_bool(request.query_params, "restore_running")
        backup_task_status = _optional_choice(request.query_params, "backup_task_status", {"success", "failed", "running", "none"})
        restore_task_status = _optional_choice(request.query_params, "restore_task_status", {"success", "failed", "running", "none"})
        backup_policy_id = _optional_positive_int(request.query_params, "backup_policy_id")
        file_filter_rule_id = _optional_positive_int(request.query_params, "file_filter_rule_id")
        repository_id = _optional_positive_int(request.query_params, "repository_id")
        source_type = _optional_choice(request.query_params, "type", {"host", "nas"})
        exclude_param = (request.query_params.get("exclude") or "").strip()
        exclude_ids = [value.strip() for value in exclude_param.split(",") if value.strip()]
        step_raw = (request.query_params.get("step") or "").strip()
        pipeline_step: int | None = None
        if step_raw:
            try:
                parsed_step = int(step_raw)
            except ValueError as exc:
                raise ValidationError({"step": "Must be 1, 2, or 3."}) from exc
            if parsed_step not in PipelineStep.VALID:
                raise ValidationError({"step": "Must be 1, 2, or 3."})
            pipeline_step = parsed_step

        try:
            page = max(1, int(page_raw))
        except ValueError:
            page = 1
        try:
            page_size = max(1, min(int(page_size_raw), 100))
        except ValueError:
            page_size = 10

        results, total = list_backup_selectable_sources(
            organization_id=org.id,
            page=page,
            page_size=page_size,
            search=search,
            search_field=search_field,
            source_name=source_name,
            source_hostname=source_hostname,
            source_ip=source_ip,
            status=status_filter,
            source_status=source_status,
            availability=availability,
            source_type=source_type,
            exclude_ids=exclude_ids if pipeline_step is None else None,
            pipeline_step=pipeline_step,
            running_task=running_task,
            backup_running=backup_running,
            restore_running=restore_running,
            backup_task_status=backup_task_status,
            restore_task_status=restore_task_status,
            backup_policy_id=backup_policy_id,
            file_filter_rule_id=file_filter_rule_id,
            repository_id=repository_id,
            expand=expand,
        )
        return Response({"page": page, "page_size": page_size, "count": total, "results": results})


class BackupSelectablePipelineView(APIView):
    """Advance real backup-selectable sources through protection wizard steps."""

    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        ids_raw = request.data.get("ids")
        if not isinstance(ids_raw, list):
            return Response({"detail": "ids must be a list of agent:/nas: keys."}, status=status.HTTP_400_BAD_REQUEST)

        ids = [str(value).strip() for value in ids_raw if str(value).strip()]
        if not ids:
            return Response({"detail": "ids must not be empty."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            step = int(request.data.get("step"))
        except (TypeError, ValueError):
            return Response({"detail": "step must be 1, 2, or 3."}, status=status.HTTP_400_BAD_REQUEST)
        if step not in PipelineStep.VALID:
            return Response({"detail": "step must be 1, 2, or 3."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated = set_pipeline_steps(organization_id=org.id, ids=ids, step=step)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"updated": updated, "step": step})


class BackupSelectablePipelineRevertView(APIView):
    """Revert real backup-selectable sources to an earlier protection wizard step."""

    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        ids_raw = request.data.get("ids")
        if not isinstance(ids_raw, list):
            return Response({"detail": "ids must be a list of agent:/nas: keys."}, status=status.HTTP_400_BAD_REQUEST)

        ids = [str(value).strip() for value in ids_raw if str(value).strip()]
        if not ids:
            return Response({"detail": "ids must not be empty."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target_step = int(request.data.get("target_step"))
        except (TypeError, ValueError):
            return Response({"detail": "target_step must be 1 or 2."}, status=status.HTTP_400_BAD_REQUEST)
        if target_step not in (PipelineStep.SOURCE_POOL, PipelineStep.CONFIG):
            return Response({"detail": "target_step must be 1 or 2."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            force = _query_bool(request.data.get("force"))
            updated = revert_backup_sources(
                org=org,
                ids=ids,
                target_step=target_step,
                force=force,
                user=request.user,
            )
        except BackupSourceDeleteFailed as exc:
            return Response(
                {
                    "detail": exc.message,
                    "reasons": [reason.as_dict() for reason in exc.reasons],
                    "hint": exc.hint,
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(updated)


class BackupSelectableDeletePreflightView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        ids = _parse_id_list(request.data.get("ids"))
        if ids is None:
            return Response({"detail": "ids must be a list of agent:/nas: keys."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            preflight_delete_backup_sources(
                organization_id=org.id,
                ids=ids,
                force=_query_bool(request.data.get("force")),
            )
        )


class BackupSelectableBulkDeleteView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        ids = _parse_id_list(request.data.get("ids"))
        if ids is None:
            return Response({"detail": "ids must be a list of agent:/nas: keys."}, status=status.HTTP_400_BAD_REQUEST)
        force = _query_bool(request.data.get("force"))
        confirmation_keyword = "FORCE DEREGISTER" if force else "DEREGISTER"
        if request.data.get("confirmation") != confirmation_keyword:
            return Response(
                {
                    "confirmation": (
                        f"Type {confirmation_keyword} exactly to confirm deregistration."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = queue_delete_backup_sources(
                org=org,
                ids=ids,
                force=force,
                user=request.user,
                idempotency_key=str(
                    request.headers.get("Idempotency-Key")
                    or request.data.get("idempotency_key")
                    or ""
                )[:96],
            )
        except BackupSourceDeleteFailed as exc:
            return Response(
                {
                    "detail": exc.message,
                    "reasons": [reason.as_dict() for reason in exc.reasons],
                    "hint": exc.hint,
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(result, status=status.HTTP_202_ACCEPTED)


class BackupSelectableRevertPreflightView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        ids = _parse_id_list(request.data.get("ids"))
        if ids is None:
            return Response({"detail": "ids must be a list of agent:/nas: keys."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target_step = int(request.data.get("target_step"))
        except (TypeError, ValueError):
            return Response({"detail": "target_step must be 1 or 2."}, status=status.HTTP_400_BAD_REQUEST)
        if target_step not in (PipelineStep.SOURCE_POOL, PipelineStep.CONFIG):
            return Response({"detail": "target_step must be 1 or 2."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            preflight_revert_backup_sources(
                organization_id=org.id,
                ids=ids,
                target_step=target_step,
            )
        )


def _parse_id_list(raw) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    ids = [str(value).strip() for value in raw if str(value).strip()]
    return ids if ids else None


class BackupSelectableDirectoryView(APIView):
    """Browse a backup-selectable source directory through the Agent/Proxy WSS task channel."""

    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        org = require_org(request)
        source_id = (request.query_params.get("source_id") or request.query_params.get("id") or "").strip()
        if not source_id:
            return Response({"detail": "source_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        path = (request.query_params.get("path") or "").strip()
        try:
            timeout = int(request.query_params.get("timeout", "30"))
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(3, min(timeout, 60))

        try:
            limit = int(request.query_params.get("limit", str(DEFAULT_DIRECTORY_LIMIT)))
        except (TypeError, ValueError):
            limit = DEFAULT_DIRECTORY_LIMIT
        limit = max(1, min(limit, 1000))
        cursor = str(request.query_params.get("cursor") or "").strip()
        include_metadata_param = request.query_params.get("include_metadata")
        include_metadata = None if include_metadata_param is None else _query_bool(include_metadata_param)

        try:
            result = list_backup_source_directories(
                organization_id=org.id,
                source_id=source_id,
                path=path,
                wait_timeout_seconds=timeout,
                limit=limit,
                include_files=_query_bool(request.query_params.get("include_files")),
                include_metadata=include_metadata,
                cursor=cursor,
            )
        except BackupSourceDirectoryNotFound as exc:
            raise AppError(
                code="RESOURCE.NOT_FOUND",
                status=status.HTTP_404_NOT_FOUND,
                diagnostic=str(exc),
            ) from exc
        except BackupSourceDirectoryForbidden as exc:
            raise AppError(
                code="AUTH.FORBIDDEN",
                status=status.HTTP_403_FORBIDDEN,
                diagnostic=str(exc),
            ) from exc
        except BackupSourceDirectoryInvalid as exc:
            raise ValidationError({"source_id": str(exc)}) from exc
        except BackupSourceDirectoryTimeout as exc:
            raise AppError(
                code="AGENT.TIMEOUT",
                status=status.HTTP_504_GATEWAY_TIMEOUT,
                retryable=True,
                diagnostic=str(exc),
                meta={"source_id": source_id},
            ) from exc
        except BackupSourceDirectoryError as exc:
            raise AppError(
                code="AGENT.EXPLORER_LIST_FAILED",
                status=status.HTTP_502_BAD_GATEWAY,
                retryable=True,
                diagnostic=str(exc),
                meta={"source_id": source_id},
            ) from exc

        return Response(result)


class BackupSelectablePathInfoView(APIView):
    """Validate a manually entered backup source path and identify file/folder type."""

    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        org = require_org(request)
        source_id = (request.query_params.get("source_id") or request.query_params.get("id") or "").strip()
        if not source_id:
            return Response({"detail": "source_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        path = (request.query_params.get("path") or "").strip()
        if not path:
            return Response({"detail": "path is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            timeout = int(request.query_params.get("timeout", "30"))
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(3, min(timeout, 60))
        include_metadata_param = request.query_params.get("include_metadata")
        include_metadata = None if include_metadata_param is None else _query_bool(include_metadata_param)

        try:
            result = get_backup_source_path_info(
                organization_id=org.id,
                source_id=source_id,
                path=path,
                wait_timeout_seconds=timeout,
                include_metadata=include_metadata,
            )
        except BackupSourceDirectoryNotFound as exc:
            raise AppError(
                code="RESOURCE.NOT_FOUND",
                status=status.HTTP_404_NOT_FOUND,
                diagnostic=str(exc),
            ) from exc
        except BackupSourceDirectoryForbidden as exc:
            raise AppError(
                code="AUTH.FORBIDDEN",
                status=status.HTTP_403_FORBIDDEN,
                diagnostic=str(exc),
            ) from exc
        except BackupSourceDirectoryInvalid as exc:
            raise ValidationError({"source_id": str(exc)}) from exc
        except BackupSourceDirectoryTimeout as exc:
            raise AppError(
                code="AGENT.TIMEOUT",
                status=status.HTTP_504_GATEWAY_TIMEOUT,
                retryable=True,
                diagnostic=str(exc),
                meta={"source_id": source_id, "path": path},
            ) from exc
        except BackupSourceDirectoryError as exc:
            raise AppError(
                code="AGENT.PATH_VALIDATE_FAILED",
                status=status.HTTP_502_BAD_GATEWAY,
                retryable=True,
                diagnostic=str(exc),
                meta={"source_id": source_id, "path": path},
            ) from exc

        return Response(result)
