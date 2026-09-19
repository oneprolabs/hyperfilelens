from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.resource_access import assert_resource_access
from apps.iam.permissions_org import IsOrgReader
from apps.protection.services.snapshot_browser import (
    SnapshotBrowserError,
    SnapshotBrowserForbidden,
)
from apps.restore.services.snapshot_browser import (
    browse_snapshot_directory_from_target,
    get_snapshot_path_info_from_target,
)
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory


def _int_query(value: str | None, default: int, *, min_value: int, max_value: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"limit": "Must be an integer."}) from exc
    return max(min_value, min(max_value, parsed))


def _required_int_query(value: str | None, field_name: str) -> int:
    if value in (None, ""):
        raise ValidationError({field_name: "This query parameter is required."})
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Must be an integer."}) from exc
    if parsed <= 0:
        raise ValidationError({field_name: "Must be a positive integer."})
    return parsed


def _assert_directory_access(request, directory_id: int) -> None:
    directory = BackupSourceSnapshotDirectory.objects.filter(
        pk=directory_id,
    ).values("source_snapshot_id").first()
    if directory is None:
        raise NotFound("snapshot directory not found")
    config_id = BackupSourceSnapshot.objects.filter(
        pk=directory["source_snapshot_id"],
    ).values_list("backup_config_id", flat=True).first()
    if config_id:
        assert_resource_access(request, "backup_config", config_id)


class RestoreSnapshotDirectoryBrowseView(APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request, directory_id: int):
        org = require_org(request)
        _assert_directory_access(request, int(directory_id))
        try:
            data = browse_snapshot_directory_from_target(
                organization_id=org.id,
                directory_id=int(directory_id),
                target_node_id=_required_int_query(
                    request.query_params.get("target_node_id"),
                    "target_node_id",
                ),
                path=request.query_params.get("path") or "",
                limit=_int_query(
                    request.query_params.get("limit"),
                    200,
                    min_value=1,
                    max_value=1000,
                ),
            )
        except SnapshotBrowserForbidden as exc:
            raise PermissionDenied(str(exc)) from exc
        except SnapshotBrowserError as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise NotFound(message) from exc
            raise ValidationError({"detail": message}) from exc
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise ValidationError(exc.message_dict) from exc
            raise ValidationError({"detail": exc.messages}) from exc
        return Response(data)


class RestoreSnapshotDirectoryPathInfoView(APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request, directory_id: int):
        org = require_org(request)
        _assert_directory_access(request, int(directory_id))
        try:
            data = get_snapshot_path_info_from_target(
                organization_id=org.id,
                directory_id=int(directory_id),
                target_node_id=_required_int_query(
                    request.query_params.get("target_node_id"),
                    "target_node_id",
                ),
                path=request.query_params.get("path") or "",
            )
        except SnapshotBrowserForbidden as exc:
            raise PermissionDenied(str(exc)) from exc
        except SnapshotBrowserError as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise NotFound(message) from exc
            raise ValidationError({"detail": message}) from exc
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise ValidationError(exc.message_dict) from exc
            raise ValidationError({"detail": exc.messages}) from exc
        return Response(data)
