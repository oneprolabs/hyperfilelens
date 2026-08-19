from __future__ import annotations

import logging
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.constants import AuditAction, AuditResult
from apps.audit.services.interface import write_audit_log
from apps.iam.models import Organization
from apps.node import agent_paths
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.source.constants import (
    ConnectionTestStatus,
    MountStatus,
    ResourceStatus,
    ResourceType,
    SelectableSourceKind,
)
from apps.source.models import SourceResource
from apps.source.selectors.interface import source_resource_by_id, source_resources_queryset
from apps.source.services.internal.connection import (
    apply_connection_test_result_if_current,
    best_effort_unmount_on_proxy,
    mount_resource as _mount_resource,
    schedule_remount_after_proxy_change,
    run_connection_test,
    unmount_resource as _unmount_resource,
)
from apps.source.services.internal.availability import public_connection_result
from apps.source.services.internal.bound_node_rules import validate_bound_node_role
from apps.source.services.internal.nas_path_normalize import normalize_resource_config
from apps.source.services.internal.validators import validate_resource_payload
from apps.source.services.internal.source_pipeline import (
    delete_pipeline_entry,
    ensure_pipeline_entry,
    sync_pipeline_projection,
)
from apps.source.services.internal.source_credentials import (
    merge_source_credentials,
    protect_source_credentials,
    resolve_source_credentials,
    scrub_source_secrets,
)

logger = logging.getLogger(__name__)


def _cancel_active_connection_probe(resource: SourceResource) -> None:
    if resource.connection_test_status not in ConnectionTestStatus.ACTIVE:
        return
    resource.connection_test_status = ConnectionTestStatus.IDLE
    resource.connection_probe_token = None


def _purge_soft_deleted_name_collision(*, organization_id: int, name: str) -> None:
    """Remove legacy soft-deleted rows that still occupy the org/name unique slot."""
    ghosts = SourceResource.all_objects.filter(
        organization_id=organization_id,
        name=name,
        is_deleted=True,
    )
    for ghost in ghosts:
        if ghost.resource_type == ResourceType.NAS:
            delete_pipeline_entry(
                organization_id=organization_id,
                source_kind=SelectableSourceKind.NAS,
                ref_id=ghost.id,
            )
        ghost.delete()


def _queue_remount_after_proxy_binding(
    *,
    resource: SourceResource,
    old_node_id: int | None,
) -> None:
    resource.mount_status = MountStatus.UNMOUNTED
    resource.mount_point = ""
    resource.mount_error = ""
    resource.availability = "offline"
    resource.availability_updated_at = timezone.now()
    resource.save(
        update_fields=[
            "mount_status",
            "mount_point",
            "mount_error",
            "availability",
            "availability_updated_at",
            "updated_at",
        ]
    )
    schedule_remount_after_proxy_change(
        resource_id=resource.id,
        old_node_id=old_node_id,
    )


