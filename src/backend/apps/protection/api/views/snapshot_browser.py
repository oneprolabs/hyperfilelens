from __future__ import annotations

from urllib.parse import quote, unquote, urlencode
from pathlib import Path

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator, IsOrgReader
from apps.protection.services.snapshot_browser import (
    SnapshotBrowserError,
    SnapshotBrowserForbidden,
    SnapshotMultiDownloadUnsupported,
    browse_snapshot_directory,
    download_snapshot_file,
)
from common.errors import AppError
from apps.protection.services.snapshot_download import (
    create_snapshot_group_download_task,
    create_snapshot_batch_download_task,
    create_snapshot_download_task,
    create_snapshot_artifact_file_token,
    accept_snapshot_artifact_upload,
    delete_downloaded_snapshot_artifact,
    get_snapshot_download_artifact,
    SnapshotArtifactUploadError,
    SnapshotDownloadSizeLimitExceeded,
    validate_snapshot_artifact_file_token,
)
from apps.protection.models import SnapshotDownloadArtifact
from apps.task.api.serializers import TaskSerializer


def _int_query(value: str | None, default: int, *, min_value: int, max_value: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"limit": "Must be an integer."}) from exc
    return max(min_value, min(max_value, parsed))


def _cursor_query(value: str | None) -> str:
    cursor = str(value or "").strip()
    if not cursor:
        return ""
    try:
        offset = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"cursor": "Must be a non-negative integer."}) from exc
    if offset < 0:
        raise ValidationError({"cursor": "Must be a non-negative integer."})
    return str(offset)


class SnapshotDirectoryBrowseView(APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request, directory_id: int):
        org = require_org(request)
        try:
            data = browse_snapshot_directory(
                organization_id=org.id,
                directory_id=int(directory_id),
                path=request.query_params.get("path") or "",
                limit=_int_query(
                    request.query_params.get("limit"),
                    200,
                    min_value=1,
                    max_value=1000,
                ),
                cursor=_cursor_query(request.query_params.get("cursor")),
            )
        except SnapshotBrowserForbidden as exc:
            raise PermissionDenied(str(exc)) from exc
        except SnapshotBrowserError as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise NotFound(message) from exc
            raise ValidationError({"detail": message}) from exc
        return Response(data)


class SnapshotDirectoryDownloadView(APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request, directory_id: int):
        org = require_org(request)
        try:
            download = download_snapshot_file(
                organization_id=org.id,
                directory_id=int(directory_id),
                path=request.query_params.get("path") or "",
            )
        except SnapshotBrowserForbidden as exc:
            raise PermissionDenied(str(exc)) from exc
        except SnapshotBrowserError as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise NotFound(message) from exc
            raise ValidationError({"detail": message}) from exc

        response = HttpResponse(download.content, content_type=download.content_type)
        filename = quote(download.filename)
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
        response["Content-Length"] = str(len(download.content))
        return response


class SnapshotDirectoryDownloadTaskView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request, directory_id: int):
        org = require_org(request)
        try:
            task = create_snapshot_download_task(
                organization_id=org.id,
                directory_id=int(directory_id),
                path=str(request.data.get("path") or ""),
            )
        except SnapshotBrowserForbidden as exc:
            raise PermissionDenied(str(exc)) from exc
        except (SnapshotBrowserError, DjangoValidationError, ValueError) as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise NotFound(message) from exc
            raise ValidationError({"detail": message}) from exc
        except Exception as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(TaskSerializer(task).data, status=201)


class SnapshotDirectoryBatchDownloadTaskView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request, directory_id: int):
        org = require_org(request)
        paths = request.data.get("paths")
        if not isinstance(paths, list):
            raise ValidationError({"paths": "Must be a list."})
        try:
            task = create_snapshot_batch_download_task(
                organization_id=org.id,
                directory_id=int(directory_id),
                paths=[str(item or "") for item in paths],
            )
        except SnapshotBrowserForbidden as exc:
            raise PermissionDenied(str(exc)) from exc
        except (SnapshotBrowserError, ValueError) as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise NotFound(message) from exc
            raise ValidationError({"paths": message}) from exc
        except Exception as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(TaskSerializer(task).data, status=201)


