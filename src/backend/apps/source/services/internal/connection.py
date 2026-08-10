"""Connection test and mount helpers via bound Proxy agent tasks."""

from __future__ import annotations

import logging
import threading
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.node.models import Node
from apps.source.constants import (
    ConnectionTestStatus,
    MountStatus,
    ResourceStatus,
    ResourceType,
)
from apps.source.models import SourceResource
from apps.source.services.internal.nas_agent import (
    _task_error_message,
    task_error_code,
    _validate_proxy_node,
    apply_mount_failure,
    apply_mount_success,
    apply_unmount_success,
    build_nas_agent_payload,
    dispatch_nas_agent_task,
    explain_nas_mount_point_error,
)
from apps.source.services.internal.availability import (
    apply_result_availability,
    confirmed_agent_failure,
    result_with_availability_observation,
)

logger = logging.getLogger(__name__)

SMB_CHARSET_UNAVAILABLE = "SMB_CHARSET_UNAVAILABLE"


def _uses_utf8_iocharset(options: object) -> bool:
    for item in str(options or "").split(","):
        key, separator, value = item.partition("=")
        if (
            separator
            and key.strip().lower() == "iocharset"
            and value.strip().lower() == "utf8"
        ):
            return True
    return False


def _is_unstructured_smb_charset_failure(*, payload: dict, message: str) -> bool:
    if str(payload.get("protocol") or "").strip().lower() != "smb":
        return False
    if not _uses_utf8_iocharset(payload.get("options")):
        return False

    normalized = str(message or "").strip().lower()
    return any(
        signature in normalized
        for signature in (
            "mount error(79)",
            "needed shared library",
            "unable to load nls charset",
        )
    ) or (
        "iocharset" in normalized
        and "utf8" in normalized
        and "not found" in normalized
    )


def _requires_nas_agent(resource_type: str) -> bool:
    return resource_type in ResourceType.REQUIRES_MOUNT


def run_connection_test(
    *,
    resource: SourceResource | None = None,
    bound_node: Node | None = None,
    resource_type: str = "",
    config: dict | None = None,
    credentials: dict | None = None,
    mount_point_override: str = "",
    cleanup_after_test: bool = False,
) -> dict:
    node = bound_node or (resource.bound_node if resource else None)
    rtype = resource_type or (resource.resource_type if resource else "")

    if node is None:
        return {"success": False, "message": "No bound node configured. Please bind a node first."}
    if node.availability != Node.Availability.ONLINE:
        return {
            "success": False,
            "message": f'Bound node "{node.name}" is not online.',
        }

    if not _requires_nas_agent(rtype):
        return {
            "success": True,
            "message": "Connection test successful",
            "details": {"storage_type": rtype},
        }

    validation_error = _validate_proxy_node(node, resource=resource)
    if validation_error:
        return {"success": False, "message": validation_error}

    payload = build_nas_agent_payload(
        resource=resource,
        resource_type=rtype,
        config=config,
        credentials=credentials,
    )

    if mount_point_override:
        payload["mount_point"] = mount_point_override
    if cleanup_after_test:
        payload["cleanup_after_test"] = True
    logger.info(
        "source connection test start node_id=%s resource_id=%s protocol=%s server=%s",
        node.id,
        resource.id if resource else payload.get("resource_id"),
        payload.get("protocol"),
        payload.get("server"),
    )
    outcome = dispatch_nas_agent_task(
        node=node,
        kind="nas.test",
        payload=payload,
        correlation_type="source.connection_test",
        correlation_id=str(resource.id if resource else payload.get("resource_id") or node.id),
        wait_timeout_seconds=180,
    )
    if outcome.timed_out:
        logger.warning(
            "source connection test timed out node_id=%s resource_id=%s",
            node.id,
            resource.id if resource else payload.get("resource_id"),
        )
        return {"success": False, "message": "Connection test timed out on the proxy agent."}
    if not outcome.ok:
        message = explain_nas_mount_point_error(
            resource=resource,
            agent_message=_task_error_message(outcome),
            payload_mount_point=str(payload.get("mount_point") or ""),
        )
        logger.warning(
            "source connection test failed node_id=%s resource_id=%s error=%s",
            node.id,
            resource.id if resource else payload.get("resource_id"),
            message[:500],
        )
        result = outcome.result if isinstance(outcome.result, dict) else {}
        details = {"storage_type": rtype, "protocol": payload.get("protocol")}
        for key in ("charset", "kernel", "cleanup_status", "mount_status"):
            if key in result:
                details[key] = result[key]
        failure = {
            "success": False,
            "message": message,
            "details": details,
        }
        error_code = task_error_code(outcome)
        if not error_code and _is_unstructured_smb_charset_failure(
            payload=payload,
            message=message,
        ):
            error_code = SMB_CHARSET_UNAVAILABLE
            details["charset"] = "utf8"
        if error_code:
            failure["error_code"] = error_code
        if confirmed_agent_failure(outcome):
            return result_with_availability_observation(failure, "offline")
        return failure

    result = outcome.result if isinstance(outcome.result, dict) else {}
    space = result.get("space_info") if isinstance(result.get("space_info"), dict) else {}
    details = {
        "storage_type": rtype,
        "protocol": payload.get("protocol"),
        "mount_point": result.get("mount_point") or payload.get("mount_point"),
        "space_info": space,
    }
    logger.info(
        "source connection test ok node_id=%s resource_id=%s mount_point=%s",
        node.id,
        resource.id if resource else payload.get("resource_id"),
        details.get("mount_point"),
    )
    return result_with_availability_observation({
        "success": True,
        "message": "Connection test successful",
        "details": details,
    }, "online")