@transaction.atomic
def create_source_resource(
    *,
    organization: Organization,
    user,
    name: str,
    resource_type: str,
    config: dict | None = None,
    credentials: dict | None = None,
    bound_node_id: int | None = None,
    description: str = "",
    status: str | None = None,
) -> SourceResource:
    config = scrub_source_secrets(normalize_resource_config(resource_type, config or {}))
    credentials = credentials or {}
    validate_resource_payload(
        resource_type=resource_type,
        config=config,
        credentials=credentials,
    )
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Name is required.")
    _purge_soft_deleted_name_collision(
        organization_id=organization.id,
        name=normalized_name,
    )
    if source_resources_queryset(organization_id=organization.id).filter(name=normalized_name).exists():
        raise ValueError("A source resource with this name already exists.")

    from apps.subscription.services.interface import enforce_license_quota

    if resource_type in (ResourceType.NAS, ResourceType.NFS, ResourceType.CIFS):
        enforce_license_quota(organization, "max_source_nas", additional=1)

    node = None
    if bound_node_id:
        node = Node.objects.filter(id=bound_node_id, organization_id=organization.id).first()
        if node is None:
            raise ValueError("Bound node not found in this organization.")
        validate_bound_node_role(resource_type=resource_type, node=node)

    try:
        connection_probe_token = (
            uuid4() if resource_type in ResourceType.REQUIRES_MOUNT else None
        )
        resource = SourceResource.objects.create(
            organization=organization,
            name=normalized_name,
            description=description or "",
            resource_type=resource_type,
            config=config or {},
            credentials=protect_source_credentials(credentials),
            bound_node=node,
            status=status or ResourceStatus.ACTIVE,
            connection_test_status=(
                ConnectionTestStatus.PENDING
                if connection_probe_token is not None
                else ConnectionTestStatus.IDLE
            ),
            connection_probe_token=connection_probe_token,
            created_by=user if user and user.is_authenticated else None,
        )
    except IntegrityError as exc:
        raise ValueError("A source resource with this name already exists.") from exc
    if resource_type == ResourceType.NAS:
        mount_path = str((config or {}).get("path") or "").strip()
        if mount_path:
            resource.mount_point = agent_paths.require_agent_mount_path(mount_path)
            resource.save(update_fields=["mount_point"])
        ensure_pipeline_entry(
            organization_id=organization.id,
            source_kind=SelectableSourceKind.NAS,
            ref_id=resource.id,
        )
    write_audit_log(
        organization=organization,
        user=user,
        action=AuditAction.CREATE,
        resource_type="source_resource",
        resource_id=str(resource.id),
        resource_name=resource.name,
        result=AuditResult.SUCCESS,
    )
    if resource.resource_type in ResourceType.REQUIRES_MOUNT:
        from apps.source.tasks.connection_probe import (
            queue_source_resource_capacity_probe,
        )

        # The create API only queues remote I/O after commit. The task uses this
        # token to discard stale results after edits, rebinds, or deletion.
        transaction.on_commit(
            lambda resource_id=resource.id,
            probe_token=str(resource.connection_probe_token),
            expected_bound_node_id=int(resource.bound_node_id or 0): (
                queue_source_resource_capacity_probe(
                    resource_id=resource_id,
                    probe_token=probe_token,
                    expected_bound_node_id=expected_bound_node_id,
                )
            )
        )
    return resource


@transaction.atomic
def update_source_resource(
    *,
    resource: SourceResource,
    user,
    **fields,
) -> SourceResource:
    # Callers commonly pass an instance loaded before this transaction began.
    # Re-read it under a row lock so concurrent type transitions consume quota
    # only when the persisted resource actually enters a metered category.
    resource = (
        SourceResource.objects.select_for_update()
        .select_related("organization")
        .get(pk=resource.pk)
    )
    if set(fields.keys()) <= {"bound_node", "bound_node_id"}:
        node_id = fields.get("bound_node_id") or fields.get("bound_node")
        if node_id is not None and int(node_id) == resource.bound_node_id:
            return resource

    if "name" in fields and fields["name"]:
        name = str(fields["name"]).strip()
        if (
            source_resources_queryset(organization_id=resource.organization_id)
            .filter(name=name)
            .exclude(id=resource.id)
            .exists()
        ):
            raise ValueError("A source resource with this name already exists.")
        resource.name = name
    if "description" in fields:
        resource.description = fields["description"] or ""
    if "resource_type" in fields and fields["resource_type"]:
        next_resource_type = fields["resource_type"]
        nas_resource_types = (ResourceType.NAS, ResourceType.NFS, ResourceType.CIFS)
        if (
            resource.resource_type not in nas_resource_types
            and next_resource_type in nas_resource_types
        ):
            from apps.subscription.services.interface import enforce_license_quota

            enforce_license_quota(
                resource.organization,
                "max_source_nas",
                additional=1,
            )
        resource.resource_type = next_resource_type
    if "config" in fields:
        resource.config = scrub_source_secrets(
            {**(resource.config or {}), **(fields["config"] or {})}
        )
        resource.config = normalize_resource_config(resource.resource_type, resource.config)
    if "credentials" in fields:
        incoming = fields["credentials"] or {}
        resource.credentials = protect_source_credentials(
            merge_source_credentials(resource.credentials, incoming)
        )
    if "bound_node" in fields or "bound_node_id" in fields:
        node_id = fields.get("bound_node_id") or fields.get("bound_node")
        old_bound_node_id = resource.bound_node_id
        if node_id:
            node = Node.objects.filter(
                id=int(node_id),
                organization_id=resource.organization_id,
                is_deleted=False,
            ).first()
            if node is None:
                raise ValueError("Bound node not found.")
            validate_bound_node_role(resource_type=resource.resource_type, node=node)
            if node.availability != Node.Availability.ONLINE:
                raise ValueError(f'Node "{node.name}" is not online.')
            resource.bound_node = node
            bound_node_changed = old_bound_node_id != node.id
        else:
            # Source NAS resources must always stay bound to a Proxy.
            from apps.source.constants import ResourceType as _RT
            if resource.resource_type in (_RT.NAS, _RT.NFS, _RT.CIFS):
                raise ValueError(
                    "Cannot unbind proxy. Replace the proxy instead."
                )
            resource.bound_node = None
            bound_node_changed = old_bound_node_id is not None
    else:
        bound_node_changed = False
        old_bound_node_id = resource.bound_node_id
    if "status" in fields and fields["status"]:
        resource.status = fields["status"]

    _cancel_active_connection_probe(resource)

    validate_resource_payload(
        resource_type=resource.resource_type,
        config=resource.config,
        credentials=resolve_source_credentials(resource.credentials),
    )
    resource.save()
    if resource.resource_type == ResourceType.NAS:
        sync_pipeline_projection(
            organization_id=resource.organization_id,
            source_kind=SelectableSourceKind.NAS,
            ref_id=resource.id,
        )
    if bound_node_changed and resource.resource_type in ResourceType.REQUIRES_MOUNT and resource.bound_node:
        _queue_remount_after_proxy_binding(
            resource=resource,
            old_node_id=old_bound_node_id,
        )
    write_audit_log(
        organization=resource.organization,
        user=user,
        action=AuditAction.UPDATE,
        resource_type="source_resource",
        resource_id=str(resource.id),
        resource_name=resource.name,
        result=AuditResult.SUCCESS,
    )
    return resource


