"""REST views for ``Node`` lifecycle."""

import logging

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response

from apps.audit.services.interface import write_audit_log
from apps.iam.models import Organization
from apps.node.api import permissions as node_permissions
from apps.node.api.serializers import (
    NodeHeartbeatSerializer,
    NodeSerializer,
)
from apps.node.api.serializers.node_operation import NodeOperationStartSerializer
from apps.node.api.serializers.lifecycle_watch import (
    NodeLifecycleWatchEntrySerializer,
    NodeLifecycleWatchRequestSerializer,
)
from apps.node.api.views.node_operation import _lifecycle_error_response
from common.drf.org_scoped import OrgScopedMixin
from apps.node.api.views.mixins import SoftDeleteDestroyMixin
from apps.node.api.pagination import NodePagination
from apps.node.models import Node, NodeInstallationSession, NodeToken
from apps.node.models.base import NodeRole
from apps.node.selectors.internal.node_query import node_field_search_q, node_search_q
from apps.monitor.services.internal.node_metrics import ingest_node_heartbeat_metrics
from apps.node.selectors.interface import list_nodes
from apps.node.services.internal.node_lifecycle import (
    LIFECYCLE_KIND_UPGRADE,
    enrich_node_row,
    start_node_remove,
    start_node_upgrade,
)
from apps.node.services.internal.node_naming import (
    is_auto_assigned_node_name,
    is_automatic_user_node_name,
    resolve_registration_node_name,
    runtime_principal_name,
    uniquify_node_name,
)
from apps.node.services.internal.network_inventory import split_network_from_metadata
from apps.node.services.internal.local_platform_gateway import registration_metadata
from apps.node.services.internal.node_registry import record_node_available
from apps.node.exceptions import NodeLifecycleError
from apps.node.services.internal.agent_uninstall import ProxyHasBoundResources
from apps.node.services.internal.bindings import (
    collect_proxy_bindings,
    count_proxy_repository_bindings,
)
from apps.node.services.internal.client_ip import resolve_agent_client_ip
from apps.node.services.internal.enrollment_auth import (
    EnrollmentAuthorization,
    complete_enrollment_authorization,
    issue_node_credential,
    legacy_enrollment_token_for_node,
    resolve_enrollment_authorization,
    validate_node_credential,
)
from apps.source.services.internal.agent_host_sync import sync_agent_source_host

logger = logging.getLogger(__name__)


def _reported_platform(payload: dict) -> str:
    """Return the bounded platform family reported by the enrollment client."""

    def normalize(value: object) -> str:
        platform = str(value or "").strip().lower()
        if platform == "darwin":
            return NodeToken.TargetPlatform.MACOS
        if platform in NodeToken.TargetPlatform.values:
            return platform
        return ""

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        platform = normalize(metadata.get("platform"))
        if platform:
            return platform
        inventory = metadata.get("inventory")
        if isinstance(inventory, dict):
            platform = normalize(inventory.get("os_family"))
            if platform:
                return platform
    os_name = str(payload.get("os_name") or "").strip().lower()
    if "windows" in os_name:
        return NodeToken.TargetPlatform.WINDOWS
    if "darwin" in os_name or "macos" in os_name or "mac os" in os_name:
        return NodeToken.TargetPlatform.MACOS
    if "linux" in os_name:
        return NodeToken.TargetPlatform.LINUX
    return ""


def _token_authorizes_installation_mode(
    token: NodeToken,
    mode: str,
    *,
    reported_platform: str = "",
) -> bool:
    """Validate the final local mode without treating ``auto`` as a Node mode."""
    if token.installation_mode_policy == NodeToken.InstallationModePolicy.FIXED:
        return mode == token.installation_mode
    if token.installation_mode_policy != NodeToken.InstallationModePolicy.AUTO:
        return False
    if reported_platform != token.target_platform:
        return False
    allowed_by_platform = {
        NodeToken.TargetPlatform.LINUX: {
            Node.InstallationMode.USER_CONTINUOUS,
            Node.InstallationMode.SYSTEM,
        },
        NodeToken.TargetPlatform.WINDOWS: {
            Node.InstallationMode.USER,
            Node.InstallationMode.SYSTEM,
        },
        NodeToken.TargetPlatform.MACOS: {
            Node.InstallationMode.USER,
            Node.InstallationMode.SYSTEM,
        },
    }
    return token.role == Node.Role.AGENT and mode in allowed_by_platform.get(
        token.target_platform,
        set(),
    )