def apply_connection_test_result(resource: SourceResource, result: dict) -> None:
    resource.last_connection_test = timezone.now()
    resource.connection_test_result = result.get("message") or result.get("error") or ""
    resource.status = (
        ResourceStatus.ACTIVE if result.get("success") else ResourceStatus.ERROR
    )
    resource.status_message = resource.connection_test_result
    resource.connection_test_status = (
        ConnectionTestStatus.SUCCESS
        if result.get("success")
        else ConnectionTestStatus.FAILED
    )
    resource.connection_probe_token = None

    details = result.get("details") or {}
    space = details.get("space_info") or {}
    if space:
        resource.total_size = int(space.get("total_bytes") or 0)
        resource.used_size = int(space.get("used_bytes") or 0)
        resource.free_size = int(space.get("free_bytes") or 0)
    if "object_count" in details:
        resource.file_count = int(details.get("object_count") or 0)
    if result.get("success") and resource.requires_mount:
        mount_point = resource.effective_mount_point()
        resource.mount_status = MountStatus.MOUNTED
        resource.mount_point = mount_point
        resource.mount_error = ""
    apply_result_availability(resource=resource, result=result)
    resource.save()
    if resource.resource_type == ResourceType.NAS:
        from apps.source.services.internal.source_pipeline import sync_pipeline_projection

        sync_pipeline_projection(
            organization_id=resource.organization_id,
            source_kind="nas",
            ref_id=resource.id,
        )


def apply_connection_test_result_if_current(
    *,
    resource_id: int,
    probe_token: UUID | str,
    result: dict,
    expected_bound_node_id: int | None = None,
    require_mount: bool = False,
) -> tuple[SourceResource | None, str]:
    """Apply a probe result only while it still owns the source revision."""
    with transaction.atomic():
        resource = (
            SourceResource.all_objects.select_for_update()
            .filter(pk=resource_id)
            .first()
        )
        if resource is None or resource.is_deleted:
            return None, "source_deleted"
        if resource.status in {ResourceStatus.REMOVING, ResourceStatus.REMOVED}:
            return None, "source_removing"
        if require_mount and not resource.requires_mount:
            return None, "mount_not_required"
        if (
            expected_bound_node_id is not None
            and int(resource.bound_node_id or 0)
            != int(expected_bound_node_id or 0)
        ):
            return None, "proxy_binding_changed"
        if str(resource.connection_probe_token or "") != str(probe_token or ""):
            return None, "source_changed"
        apply_connection_test_result(resource, result)
        return resource, ""


def mount_resource(resource: SourceResource) -> dict:
    if not resource.requires_mount:
        return {
            "success": False,
            "message": f"{resource.resource_type} does not require mounting.",
        }
    validation_error = _validate_proxy_node(resource.bound_node, resource=resource)
    if validation_error:
        return {"success": False, "message": validation_error}

    payload = build_nas_agent_payload(resource=resource)
    logger.info(
        "source mount start node_id=%s resource_id=%s protocol=%s server=%s",
        resource.bound_node_id,
        resource.id,
        payload.get("protocol"),
        payload.get("server"),
    )
    outcome = dispatch_nas_agent_task(
        node=resource.bound_node,
        kind="nas.mount",
        payload=payload,
        correlation_type="source.mount",
        correlation_id=str(resource.id),
        wait_timeout_seconds=180,
    )
    if outcome.timed_out:
        message = "Mount timed out on the proxy agent."
        apply_mount_failure(resource, message, availability_confirmed=False)
        return {"success": False, "message": message}
    if not outcome.ok:
        message = explain_nas_mount_point_error(
            resource=resource,
            agent_message=_task_error_message(outcome),
            payload_mount_point=str(payload.get("mount_point") or ""),
        )
        apply_mount_failure(
            resource,
            message,
            availability_confirmed=confirmed_agent_failure(outcome),
        )
        result = {"success": False, "message": message}
        if error_code := task_error_code(outcome):
            result["error_code"] = error_code
        return result

    result = outcome.result if isinstance(outcome.result, dict) else {}
    apply_mount_success(resource, result)
    logger.info(
        "source mount ok node_id=%s resource_id=%s mount_point=%s",
        resource.bound_node_id,
        resource.id,
        resource.mount_point,
    )
    return {
        "success": True,
        "message": "Resource mounted successfully",
        "mount_point": resource.mount_point,
    }


