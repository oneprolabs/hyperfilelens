from __future__ import annotations

from uuid import UUID
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import JsonResponse
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.permissions_org import (
    IsOrgOperator,
    IsOrgStaffReader,
    IsOrgWriter,
    get_membership,
)
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.node_registry import agent_connection_status
from apps.protection.models import BackupConfig
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.source.services.internal.nas_display import nas_mount_source_uri
from apps.storage.repositories.models import Repository
from apps.storage.repositories.models import RepositoryUsageShard
from apps.storage.repositories.serializers import (
    NASRepositoryRepairSerializer,
    RepositoryCleanupPreflightSerializer,
    RepositoryCleanupSerializer,
    RepositoryResidualReleaseSerializer,
    RepositorySerializer,
    RepositoryWriteSerializer,
)
from apps.audit.constants import AuditAction, AuditResourceType
from apps.audit.services.interface import write_audit_log_from_request
from apps.iam.models import Organization
from apps.storage.selectors.interface import (
    get_effective_storage_provider,
    list_repositories,
)
from apps.storage.services.interface import check_repository, retry_repository_create
from apps.storage.services.internal.nas_repair import (
    NASRepositoryBusyError,
    repair_nas_repository,
)
from apps.storage.services.internal.nas_repair import _UNSET as _UNSET_SENTINEL
from apps.storage.services.internal.nas_repository import (
    nas_agent_repository_subdir,
    nas_mount_point,
)
from apps.storage.services.internal.repository_usage import (
    enqueue_repository_usage_refresh,
)
from apps.storage.services.internal.repository_secrets import resolve_repository_secrets
from apps.storage.services.internal.repository_endpoints import (
    normalize_s3_endpoint_host,
    repository_control_endpoint,
)
from apps.storage.services.internal.repository_cleanup import (
    RepositoryCleanupBlocked,
    create_repository_cleanup_task,
    repository_cleanup_preflight,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
    validate_s3_connection,
    verify_s3_bucket_access,
)
from apps.storage.services.internal.repository_operations import (
    request_repository_operation_cancel,
)
from apps.storage.services.internal.repository_location import (
    release_repository_residual_locations,
)
from apps.storage.services.internal.s3_url_style import normalize_s3_url_style
from apps.storage.services.internal.s3_validation_errors import s3_validation_app_error
from apps.task.api.serializers.task import TaskSerializer
from apps.task.models import Task


class RepositoryTaskCancelView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request, task_uuid: UUID):
        membership = get_membership(request)
        if membership is None:
            raise PermissionDenied("organization membership required")
        data = request.data if isinstance(request.data, dict) else {}
        task, cancellation_pending = request_repository_operation_cancel(
            task_uuid=task_uuid,
            organization_id=membership.organization_id,
            reason=str(data.get("reason") or ""),
            requested_by_id=getattr(request.user, "id", None),
        )
        if cancellation_pending:
            from apps.storage.tasks import execute_repository_operation

            execute_repository_operation.apply_async(
                kwargs={"repository_task_id": task.repository_operation.id}
            )
        return Response(
            TaskSerializer(task).data,
            status=(
                status.HTTP_202_ACCEPTED if cancellation_pending else status.HTTP_200_OK
            ),
        )


def health(_request):
    return JsonResponse({"app": "storage", "status": "ok"})