def health(_request):
    return JsonResponse({"app": "node", "status": "ok"})


class NodeViewSet(OrgScopedMixin, SoftDeleteDestroyMixin, viewsets.ModelViewSet):
    org_scoped_skip_actions = ("heartbeat",)

    serializer_class = NodeSerializer
    permission_classes = [
        node_permissions.IsAuthenticated,
        node_permissions.IsOrgWriter,
    ]

    def create(self, request, *args, **kwargs):
        """Nodes are created only by the enrollment/heartbeat protocol."""
        del request, args, kwargs
        raise MethodNotAllowed(
            "POST",
            detail="Nodes must be created through the enrollment workflow.",
        )

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [
                node_permissions.IsAuthenticated(),
                node_permissions.IsOrgStaffReader(),
            ]
        if self.action == "lifecycle_watch":
            return [
                node_permissions.IsAuthenticated(),
                node_permissions.IsOrgOperator(),
            ]
        if self.action == "maintenance_release":
            return [
                node_permissions.IsAuthenticated(),
                node_permissions.IsOrgOperator(),
            ]
        if self.action == "heartbeat":
            return [node_permissions.AllowAny()]
        if self.action == "operations":
            return [
                node_permissions.IsAuthenticated(),
                node_permissions.IsOrgOperator(),
            ]
        return super().get_permissions()

    def get_org_scoped_queryset(self):
        queryset = Node.objects.select_related("organization").all()
        role = (self.request.query_params.get("role") or "").strip()
        if role:
            queryset = queryset.filter(role=role)
        status = (self.request.query_params.get("status") or "").strip()
        if status:
            queryset = queryset.filter(status=status)
        return queryset.order_by("name", "id")

    def _build_enrichments(self, nodes) -> dict[int, dict]:
        from apps.node.services.internal.agent_upgrade import node_agent_release_status

        enrichments: dict[int, dict] = {}
        release_targets: dict[tuple[str, str, str, str], str] = {}
        for node in nodes:
            enrichments[node.id] = enrich_node_row(
                org=self.org,
                node=node,
                user=self.request.user,
            )
            enrichments[node.id]["agent_release"] = node_agent_release_status(
                node,
                target_cache=release_targets,
            )
        repository_counts = count_proxy_repository_bindings(
            organization_id=self.org.id,
            proxy_ids=[node.id for node in nodes if node.role == NodeRole.PROXY],
        )
        for node in nodes:
            enrichments[node.id]["associated_repository_count"] = repository_counts.get(
                node.id, 0
            )
        return enrichments

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if getattr(self, "_node_enrichments", None) is not None:
            context["enrichments"] = self._node_enrichments
        return context

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        search = (request.query_params.get("search") or "").strip()
        search_field = (request.query_params.get("search_field") or "").strip()
        if search:
            field_query = (
                node_field_search_q(search_field, search) if search_field else None
            )
            queryset = queryset.filter(field_query or node_search_q(search))

        page_size_raw = request.query_params.get("page_size")
        if page_size_raw is not None:
            paginator = NodePagination()
            page = paginator.paginate_queryset(queryset, request, view=self)
            nodes = list(page) if page is not None else []
            self._node_enrichments = self._build_enrichments(nodes)
            serializer = self.get_serializer(nodes, many=True)
            if page is not None:
                return paginator.get_paginated_response(serializer.data)

        nodes = list(queryset)
        self._node_enrichments = self._build_enrichments(nodes)
        serializer = self.get_serializer(nodes, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        self._node_enrichments = self._build_enrichments([instance])
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="lifecycle-watch")
    def lifecycle_watch(self, request):
        """Poll lifecycle state for in-flight upgrade/remove batches (read-only)."""
        ser = NodeLifecycleWatchRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        node_ids = ser.validated_data["node_ids"]
        nodes = list(
            self.get_org_scoped_queryset().filter(pk__in=node_ids).order_by("id"),
        )
        payload = NodeLifecycleWatchEntrySerializer(
            nodes,
            many=True,
            context={"org": self.org},
        ).data
        return Response({"nodes": payload})

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.role not in (NodeRole.AGENT, NodeRole.PROXY, NodeRole.GATEWAY):
            return super().destroy(request, *args, **kwargs)
        try:
            result = start_node_remove(
                org=instance.organization,
                node=instance,
                user=request.user,
            )
        except NodeLifecycleError as exc:
            if exc.code == "proxy_has_bindings":
                bindings = collect_proxy_bindings(
                    organization_id=instance.organization_id,
                    proxy_id=instance.id,
                )
                return Response(
                    {
                        "detail": str(exc),
                        "bound": bindings.totals,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {"detail": str(exc), "code": exc.code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ProxyHasBoundResources as exc:
            return Response(
                {
                    "detail": "Proxy has bound resources. Replace them before deletion.",
                    "bound": exc.bindings.totals,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if result.get("state") != "completed":
            return Response(
                {
                    "detail": "Node removal is asynchronous. Use POST /nodes/{id}/operations/.",
                    "operation": result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance: Node) -> None:
        super().perform_destroy(instance)

    @action(detail=True, methods=["post"], url_path="operations")
    def operations(self, request, pk=None):
        node = self.get_object()
        ser = NodeOperationStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        kind = ser.validated_data["kind"]
        try:
            if kind == LIFECYCLE_KIND_UPGRADE:
                result = start_node_upgrade(org=self.org, node=node, user=request.user)
            else:
                result = start_node_remove(
                    org=self.org,
                    node=node,
                    user=request.user,
                    force=bool(ser.validated_data.get("force")),
                )
        except Exception as exc:
            return _lifecycle_error_response(exc)

        write_audit_log(
            organization=self.org,
            user=request.user,
            action=f"node.lifecycle.{kind}",
            target_type="node",
            target_id=str(node.id),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=str(request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={
                "kind": kind,
                "role": node.role,
                "operation_id": result.get("operation_id"),
                "task_id": result.get("task_id"),
                "state": result.get("state"),
            },
        )
        return Response(result, status=status.HTTP_202_ACCEPTED)

    @action(
        detail=True,
        methods=["get"],
        url_path="audit-logs",
        permission_classes=[node_permissions.IsOrgStaffReader],
    )
    def audit_logs(self, request, pk=None):
        """Return recent lifecycle audit logs for this node."""
        node = self.get_object()
        from apps.audit.models.audit_log import AuditLog

        logs = AuditLog.objects.filter(
            organization=self.org,
            target_type="node",
            target_id=str(node.id),
            action__startswith="node.lifecycle.",
        ).order_by("-created_at")[:20]

        return Response(
            {
                "node_id": node.id,
                "results": [
                    {
                        "id": log.id,
                        "action": log.action,
                        "user_display": log.user_name
                        or (log.user.email if log.user else None)
                        or "—",
                        "result": log.result,
                        "created_at": log.created_at.isoformat()
                        if log.created_at
                        else None,
                        "ip_address": log.ip_address,
                        "error_message": log.error_message,
                        "correlation_id": log.correlation_id,
                        "metadata": log.metadata,
                    }
                    for log in logs
                ],
            }
        )

    @action(
        detail=True,
        methods=["get"],
        permission_classes=[node_permissions.IsOrgStaffReader],
    )
    def bindings(self, request, pk=None):
        """Return the resources bound to this node as a Proxy worker.

        Empty when the node is not a Proxy.
        """
        node = self.get_object()
        from apps.node.services.internal.bindings import collect_proxy_bindings

        return Response(
            collect_proxy_bindings(
                organization_id=node.organization_id,
                proxy_id=node.id,
            ).to_payload()
        )

    @action(detail=True, methods=["post"], url_path="maintenance-release")
    def maintenance_release(self, request, pk=None):
        """Return a signed package URL for manual maintenance of this node."""
        from apps.node.api.views.artifact_release import (
            issue_node_maintenance_release,
        )
        from common.deploy.site import tenant_public_url

        node = self.get_object()
        try:
            payload = issue_node_maintenance_release(
                request=request,
                node=node,
                api_base=tenant_public_url(),
            )
        except FileNotFoundError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        write_audit_log(
            organization=self.org,
            user=request.user,
            action="node.maintenance.release.generate",
            target_type="node",
            target_id=str(node.id),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=str(request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={
                "role": node.role,
                "version": payload["version"],
                "expires_in": payload["expires_in"],
            },
        )
        return Response(payload)

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[node_permissions.AllowAny],
    )
    def heartbeat(self, request):
        org_key = str(request.headers.get("X-Org-Key", "") or "").strip()
        node_token = str(request.headers.get("X-Node-Token", "") or "").strip()
        if not org_key:
            return Response(
                {"error": "X-Org-Key required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org = Organization.objects.filter(key=org_key, is_active=True).first()
        if org is None:
            return Response(
                {"error": "organization not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = NodeHeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        client_ip = resolve_agent_client_ip(request)
        installation_id = str(payload.get("installation_id") or "").strip()
        host_fingerprint = str(payload.get("host_fingerprint") or "").strip()
        existing_node_credential = str(
            payload.get("existing_node_credential") or ""
        ).strip()

        node_id = payload.get("node_id")
        node = None
        token_row = None
        node_credential = ""
        credential_reused = False
        metadata_payload, network_state = split_network_from_metadata(
            payload.get("metadata")
        )
        if node_id:
            node = list_nodes(organization=org).filter(pk=node_id).first()

        observed_at = timezone.now()
        new_agent_registered = False
        if node is None and node_token:
            authorization = resolve_enrollment_authorization(
                org=org,
                secret=node_token,
                role=payload["role"],
            )
            if authorization is None:
                return Response(
                    {"error": "invalid enrollment token"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            token_row = authorization.token
            resolved_installation_mode = payload["installation_mode"]
            reported_platform = _reported_platform(payload)
            if not _token_authorizes_installation_mode(
                token_row,
                resolved_installation_mode,
                reported_platform=reported_platform,
            ):
                return Response(
                    {"error": "installation mode does not match enrollment token"},
                    status=status.HTTP_409_CONFLICT,
                )
            with transaction.atomic():
                # Match the token -> session lock order used by session creation.
                # A consistent order prevents open/register deadlocks under load.
                session = authorization.session
                locked_token = NodeToken.all_objects.select_for_update().get(
                    pk=authorization.token.pk
                )
                authorization = EnrollmentAuthorization(
                    token=locked_token,
                    session=session,
                )
                token_row = locked_token
                if not _token_authorizes_installation_mode(
                    token_row,
                    resolved_installation_mode,
                    reported_platform=reported_platform,
                ):
                    return Response(
                        {"error": "installation mode authorization changed"},
                        status=status.HTTP_409_CONFLICT,
                    )
                if session is not None:
                    locked_session = (
                        NodeInstallationSession.objects.select_for_update().get(
                            pk=session.pk
                        )
                    )
                    now = timezone.now()
                    if (
                        locked_session.status != NodeInstallationSession.Status.ACTIVE
                        or locked_session.idle_expires_at <= now
                        or locked_session.absolute_expires_at <= now
                    ):
                        return Response(
                            {
                                "error": "installation session expired or is no longer active"
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    if locked_session.installation_id != installation_id:
                        return Response(
                            {
                                "error": (
                                    "installation session does not match "
                                    "this installation identity"
                                )
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    authorization = EnrollmentAuthorization(
                        token=authorization.token,
                        session=locked_session,
                    )
                node = None
                if installation_id:
                    # The installation identity is authoritative; the host
                    # fingerprint is non-unique correlation metadata. A fresh
                    # local installation must create a new Node even when an
                    # older record has the same host fingerprint.
                    node = (
                        Node.objects.select_for_update()
                        .filter(
                            organization=org,
                            role=payload["role"],
                            installation_id=installation_id,
                        )
                        .first()
                    )
                if (
                    node is not None
                    and node.installation_mode != resolved_installation_mode
                ):
                    return Response(
                        {"error": "installation mode is fixed during enrollment"},
                        status=status.HTTP_409_CONFLICT,
                    )
                created_node = node is None
                if node is None:
                    from apps.subscription.services.interface import (
                        enforce_node_role_quota,
                    )

                    enforce_node_role_quota(organization=org, role=payload["role"])
                    try:
                        with transaction.atomic():
                            node = Node.objects.create(
                                organization=org,
                                name=uniquify_node_name(
                                    organization_id=org.id,
                                    name=resolve_registration_node_name(
                                        payload=payload
                                    ),
                                ),
                                role=payload["role"],
                                installation_mode=resolved_installation_mode,
                                version=payload.get("version", ""),
                                os_name=payload.get("os_name", ""),
                                availability_updated_at=observed_at,
                                installation_id=installation_id,
                                host_fingerprint=host_fingerprint,
                                metadata=registration_metadata(
                                    metadata_payload,
                                    token_note=token_row.note,
                                ),
                                last_seen_at=observed_at,
                                ip_address=network_state.primary_ip_address,
                                connection_ip_address=client_ip,
                                network_inventory=network_state.inventory or {},
                            )
                    except IntegrityError:
                        if not installation_id:
                            raise
                        node = (
                            Node.objects.select_for_update()
                            .filter(
                                organization=org,
                                role=payload["role"],
                                installation_id=installation_id,
                            )
                            .first()
                        )
                        if node is None:
                            raise
                        if node.installation_mode != resolved_installation_mode:
                            return Response(
                                {
                                    "error": (
                                        "installation mode is fixed during enrollment"
                                    )
                                },
                                status=status.HTTP_409_CONFLICT,
                            )
                        created_node = False
                    else:
                        unique_name = uniquify_node_name(
                            organization_id=org.id,
                            name=node.name,
                            exclude_node_id=node.id,
                        )
                        if unique_name != node.name:
                            node.name = unique_name
                            node.save(update_fields=["name", "updated_at"])
                if host_fingerprint and not node.host_fingerprint:
                    node.host_fingerprint = host_fingerprint
                    node.save(update_fields=["host_fingerprint", "updated_at"])
                credential_reused = bool(
                    not created_node
                    and existing_node_credential
                    and validate_node_credential(
                        node,
                        existing_node_credential,
                        touch=False,
                    )
                )
                if not credential_reused:
                    node_credential = issue_node_credential(
                        node=node,
                        enrollment_token=token_row,
                        installation_id=installation_id,
                    )
                try:
                    complete_enrollment_authorization(authorization)
                except PermissionError as exc:
                    transaction.set_rollback(True)
                    return Response(
                        {"error": str(exc)},
                        status=status.HTTP_409_CONFLICT,
                    )
                if created_node and node.role == NodeRole.AGENT:
                    record_node_available(node_id=node.id, observed_at=observed_at)
                    node.refresh_from_db(
                        fields=["availability", "availability_updated_at"]
                    )
                    sync_agent_source_host(node=node)
                    new_agent_registered = True
        elif node is not None:
            legacy_token = None
            credential_valid = validate_node_credential(node, node_token)
            if not credential_valid:
                legacy_token = legacy_enrollment_token_for_node(
                    node,
                    node_token,
                    expected_role=node.role,
                )
            if legacy_token is None and not credential_valid:
                return Response(
                    {"error": "invalid node credential"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            if payload["installation_mode"] != node.installation_mode:
                return Response(
                    {"error": "installation mode is fixed during enrollment"},
                    status=status.HTTP_409_CONFLICT,
                )
            if legacy_token is not None:
                token_row = legacy_token
                node_credential = issue_node_credential(
                    node=node,
                    enrollment_token=legacy_token,
                    installation_id=installation_id or node.installation_id,
                )
            if installation_id and not node.installation_id:
                identity_in_use = (
                    Node.objects.filter(
                        organization=org,
                        role=node.role,
                        installation_id=installation_id,
                    )
                    .exclude(pk=node.pk)
                    .exists()
                )
                if not identity_in_use:
                    node.installation_id = installation_id
            node.last_seen_at = observed_at
            if node.installation_mode in (
                Node.InstallationMode.USER,
                Node.InstallationMode.USER_CONTINUOUS,
            ):
                # User-scoped instances are identified by the runtime
                # principal. Do not let the ordinary hostname in a heartbeat
                # erase the account suffix after reconnecting, but preserve a
                # display name that a console user explicitly assigned.
                principal = runtime_principal_name(metadata_payload)
                if principal and (
                    is_automatic_user_node_name(
                        name=node.name,
                        metadata=node.metadata,
                        node_id=node.id,
                    )
                    or is_automatic_user_node_name(
                        name=node.name,
                        metadata=metadata_payload,
                        node_id=node.id,
                    )
                ):
                    next_name = uniquify_node_name(
                        organization_id=org.id,
                        name=resolve_registration_node_name(payload=payload),
                        exclude_node_id=node.id,
                    )
                    if next_name != node.name:
                        node.name = next_name
            elif is_auto_assigned_node_name(node.name):
                next_name = resolve_registration_node_name(
                    payload=payload,
                    fallback=node.name,
                )
                next_name = uniquify_node_name(
                    organization_id=org.id,
                    name=next_name,
                    exclude_node_id=node.id,
                )
                if next_name != node.name:
                    node.name = next_name
            elif payload.get("name"):
                node.name = payload.get("name")
            node.version = payload.get("version", node.version)
            node.os_name = payload.get("os_name", node.os_name)
            if "metadata" in payload:
                node.metadata = registration_metadata(
                    metadata_payload,
                    existing_metadata=node.metadata,
                )
                if network_state.primary_ip_address:
                    node.ip_address = network_state.primary_ip_address
                if network_state.inventory is not None:
                    node.network_inventory = network_state.inventory
            if client_ip:
                node.connection_ip_address = client_ip
            if host_fingerprint and not node.host_fingerprint:
                node.host_fingerprint = host_fingerprint
            node.save()
        else:
            return Response(
                {"error": "node not found; enrollment token required"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not new_agent_registered:
            record_node_available(node_id=node.id, observed_at=observed_at)
        node.refresh_from_db(fields=["availability", "availability_updated_at"])

        try:
            ingest_node_heartbeat_metrics(node=node)
        except Exception:
            logger.warning(
                "node heartbeat metrics ingest failed node_id=%s",
                node.id,
                exc_info=True,
            )

        if not new_agent_registered:
            try:
                sync_agent_source_host(node=node)
            except Exception:
                logger.warning(
                    "node heartbeat source-host sync failed node_id=%s",
                    node.id,
                    exc_info=True,
                )

        response_payload: dict = {"node_id": node.id, "status": node.status}
        if node_credential:
            response_payload["node_credential"] = node_credential
        if credential_reused:
            response_payload["credential_reused"] = True
        if node.role == NodeRole.GATEWAY:
            from apps.lens_bridge.services import provisioning

            lens = provisioning.provision_gateway_lens_on_register(
                org=org,
                gateway=node,
                owner_user=token_row.created_by if token_row is not None else None,
                scope=token_row.gateway_scope if token_row is not None else None,
            )
            if lens:
                response_payload["lens"] = lens

        return Response(response_payload)