def best_effort_unmount_on_proxy(*, resource: SourceResource, node_id: int) -> None:
    """Best-effort NAS unmount on a proxy that no longer owns the source binding."""

    if not resource.requires_mount:
        return
    node = Node.objects.filter(
        id=node_id,
        organization_id=resource.organization_id,
        is_deleted=False,
    ).first()
    if node is None:
        return

    payload = build_nas_agent_payload(resource=resource)
    try:
        outcome = dispatch_nas_agent_task(
            node=node,
            kind="nas.unmount",
            payload=payload,
            correlation_type="source.unmount",
            correlation_id=str(resource.id),
            wait_timeout_seconds=60,
        )
        if outcome.timed_out or not outcome.ok:
            logger.warning(
                "source NAS unmount on old proxy failed resource_id=%s node_id=%s error=%s",
                resource.id,
                node_id,
                _task_error_message(outcome),
            )
    except Exception:
        logger.warning(
            "source NAS unmount on old proxy failed resource_id=%s node_id=%s",
            resource.id,
            node_id,
            exc_info=True,
        )


def remount_after_proxy_change(
    *,
    resource: SourceResource,
    old_node_id: int | None,
) -> dict:
    """Mount the NAS share on the newly bound proxy after a proxy replacement."""

    if not resource.requires_mount:
        return {"success": True, "message": "No mount required."}
    if resource.bound_node is None:
        return {"success": False, "message": "No bound node configured. Please bind a proxy node first."}

    resource.mount_status = MountStatus.UNMOUNTED
    resource.mount_point = ""
    resource.mount_error = ""
    resource.save(
        update_fields=["mount_status", "mount_point", "mount_error", "updated_at"]
    )

    result = mount_resource(resource)
    if not result.get("success"):
        return result

    new_node_id = resource.bound_node_id
    if old_node_id and old_node_id != new_node_id:
        transaction.on_commit(
            lambda rid=resource.id, old_id=old_node_id: _unmount_old_proxy_after_commit(
                resource_id=rid,
                old_node_id=old_id,
            )
        )
    return result


def schedule_remount_after_proxy_change(
    *,
    resource_id: int,
    old_node_id: int | None,
) -> None:
    """Remount NAS on the new proxy in a background thread after commit."""

    def _start_remount() -> None:
        def _run() -> None:
            resource = SourceResource.objects.filter(id=resource_id, is_deleted=False).first()
            if resource is None:
                return
            try:
                result = remount_after_proxy_change(
                    resource=resource,
                    old_node_id=old_node_id,
                )
                if not result.get("success"):
                    logger.warning(
                        "NAS remount after proxy change failed resource_id=%s message=%s",
                        resource_id,
                        result.get("message"),
                    )
            except Exception:
                logger.exception(
                    "NAS remount after proxy change failed resource_id=%s",
                    resource_id,
                )

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"nas-remount-{resource_id}",
        ).start()

    transaction.on_commit(_start_remount)


def _unmount_old_proxy_after_commit(*, resource_id: int, old_node_id: int) -> None:
    resource = SourceResource.objects.filter(id=resource_id, is_deleted=False).first()
    if resource is None:
        return
    best_effort_unmount_on_proxy(resource=resource, node_id=old_node_id)


def unmount_resource(resource: SourceResource) -> dict:
    validation_error = _validate_proxy_node(resource.bound_node, resource=resource)
    if validation_error:
        return {"success": False, "message": validation_error}

    payload = build_nas_agent_payload(resource=resource)
    logger.info(
        "source unmount start node_id=%s resource_id=%s mount_point=%s",
        resource.bound_node_id,
        resource.id,
        resource.mount_point or payload.get("mount_point"),
    )
    outcome = dispatch_nas_agent_task(
        node=resource.bound_node,
        kind="nas.unmount",
        payload=payload,
        correlation_type="source.unmount",
        correlation_id=str(resource.id),
        wait_timeout_seconds=60,
    )
    if outcome.timed_out:
        return {"success": False, "message": "Unmount timed out on the proxy agent."}
    if not outcome.ok:
        return {"success": False, "message": _task_error_message(outcome)}

    apply_unmount_success(resource)
    logger.info(
        "source unmount ok node_id=%s resource_id=%s",
        resource.bound_node_id,
        resource.id,
    )
    return {"success": True, "message": "Resource unmounted successfully"}