def delete_source_resource(*, resource: SourceResource, user, force: bool = False) -> dict:
    from apps.source.services.internal.backup_source_delete import (
        BackupSourceDeleteFailed,
        delete_backup_sources,
    )

    selectable_id = f"nas:{resource.id}"
    try:
        result = delete_backup_sources(
            org=resource.organization,
            ids=[selectable_id],
            force=force,
            user=user,
        )
    except BackupSourceDeleteFailed as exc:
        return {
            "deleted": False,
            "ok": False,
            "message": exc.message,
            "reasons": [reason.as_dict() for reason in exc.reasons],
            "hint": exc.hint,
            "agent_removal": None,
        }

    deleted = selectable_id in result.get("deleted", [])
    return {
        "deleted": deleted,
        "ok": result.get("ok", deleted),
        "result": result.get("result"),
        "warnings": result.get("warnings", []),
        "agent_removal": None,
    }


def test_resource_connection(*, resource: SourceResource) -> dict:
    probe_token = uuid4()
    claimed = (
        SourceResource.all_objects.filter(pk=resource.id, is_deleted=False)
        .exclude(status__in=ResourceStatus.REMOVAL_FENCED)
        .update(
            connection_test_status=ConnectionTestStatus.RUNNING,
            connection_probe_token=probe_token,
            updated_at=timezone.now(),
        )
    )
    if not claimed:
        return {
            "success": False,
            "message": "Connection testing is unavailable while this source is being removed.",
        }
    if resource.resource_type == ResourceType.NAS:
        sync_pipeline_projection(
            organization_id=resource.organization_id,
            source_kind=SelectableSourceKind.NAS,
            ref_id=resource.id,
        )
    try:
        result = run_connection_test(resource=resource)
    except Exception:
        logger.exception(
            "manual source connection test failed resource_id=%s",
            resource.id,
        )
        result = {
            "success": False,
            "message": "Connection test failed unexpectedly. Try again.",
        }
    current, skip_reason = apply_connection_test_result_if_current(
        resource_id=resource.id,
        probe_token=probe_token,
        result=result,
    )
    if current is None:
        if skip_reason in {
            "source_deleted",
            "source_removing",
        }:
            best_effort_unmount_on_proxy(
                resource=resource,
                node_id=int(resource.bound_node_id or 0),
                force=True,
            )
        return {**public_connection_result(result), "stale": True}
    return public_connection_result(result)


def test_draft_connection(
    *,
    organization_id: int,
    bound_node_id: int,
    resource_type: str,
    config: dict | None = None,
    credentials: dict | None = None,
) -> dict:
    node = Node.objects.filter(id=bound_node_id, organization_id=organization_id).first()
    if node is None:
        return {"success": False, "message": "Node not found."}
    try:
        validate_bound_node_role(resource_type=resource_type, node=node)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    validation_mount_point = agent_paths.source_validation_mount_point(
        str(uuid4()),
        node.id,
    )
    return public_connection_result(run_connection_test(
        bound_node=node,
        resource_type=resource_type,
        config=config,
        credentials=credentials,
        mount_point_override=validation_mount_point,
        cleanup_after_test=True,
    ))