class SnapshotGroupDownloadTaskView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request, snapshot_id: int):
        org = require_org(request)
        groups = request.data.get("groups")
        if not isinstance(groups, list):
            raise ValidationError({"groups": "Must be a list."})
        try:
            task = create_snapshot_group_download_task(
                organization_id=org.id,
                snapshot_id=int(snapshot_id),
                groups=groups,
            )
        except SnapshotBrowserForbidden as exc:
            raise PermissionDenied(str(exc)) from exc
        except SnapshotMultiDownloadUnsupported as exc:
            raise AppError(
                code="PROTECTION.SNAPSHOT_MULTI_DOWNLOAD_UPGRADE_REQUIRED",
                status=409,
                title="Cross-Source Path download requires a backup source upgrade.",
                diagnostic=str(exc),
            ) from exc
        except SnapshotDownloadSizeLimitExceeded as exc:
            raise AppError(
                code="PROTECTION.SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED",
                status=400,
                title="Snapshot download exceeds the size limit.",
                diagnostic=str(exc),
                meta={
                    "selected_size_bytes": exc.selected_size_bytes,
                    "max_size_bytes": exc.max_size_bytes,
                },
            ) from exc
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            raise ValidationError(detail) from exc
        except (SnapshotBrowserError, ValueError) as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise NotFound(message) from exc
            raise ValidationError({"groups": message}) from exc
        return Response(TaskSerializer(task).data, status=201)


class SnapshotDownloadArtifactFileView(APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request, artifact_id: int):
        org = require_org(request)
        token = str(request.query_params.get("token") or "").strip()
        if token:
            try:
                token_organization_id = validate_snapshot_artifact_file_token(
                    token=token,
                    artifact_id=int(artifact_id),
                    user_id=int(request.user.id),
                )
            except SnapshotArtifactUploadError as exc:
                raise PermissionDenied(str(exc)) from exc
            if token_organization_id != org.id:
                raise PermissionDenied("snapshot download link does not match the organization")
        artifact = get_snapshot_download_artifact(
            organization_id=org.id,
            artifact_id=int(artifact_id),
        )
        if artifact is None:
            raise NotFound("snapshot download artifact not found")
        if artifact.status != SnapshotDownloadArtifact.Status.READY:
            raise ValidationError({"detail": "snapshot download artifact is not available"})
        if artifact.expires_at <= timezone.now():
            raise ValidationError({"detail": "snapshot download artifact expired"})
        path = Path(artifact.storage_path)
        if not path.exists() or not path.is_file():
            raise NotFound("snapshot download file not found")
        response = FileResponse(open(path, "rb"), content_type=artifact.content_type)
        response._resource_closers.append(
            lambda: delete_downloaded_snapshot_artifact(
                organization_id=org.id,
                artifact_id=artifact.id,
            )
        )
        filename = quote(artifact.filename)
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
        response["Content-Length"] = str(artifact.size_bytes)
        return response


class SnapshotDownloadArtifactDownloadUrlView(APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request, artifact_id: int):
        org = require_org(request)
        artifact = get_snapshot_download_artifact(
            organization_id=org.id,
            artifact_id=int(artifact_id),
        )
        if artifact is None:
            raise NotFound("snapshot download artifact not found")
        if artifact.status != SnapshotDownloadArtifact.Status.READY or artifact.expires_at <= timezone.now():
            raise ValidationError({"detail": "snapshot download artifact is not available"})
        token = create_snapshot_artifact_file_token(
            artifact=artifact,
            user_id=int(request.user.id),
        )
        path = f"/api/v1/protection/snapshot-download-artifacts/{artifact.id}/file/"
        return Response({"url": f"{path}?{urlencode({'token': token, 'org': org.key})}"})


class SnapshotDownloadArtifactContentView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = []

    def put(self, request, artifact_id: int):
        authorization = str(request.META.get("HTTP_AUTHORIZATION") or "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise PermissionDenied("snapshot download upload token is required")
        try:
            artifact = accept_snapshot_artifact_upload(
                artifact_id=int(artifact_id),
                token=token.strip(),
                stream=request.stream,
                content_length=request.META.get("CONTENT_LENGTH"),
                content_type=request.content_type or "application/octet-stream",
                expected_sha256=request.META.get("HTTP_X_CONTENT_SHA256") or "",
                filename=unquote(request.META.get("HTTP_X_ARTIFACT_FILENAME") or ""),
            )
        except SnapshotArtifactUploadError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            {
                "artifact_id": artifact.id,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
                "status": artifact.status,
            }
        )