def _bool_param(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_list_param(value) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    ids: list[int] = []
    for item in values:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in ids:
            ids.append(parsed)
    return ids


def _optional_int_param(value, field_name: str) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Must be an integer."}) from exc


def _positive_int_param(value, *, default: int, max_value: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(1, parsed)
    if max_value is not None:
        parsed = min(parsed, max_value)
    return parsed


def _iso(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _django_validation_detail(exc: DjangoValidationError) -> dict:
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    messages = list(getattr(exc, "messages", []) or [])
    return {"detail": "; ".join(messages) or str(exc)}


def _nas_protocol(config: dict) -> str:
    explicit = str(config.get("protocol") or "").strip().lower()
    if explicit in {"smb", "nfs"}:
        return explicit
    if config.get("share"):
        return "smb"
    return "nfs"


def _agent_platform(node: Node, metadata: dict | None = None) -> str:
    merged = metadata if isinstance(metadata, dict) else {}
    raw = (
        str(merged.get("os") or merged.get("platform") or node.os_name or "")
        .strip()
        .lower()
    )
    if "darwin" in raw or "mac" in raw:
        return "macos"
    if "windows" in raw or raw in {"win32", "win64"} or raw.startswith("win "):
        return "windows"
    return "linux"


def _agent_source_payload(node: Node | None, *, config: BackupConfig) -> dict:
    if node is None:
        return {
            "source_type": config.source_type,
            "source_ref_id": config.source_ref_id,
            "source_name": f"#{config.source_ref_id}",
            "source_kind": "host",
            "status": "offline",
            "availability": "offline",
            "platform": "",
            "hostname": "",
            "node_name": "",
            "node_ip": "",
            "protocol": "",
            "connection_uri": "",
            "bound_node_id": None,
            "mount_status": "",
            "mount_point": "",
            "registered_at": None,
        }
    inv = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = inv.get("inventory") if isinstance(inv.get("inventory"), dict) else {}
    merged = {**inv, **inventory}
    hostname = str(merged.get("hostname") or node.name or "").strip()
    status = agent_connection_status(node)
    return {
        "source_type": config.source_type,
        "source_ref_id": config.source_ref_id,
        "source_name": node.name,
        "source_kind": "host",
        "status": status if status in {"online", "reconnecting"} else "offline",
        "availability": str(node.availability or "offline"),
        "platform": _agent_platform(node, merged),
        "hostname": hostname,
        "node_name": hostname or node.name,
        "node_ip": str(node.ip_address or "").strip(),
        "protocol": "",
        "connection_uri": "",
        "bound_node_id": node.id,
        "mount_status": "",
        "mount_point": "",
        "registered_at": _iso(node.created_at),
    }


def _nas_source_payload(
    resource: SourceResource | None, *, config: BackupConfig
) -> dict:
    if resource is None:
        return {
            "source_type": config.source_type,
            "source_ref_id": config.source_ref_id,
            "source_name": f"#{config.source_ref_id}",
            "source_kind": "nas",
            "status": "offline",
            "availability": "offline",
            "platform": "",
            "hostname": "",
            "node_name": "",
            "node_ip": "",
            "protocol": "",
            "connection_uri": "",
            "bound_node_id": None,
            "mount_status": "",
            "mount_point": "",
            "registered_at": None,
        }
    cfg = resource.config if isinstance(resource.config, dict) else {}
    node = resource.bound_node
    if node is not None:
        node_status = agent_connection_status(node)
        source_status = (
            node_status if node_status in {"online", "reconnecting"} else "offline"
        )
    else:
        source_status = "online" if resource.status == "active" else "offline"
    return {
        "source_type": config.source_type,
        "source_ref_id": config.source_ref_id,
        "source_name": resource.name,
        "source_kind": "nas",
        "status": source_status,
        "availability": str(resource.availability or "offline"),
        "platform": "",
        "hostname": str(cfg.get("server") or "").strip(),
        "node_name": (node.name if node else "")
        or str(cfg.get("server") or "").strip(),
        "node_ip": str(node.ip_address or "").strip() if node else "",
        "protocol": _nas_protocol(cfg),
        "connection_uri": nas_mount_source_uri(
            resource_type=resource.resource_type, config=cfg
        ),
        "bound_node_id": node.id if node else None,
        "mount_status": resource.mount_status,
        "mount_point": resource.mount_point,
        "registered_at": _iso(resource.created_at),
    }


def _repository_nas_location(repository: Repository) -> str:
    config = repository.config if isinstance(repository.config, dict) else {}
    protocol = "nfs" if repository.nas_protocol == Repository.NasProtocol.NFS else "smb"
    server = (
        str(config.get("server_address") or config.get("nfs_host") or "")
        .strip()
        .strip("/")
    )
    share = (
        str(
            config.get("share_path")
            or config.get("nfs_export")
            or config.get("repo_dir")
            or ""
        )
        .strip()
        .strip("/")
    )
    if not server:
        return ""
    return f"{protocol}://{server}/{share}" if share else f"{protocol}://{server}"


def _repository_mount_point_for_source(
    *,
    repository: Repository,
    config: BackupConfig,
    source: dict,
    shard: RepositoryUsageShard | None = None,
) -> str:
    if repository.repo_type != Repository.Type.NAS:
        return ""
    if shard is not None:
        mount_point = str(shard.mount_point or "").strip()
        if mount_point:
            return mount_point
    if repository.bind_node_id:
        repo_config = repository.config if isinstance(repository.config, dict) else {}
        saved_path = str(repo_config.get("proxy_mount_path") or "").strip()
        return saved_path or nas_mount_point(
            repository, node_id=int(repository.bind_node_id)
        )
    return ""


def _associated_sources_payload(
    repository: Repository,
    *,
    page: int = 1,
    page_size: int = 10,
) -> tuple[int, list[dict]]:
    queryset = BackupConfig.objects.filter(
        organization_id=repository.organization_id,
        repository_id=repository.id,
    ).order_by("source_type", "source_ref_id", "id")
    total = queryset.count()
    offset = (page - 1) * page_size
    configs = list(queryset[offset : offset + page_size])
    agent_ids = [
        config.source_ref_id for config in configs if config.source_type == "agent"
    ]
    nas_ids = [
        config.source_ref_id for config in configs if config.source_type == "nas"
    ]
    agents = {
        node.id: node
        for node in Node.objects.filter(
            organization_id=repository.organization_id,
            role=NodeRole.AGENT,
            id__in=agent_ids,
            is_deleted=False,
        )
    }
    nas_sources = {
        resource.id: resource
        for resource in SourceResource.objects.filter(
            organization_id=repository.organization_id,
            resource_type=ResourceType.NAS,
            id__in=nas_ids,
            is_deleted=False,
        ).select_related("bound_node")
    }
    shards = {
        (int(shard.node_id), str(shard.repository_subdir)): shard
        for shard in RepositoryUsageShard.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
            usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
            is_active=True,
        )
    }
    location = _repository_nas_location(repository)
    rows: list[dict] = []
    for config in configs:
        if config.source_type == "agent":
            source = _agent_source_payload(
                agents.get(config.source_ref_id), config=config
            )
            subdir = nas_agent_repository_subdir(config.source_ref_id)
            shard = shards.get((int(config.source_ref_id), subdir))
            probe_status = shard.status if shard else ""
            health = (
                Repository.Health.ONLINE
                if probe_status == RepositoryUsageShard.Status.SUCCESS
                else Repository.Health.OFFLINE
                if probe_status
                in {
                    RepositoryUsageShard.Status.FAILED,
                    RepositoryUsageShard.Status.SKIPPED,
                }
                else Repository.Health.UNVERIFIED
            )
        else:
            source = _nas_source_payload(
                nas_sources.get(config.source_ref_id), config=config
            )
            bound_node = nas_sources.get(config.source_ref_id)
            bound_node = bound_node.bound_node if bound_node is not None else None
            node_id = int(bound_node.id) if bound_node is not None else 0
            subdir = nas_agent_repository_subdir(node_id) if node_id > 0 else ""
            shard = shards.get((node_id, subdir)) if subdir else None
            probe_status = shard.status if shard else ""
            health = (
                Repository.Health.ONLINE
                if probe_status == RepositoryUsageShard.Status.SUCCESS
                else Repository.Health.OFFLINE
                if probe_status
                in {
                    RepositoryUsageShard.Status.FAILED,
                    RepositoryUsageShard.Status.SKIPPED,
                }
                else repository.health
            )
        rows.append(
            {
                "backup_config_id": config.id,
                "backup_config_name": config.name,
                **source,
                "nas_location": location,
                "repository_subdir": subdir,
                "repository_mount_point": _repository_mount_point_for_source(
                    repository=repository,
                    config=config,
                    source=source,
                    shard=shard,
                ),
                "health": health,
                "probe_status": probe_status,
                "last_checked_at": _iso(shard.last_checked_at) if shard else None,
                "last_success_checked_at": _iso(shard.last_success_checked_at)
                if shard
                else None,
                "last_error": shard.last_error if shard else "",
            }
        )
    return total, rows


class RepositoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsOrgWriter]
    serializer_class = RepositorySerializer

    def _membership(self):
        membership = get_membership(self.request)
        if membership is None:
            raise PermissionDenied("organization membership required")
        return membership

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated(), IsOrgStaffReader()]
        return super().get_permissions()

    def get_queryset(self):
        organization_id = self._membership().organization_id
        if self.action != "list":
            return list_repositories(organization_id=organization_id)
        queryset = list_repositories(
            organization_id=organization_id,
            repo_type=self.request.query_params.get("repo_type") or None,
            status=self.request.query_params.get("status") or None,
            health=self.request.query_params.get("health") or None,
            search=self.request.query_params.get("search") or None,
            search_field=self.request.query_params.get("search_field") or None,
        )
        if not self.request.query_params.get("status"):
            queryset = queryset.filter(
                ~Q(status=Repository.Status.REMOVED)
                | Q(location_claims__state="residual")
            ).distinct()
        return queryset

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return RepositoryWriteSerializer
        return RepositorySerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action in {"create", "update", "partial_update"}:
            context["organization_id"] = self._membership().organization_id
            context["request_action"] = self.action
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        repository = serializer.save()
        out = RepositorySerializer(repository, context=self.get_serializer_context())
        if repository.status == Repository.Status.CREATING:
            return Response(out.data, status=status.HTTP_202_ACCEPTED)
        return Response(out.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        repository = serializer.save()
        out = RepositorySerializer(repository, context=self.get_serializer_context())
        return Response(out.data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        serializer = RepositoryCleanupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        repository = self.get_object()
        try:
            repository_task = create_repository_cleanup_task(
                repository=repository,
                requested_by=request.user,
                force=serializer.validated_data["force"],
            )
        except RepositoryCleanupBlocked as exc:
            return Response(exc.preflight, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            raise ValidationError(_django_validation_detail(exc)) from exc
        return Response(
            TaskSerializer(repository_task.task).data, status=status.HTTP_202_ACCEPTED
        )

    @action(detail=True, methods=["post"], url_path="cleanup/preflight")
    def cleanup_preflight(self, request, pk=None):
        serializer = RepositoryCleanupPreflightSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            repository_cleanup_preflight(
                repository=self.get_object(),
                force=serializer.validated_data["force"],
            ),
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="validate/s3")
    def validate_s3(self, request):
        data = request.data
        access_key_id = str(data.get("access_key_id") or "").strip()
        secret_access_key = str(data.get("secret_access_key") or "")
        try:
            bucket_limit = int(data.get("count") or 3)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"count": "count must be an integer."}) from exc
        bucket_limit = max(1, min(bucket_limit, 1000))
        if not access_key_id:
            raise ValidationError({"access_key_id": "Access key ID is required."})
        if not secret_access_key:
            raise ValidationError(
                {"secret_access_key": "Secret access key is required."}
            )
        platform = str(data.get("platform") or "custom").strip().lower()
        if platform == "other":
            platform = Repository.S3Platform.CUSTOM
        region_id = str(data.get("region") or "").strip()
        endpoint = normalize_s3_endpoint_host(data.get("endpoint"))
        s3_url_style = str(data.get("s3_url_style") or "").strip() or None
        use_tls = data.get("use_tls") is not False
        if platform != Repository.S3Platform.CUSTOM:
            provider_record = get_effective_storage_provider(platform)
            provider = provider_record.get("config") if provider_record else None
            region = next(
                (
                    item
                    for item in (provider or {}).get("regions", [])
                    if item.get("id") == region_id
                ),
                None,
            )
            if not provider or not provider.get("enabled") or region is None:
                raise ValidationError(
                    {"region": "Region is not in the current Provider Catalog."}
                )
            endpoint = str(region["external_endpoint"])
            s3_url_style = str(region["s3_url_style"])
            use_tls = bool(region["use_tls"])
        try:
            buckets = validate_s3_connection(
                platform=platform,
                endpoint=endpoint,
                region=region_id,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                s3_url_style=s3_url_style,
                use_tls=use_tls,
            )
        except RepositoryInitializationError as exc:
            raise s3_validation_app_error(exc, operation="list_buckets") from exc
        limited_buckets = buckets[:bucket_limit]
        return Response(
            {
                "buckets": limited_buckets,
                "count": len(limited_buckets),
                "total_count": len(buckets),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="verify-access")
    def verify_access(self, request, pk=None):
        repository = self.get_object()
        if repository.repo_type != Repository.Type.S3:
            raise ValidationError(
                {"detail": "Verify access only supports S3 repositories."}
            )
        # Pull saved connection/auth fields from the repository. Bucket, endpoint
        # and prefix are intentionally locked and must come from the saved row.
        saved_config = repository.config or {}
        saved_secrets = resolve_repository_secrets(repository)
        saved_access_key_id = str(saved_config.get("access_key_id") or "")
        saved_secret_access_key = str(saved_secrets.get("secret_access_key") or "")
        saved_region = str(saved_config.get("region") or "")
        saved_s3_url_style = normalize_s3_url_style(
            saved_config.get("s3_url_style"), platform=repository.s3_platform
        )
        saved_use_tls = saved_config.get("use_tls") is not False

        # Accept optional draft overrides from the client. We still strip any
        # forbidden fields (bucket / endpoint / prefix) just in case.
        data = request.data if isinstance(request.data, dict) else {}
        forbidden = {
            "endpoint",
            "endpoint_type",
            "external_endpoint",
            "internal_endpoint",
            "prefix",
            "bucket",
            "s3_bucket",
        }
        if any(k in data for k in forbidden):
            raise ValidationError(
                {
                    "detail": "Endpoint, prefix and bucket are locked and cannot be verified."
                }
            )

        access_key_id = (
            str(data.get("access_key_id") or "").strip() or saved_access_key_id
        )
        secret_access_key = (
            str(data.get("secret_access_key") or "") or saved_secret_access_key
        )
        region = str(data.get("region") or "").strip() or saved_region
        s3_url_style = str(data.get("s3_url_style") or "").strip() or saved_s3_url_style
        if "use_tls" in data:
            use_tls = bool(data.get("use_tls"))
        else:
            use_tls = saved_use_tls

        try:
            result = verify_s3_bucket_access(
                endpoint=repository_control_endpoint(saved_config),
                region=region,
                bucket=str(repository.s3_bucket or ""),
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                s3_url_style=s3_url_style,
                use_tls=use_tls,
            )
        except RepositoryInitializationError as exc:
            raise s3_validation_app_error(exc, operation="bucket_access") from exc
        return Response({"ok": True, **result}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        repository = check_repository(repository=self.get_object())
        return Response(
            {
                "repository": RepositorySerializer(repository).data,
                "health": repository.health,
                "task_id": None,
                "message": "",
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="retry-initialization")
    def retry_initialization(self, request, pk=None):
        repository = self.get_object()
        try:
            repository_task = retry_repository_create(
                repository=repository,
                requested_by=request.user,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_django_validation_detail(exc)) from exc
        repository.refresh_from_db()
        return Response(
            {
                "repository": RepositorySerializer(
                    repository,
                    context=self.get_serializer_context(),
                ).data,
                "task": TaskSerializer(repository_task.task).data,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="release-residual-location")
    def release_residual_location(self, request, pk=None):
        repository = self.get_object()
        serializer = RepositoryResidualReleaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            released_count = release_repository_residual_locations(repository)
        except DjangoValidationError as exc:
            raise ValidationError(_django_validation_detail(exc)) from exc
        organization = Organization.objects.filter(
            pk=repository.organization_id
        ).first()
        if organization is not None:
            write_audit_log_from_request(
                request,
                organization=organization,
                action=AuditAction.UPDATE,
                resource_type=AuditResourceType.REPOSITORY,
                resource_id=str(repository.id),
                resource_name=repository.name,
                details=(
                    "Operator confirmed external residual cleanup and released "
                    f"{released_count} repository location claim(s)."
                ),
                metadata={
                    "operation": "release_residual_location",
                    "released_claim_count": released_count,
                },
            )
        repository.refresh_from_db()
        return Response(
            RepositorySerializer(
                repository,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="associated-sources")
    def associated_sources(self, request, pk=None):
        repository = self.get_object()
        page = _positive_int_param(request.query_params.get("page"), default=1)
        page_size = _positive_int_param(
            request.query_params.get("page_size"), default=10, max_value=100
        )
        count, rows = _associated_sources_payload(
            repository, page=page, page_size=page_size
        )
        return Response({"count": count, "results": rows}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="tasks")
    def repository_tasks(self, request, pk=None):
        repository = self.get_object()
        queryset = (
            Task.objects.filter(
                organization_id=repository.organization_id,
                task_type=Task.Type.REPOSITORY_OPERATION,
                repository_operation__repository_id=repository.id,
            )
            .select_related(
                "repository_operation__execution_target",
                "repository_operation__triggered_by_task",
            )
            .prefetch_related("resources", "steps", "events")
            .order_by("-created_at", "-id")
        )
        operation_type = str(request.query_params.get("operation_type") or "").strip()
        task_status = str(request.query_params.get("status") or "").strip()
        trigger_type = str(request.query_params.get("trigger_type") or "").strip()
        search = str(request.query_params.get("search") or "").strip()
        search_field = (
            str(request.query_params.get("search_field") or "").strip().lower()
        )
        if search_field not in {"", "name", "uuid"}:
            raise ValidationError(
                {"search_field": "search_field must be one of: name, uuid."}
            )
        if operation_type:
            queryset = queryset.filter(
                repository_operation__operation_type=operation_type
            )
        if task_status:
            queryset = queryset.filter(status=task_status)
        if trigger_type:
            queryset = queryset.filter(trigger_type=trigger_type)
        if search:
            if search_field == "name":
                queryset = queryset.filter(display_name__icontains=search)
            elif search_field == "uuid":
                try:
                    queryset = queryset.filter(task_uuid=UUID(search))
                except (TypeError, ValueError):
                    queryset = queryset.none()
            else:
                filters = Q(display_name__icontains=search)
                try:
                    filters |= Q(task_uuid=UUID(search))
                except (TypeError, ValueError):
                    pass
                queryset = queryset.filter(filters)
        created_after = request.query_params.get("created_after")
        created_before = request.query_params.get("created_before")
        if created_after:
            parsed_after = parse_datetime(created_after)
            if parsed_after is None:
                raise ValidationError(
                    {"created_after": "Must be an ISO-8601 datetime."}
                )
            queryset = queryset.filter(created_at__gte=parsed_after)
        if created_before:
            parsed_before = parse_datetime(created_before)
            if parsed_before is None:
                raise ValidationError(
                    {"created_before": "Must be an ISO-8601 datetime."}
                )
            queryset = queryset.filter(created_at__lte=parsed_before)
        page = self.paginate_queryset(queryset)
        serializer = TaskSerializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @action(detail=True, methods=["post"], url_path="sync-usage")
    def sync_usage(self, request, pk=None):
        repository = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        result = enqueue_repository_usage_refresh(
            organization_id=repository.organization_id,
            repository_ids=[repository.id],
            limit=1,
            force=_bool_param(data.get("force"), default=True),
            stale_after_seconds=_optional_int_param(
                data.get("stale_after_seconds"), "stale_after_seconds"
            ),
            trigger="api.repository.sync_usage",
        )
        return Response(result, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["post"], url_path="sync-usage")
    def sync_usage_all(self, request):
        membership = self._membership()
        data = request.data if isinstance(request.data, dict) else {}
        try:
            limit = int(data.get("limit") or 200)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"limit": "limit must be an integer."}) from exc
        limit = max(1, min(limit, 500))
        repo_type = str(data.get("repo_type") or "").strip() or None
        if repo_type and repo_type not in {
            choice[0] for choice in Repository.Type.choices
        }:
            raise ValidationError({"repo_type": "Unsupported repository type."})
        stale_after_seconds = _optional_int_param(
            data.get("stale_after_seconds"), "stale_after_seconds"
        )
        result = enqueue_repository_usage_refresh(
            organization_id=membership.organization_id,
            repository_ids=_int_list_param(data.get("repository_ids")),
            repo_type=repo_type,
            limit=limit,
            force=_bool_param(data.get("force"), default=False),
            stale_after_seconds=stale_after_seconds,
            trigger="api.repositories.sync_usage",
        )
        return Response(result, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["patch"], url_path="repair")
    def repair(self, request, pk=None):
        repository = self.get_object()
        if repository.repo_type != Repository.Type.NAS:
            raise ValidationError(
                {"detail": "Repair is only supported for NAS repositories."}
            )
        serializer = NASRepositoryRepairSerializer(
            data=request.data,
            context={"repository": repository, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        try:
            repository = repair_nas_repository(
                repository=repository,
                name=payload.get("name"),
                config_updates=payload.get("config") or {},
                bind_node_id=payload.get("bind_node_id", _UNSET_SENTINEL),
            )
        except NASRepositoryBusyError as exc:
            # Busy errors include structured task detail; surface them as 409.
            raise exc
        out = RepositorySerializer(repository, context=self.get_serializer_context())
        if repository.status == Repository.Status.CREATING:
            return Response(out.data, status=status.HTTP_202_ACCEPTED)
        return Response(out.data, status=status.HTTP_200_OK)