@transaction.atomic
def bind_node(*, resource: SourceResource, node_id: int) -> dict:
    node = Node.objects.filter(
        id=node_id,
        organization_id=resource.organization_id,
        is_deleted=False,
    ).first()
    if node is None:
        return {"success": False, "message": "Node not found."}
    try:
        validate_bound_node_role(resource_type=resource.resource_type, node=node)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    if node.availability != Node.Availability.ONLINE:
        return {"success": False, "message": f'Node "{node.name}" is not online.'}
    old_bound_node_id = resource.bound_node_id
    resource.bound_node = node
    _cancel_active_connection_probe(resource)
    resource.save(
        update_fields=[
            "bound_node",
            "connection_test_status",
            "connection_probe_token",
            "updated_at",
        ]
    )
    if resource.resource_type == ResourceType.NAS:
        sync_pipeline_projection(
            organization_id=resource.organization_id,
            source_kind=SelectableSourceKind.NAS,
            ref_id=resource.id,
        )
    if resource.resource_type in ResourceType.REQUIRES_MOUNT:
        _queue_remount_after_proxy_binding(
            resource=resource,
            old_node_id=old_bound_node_id,
        )
    return {
        "success": True,
        "message": f'Resource bound to node "{node.name}".',
        "bound_node": {"id": node.id, "name": node.name, "status": node.status},
    }


def unbind_node(*, resource: SourceResource) -> dict:
    from apps.source.constants import ResourceType as _RT
    if resource.resource_type in (_RT.NAS, _RT.NFS, _RT.CIFS):
        return {
            "success": False,
            "message": "Cannot unbind proxy. Replace the proxy instead.",
        }
    resource.bound_node = None
    resource.mount_status = "unmounted"
    resource.mount_point = ""
    resource.mount_error = ""
    _cancel_active_connection_probe(resource)
    resource.save(
        update_fields=[
            "bound_node",
            "mount_status",
            "mount_point",
            "mount_error",
            "connection_test_status",
            "connection_probe_token",
            "updated_at",
        ]
    )
    return {"success": True, "message": "Resource unbound."}


def resource_statistics(*, organization_id: int) -> dict:
    qs = source_resources_queryset(organization_id=organization_id)
    by_type = {}
    for code, _ in SourceResource._meta.get_field("resource_type").choices:
        by_type[code] = qs.filter(resource_type=code).count()
    agg = SourceResource.objects.filter(
        organization_id=organization_id,
        is_deleted=False,
    ).aggregate(total_size=Sum("total_size"), total_files=Sum("file_count"))
    return {
        "total": qs.count(),
        "active": qs.filter(status="active").count(),
        "inactive": qs.filter(status="inactive").count(),
        "error": qs.filter(status="error").count(),
        "mounted": qs.filter(mount_status="mounted").count(),
        "by_type": by_type,
        "total_size": int(agg["total_size"] or 0),
        "total_files": int(agg["total_files"] or 0),
    }


def production_source_summary(*, organization_id: int) -> dict:
    """Return the Dashboard's canonical Agent + Source NAS inventory summary."""
    agent_qs = Node.objects.filter(
        organization_id=organization_id,
        role=NodeRole.AGENT,
    )
    nas_qs = SourceResource.objects.filter(
        organization_id=organization_id,
        resource_type=ResourceType.NAS,
    )

    hosts_total = agent_qs.count()
    hosts_available = agent_qs.filter(availability="online").count()
    nas_total = nas_qs.count()
    nas_available = nas_qs.filter(availability="online").count()
    total = hosts_total + nas_total
    available = hosts_available + nas_available

    return {
        "total": total,
        "available": available,
        "unavailable": total - available,
        "hosts": {
            "total": hosts_total,
            "available": hosts_available,
            "unavailable": hosts_total - hosts_available,
        },
        "nas": {
            "total": nas_total,
            "available": nas_available,
            "unavailable": nas_total - nas_available,
        },
    }


def get_resource(*, organization_id: int, resource_id: int) -> SourceResource | None:
    return source_resource_by_id(organization_id=organization_id, resource_id=resource_id)


def mount_resource(*, resource: SourceResource) -> dict:
    return _mount_resource(resource)


def unmount_resource(*, resource: SourceResource, force: bool = False) -> dict:
    return _unmount_resource(resource, force=force)
