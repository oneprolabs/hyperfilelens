"""Asynchronous Source NAS connection and capacity discovery."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import uuid4

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.node.models import Node
from apps.source.constants import (
    Availability,
    ConnectionTestStatus,
    ResourceStatus,
    ResourceType,
)
from apps.source import conf as source_conf
from apps.source.models import SourceResource
from apps.source.services.internal.connection import (
    apply_connection_test_result_if_current,
    best_effort_unmount_on_proxy,
    run_connection_test,
)

logger = logging.getLogger(__name__)

SOURCE_REMOTE_IO_QUEUE = "source.remote-io"
_PROBE_MAX_RETRIES = 2
_PROBE_STALE_SECONDS = source_conf.AVAILABILITY_VALIDITY_SECONDS


def _probe_target(
    *,
    resource_id: int,
    probe_token: str,
    expected_bound_node_id: int,
) -> tuple[SourceResource | None, str]:
    resource = SourceResource.all_objects.filter(pk=resource_id).first()
    if resource is None or resource.is_deleted:
        return None, "source_deleted"
    if resource.status in ResourceStatus.REMOVAL_FENCED:
        return None, "source_removing"
    if resource.resource_type not in ResourceType.REQUIRES_MOUNT:
        return None, "mount_not_required"
    if int(resource.bound_node_id or 0) != int(expected_bound_node_id or 0):
        return None, "proxy_binding_changed"
    if str(resource.connection_probe_token or "") != str(probe_token or ""):
        return None, "source_changed"
    return resource, ""


def run_source_resource_capacity_probe(
    *,
    resource_id: int,
    probe_token: str,
    expected_bound_node_id: int,
) -> dict:
    """Run one probe and discard its result if the source changes meanwhile."""
    resource, skip_reason = _probe_target(
        resource_id=resource_id,
        probe_token=probe_token,
        expected_bound_node_id=expected_bound_node_id,
    )
    if resource is None:
        return {"status": "skipped", "reason": skip_reason}

    node = resource.bound_node
    if node is None or node.availability != Node.Availability.ONLINE:
        resource, skip_reason = apply_connection_test_result_if_current(
            resource_id=resource_id,
            probe_token=probe_token,
            expected_bound_node_id=expected_bound_node_id,
            require_mount=True,
            result={
                "success": False,
                "message": "Automatic connection test skipped because the Proxy is offline.",
            },
        )
        if resource is None:
            return {"status": "discarded", "reason": skip_reason}
        return {"status": "failed", "reason": "proxy_offline"}

    claimed = SourceResource.all_objects.filter(
        pk=resource.id,
        connection_probe_token=probe_token,
        is_deleted=False,
    ).exclude(status__in=ResourceStatus.REMOVAL_FENCED).update(
        connection_test_status=ConnectionTestStatus.RUNNING,
        status=ResourceStatus.PROBING,
        updated_at=timezone.now(),
    )
    if not claimed:
        _, skip_reason = _probe_target(
            resource_id=resource_id,
            probe_token=probe_token,
            expected_bound_node_id=expected_bound_node_id,
        )
        return {"status": "skipped", "reason": skip_reason or "source_changed"}

    probe_resource = resource
    result = run_connection_test(resource=probe_resource)

    # The remote call may wait for up to 180 seconds. Lock and re-read the row
    # before applying the result so an edit, rebind, or delete wins the race.
    resource, skip_reason = apply_connection_test_result_if_current(
        resource_id=resource_id,
        probe_token=probe_token,
        expected_bound_node_id=expected_bound_node_id,
        require_mount=True,
        result=result,
    )
    if resource is None:
        if skip_reason in {
            "source_deleted",
            "source_removing",
        }:
            best_effort_unmount_on_proxy(
                resource=probe_resource,
                node_id=expected_bound_node_id,
                force=True,
            )
        return {"status": "discarded", "reason": skip_reason}
    return {
        "status": "success" if result.get("success") else "failed",
        "resource_id": resource.id,
        "message": str(result.get("message") or result.get("error") or ""),
    }


def _record_terminal_probe_failure(
    *,
    resource_id: int,
    probe_token: str,
    expected_bound_node_id: int,
    error: Exception,
) -> dict:
    resource, skip_reason = _probe_target(
        resource_id=resource_id,
        probe_token=probe_token,
        expected_bound_node_id=expected_bound_node_id,
    )
    if resource is None:
        return {"status": "discarded", "reason": skip_reason}
    message = "Automatic Source NAS connection test failed. Retry Test Connection."
    resource, skip_reason = apply_connection_test_result_if_current(
        resource_id=resource_id,
        probe_token=probe_token,
        expected_bound_node_id=expected_bound_node_id,
        require_mount=True,
        result={"success": False, "message": message},
    )
    if resource is None:
        return {"status": "discarded", "reason": skip_reason}
    logger.error(
        "source capacity probe exhausted retries resource_id=%s error=%s",
        resource_id,
        error,
    )
    return {
        "status": "failed",
        "resource_id": resource_id,
        "message": message,
    }


@shared_task(
    name="apps.source.tasks.connection_probe.probe_source_resource_capacity",
    bind=True,
    max_retries=_PROBE_MAX_RETRIES,
)
def probe_source_resource_capacity(
    self,
    *,
    resource_id: int,
    probe_token: str,
    expected_bound_node_id: int,
) -> dict:
    """Run a Source NAS probe without holding up the create API request."""
    try:
        return run_source_resource_capacity_probe(
            resource_id=int(resource_id),
            probe_token=str(probe_token),
            expected_bound_node_id=int(expected_bound_node_id or 0),
        )
    except Exception as exc:
        retries = int(getattr(self.request, "retries", 0) or 0)
        if retries < _PROBE_MAX_RETRIES:
            raise self.retry(exc=exc, countdown=5 * (2**retries))
        return _record_terminal_probe_failure(
            resource_id=int(resource_id),
            probe_token=str(probe_token),
            expected_bound_node_id=int(expected_bound_node_id or 0),
            error=exc,
        )


def queue_source_resource_capacity_probe(
    *,
    resource_id: int,
    probe_token: str,
    expected_bound_node_id: int,
) -> bool:
    """Best-effort enqueue that cannot turn a committed create into an API 500."""
    try:
        probe_source_resource_capacity.apply_async(
            kwargs={
                "resource_id": int(resource_id),
                "probe_token": str(probe_token),
                "expected_bound_node_id": int(expected_bound_node_id or 0),
            },
            queue=SOURCE_REMOTE_IO_QUEUE,
        )
    except Exception:
        logger.exception(
            "source capacity probe enqueue failed resource_id=%s",
            resource_id,
        )
        SourceResource.all_objects.filter(
            pk=resource_id,
            is_deleted=False,
            connection_probe_token=probe_token,
        ).update(
            connection_test_status=ConnectionTestStatus.FAILED,
            connection_probe_token=None,
            status=ResourceStatus.ERROR,
            status_message=(
                "Automatic connection test could not be queued. Retry Test Connection."
            ),
            connection_test_result=(
                "Automatic connection test could not be queued. Retry Test Connection."
            ),
            updated_at=timezone.now(),
        )
        return False
    return True


def _queue_availability_probe(
    *,
    resource_id: int,
    force: bool = False,
    expected_updated_at: datetime | None = None,
) -> bool:
    """Claim and enqueue one availability refresh without overlapping probes."""
    with transaction.atomic():
        resource = (
            SourceResource.objects.select_for_update()
            .filter(
                pk=resource_id,
                resource_type__in=ResourceType.REQUIRES_MOUNT,
                is_deleted=False,
            )
            .first()
        )
        if resource is None:
            return False
        if resource.status in ResourceStatus.REMOVAL_FENCED:
            return False
        if resource.connection_test_status in ConnectionTestStatus.ACTIVE:
            return False
        if (
            expected_updated_at is not None
            and resource.availability_updated_at != expected_updated_at
        ):
            return False
        if (
            resource.bound_node is None
            or resource.bound_node.availability != Node.Availability.ONLINE
        ):
            return False
        refresh_cutoff = timezone.now() - timedelta(
            seconds=max(1, source_conf.AVAILABILITY_VALIDITY_SECONDS // 2)
        )
        if not force and resource.availability_updated_at > refresh_cutoff:
            return False

        probe_token = uuid4()
        resource.connection_test_status = ConnectionTestStatus.PENDING
        resource.status = ResourceStatus.PROBING
        resource.connection_probe_token = probe_token
        resource.save(
            update_fields=[
                "connection_test_status",
                "status",
                "connection_probe_token",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda: queue_source_resource_capacity_probe(
                resource_id=resource.id,
                probe_token=str(probe_token),
                expected_bound_node_id=int(resource.bound_node_id or 0),
            )
        )
        return True


def queue_source_availability_probes_for_proxy(
    *,
    proxy_id: int,
    limit: int | None = None,
) -> dict[str, int]:
    """Queue a bounded fresh NAS observation after a Proxy becomes available."""
    batch_size = max(
        1,
        int(limit or source_conf.AVAILABILITY_RECONCILE_BATCH_SIZE),
    )
    resource_ids = list(
        SourceResource.objects.filter(
            bound_node_id=proxy_id,
            resource_type__in=ResourceType.REQUIRES_MOUNT,
            is_deleted=False,
        )
        .exclude(connection_test_status__in=ConnectionTestStatus.ACTIVE)
        .exclude(status__in=ResourceStatus.REMOVAL_FENCED)
        .order_by("availability_updated_at", "id")
        .values_list("id", flat=True)[:batch_size]
    )
    queued = sum(
        1
        for resource_id in resource_ids
        if _queue_availability_probe(resource_id=resource_id, force=True)
    )
    return {"candidates": len(resource_ids), "queued": queued}


@shared_task(
    name=(
        "apps.source.tasks.connection_probe."
        "queue_source_availability_probes_for_proxy_task"
    )
)
def queue_source_availability_probes_for_proxy_task(
    *,
    proxy_id: int,
    limit: int | None = None,
) -> dict[str, int]:
    """Queue bounded NAS refreshes outside the Proxy heartbeat request."""
    return queue_source_availability_probes_for_proxy(
        proxy_id=int(proxy_id),
        limit=limit,
    )


def reconcile_source_availability(*, limit: int | None = None) -> dict[str, int]:
    """Expire stale NAS observations and queue bounded pre-refresh probes."""
    now = timezone.now()
    validity_seconds = max(2, source_conf.AVAILABILITY_VALIDITY_SECONDS)
    refresh_cutoff = now - timedelta(seconds=validity_seconds // 2)
    expiry_cutoff = now - timedelta(seconds=validity_seconds)
    batch_size = max(
        1,
        int(limit or source_conf.AVAILABILITY_RECONCILE_BATCH_SIZE),
    )
    resources = list(
        SourceResource.objects.select_related("bound_node")
        .filter(
            resource_type__in=ResourceType.REQUIRES_MOUNT,
            is_deleted=False,
        )
        .exclude(status__in=ResourceStatus.REMOVAL_FENCED)
        .filter(
            Q(
                availability_updated_at__lte=refresh_cutoff,
                bound_node__availability=Node.Availability.ONLINE,
            )
            | Q(
                availability=Availability.ONLINE,
            )
            & (
                Q(bound_node__isnull=True)
                | Q(bound_node__availability=Node.Availability.OFFLINE)
            )
        )
        .order_by("availability_updated_at", "id")[:batch_size]
    )
    expired = 0
    proxy_offline = 0
    queued = 0
    for resource in resources:
        node = resource.bound_node
        if node is None or node.availability != Node.Availability.ONLINE:
            observed_at = (
                node.availability_updated_at
                if node is not None
                else now
            )
            changed = SourceResource.objects.filter(
                pk=resource.id,
                availability=resource.availability,
                availability_updated_at=resource.availability_updated_at,
            ).update(
                availability=Availability.OFFLINE,
                availability_updated_at=observed_at,
            )
            proxy_offline += int(changed)
            continue

        due_for_refresh = resource.availability_updated_at <= refresh_cutoff
        expected_updated_at = resource.availability_updated_at
        if resource.availability_updated_at <= expiry_cutoff:
            changed = SourceResource.objects.filter(
                pk=resource.id,
                availability_updated_at=resource.availability_updated_at,
            ).update(
                availability=Availability.OFFLINE,
                availability_updated_at=now,
            )
            if not changed:
                continue
            expected_updated_at = now
            expired += int(changed)
        if due_for_refresh and _queue_availability_probe(
            resource_id=resource.id,
            force=True,
            expected_updated_at=expected_updated_at,
        ):
            queued += 1
    return {
        "candidates": len(resources),
        "expired": expired,
        "proxy_offline": proxy_offline,
        "queued": queued,
    }


@shared_task(
    name=(
        "apps.source.tasks.connection_probe."
        "reconcile_source_availability_task"
    )
)
def reconcile_source_availability_task(*, limit: int = 100) -> dict[str, int]:
    return reconcile_source_availability(limit=int(limit))


def reconcile_stale_source_connection_probes(*, limit: int = 100) -> dict[str, int]:
    """Fail probes that can no longer be owned by a live Celery execution."""
    cutoff = timezone.now() - timedelta(seconds=_PROBE_STALE_SECONDS)
    stale_ids = list(
        SourceResource.all_objects.filter(
            is_deleted=False,
            connection_test_status__in=ConnectionTestStatus.ACTIVE,
            updated_at__lt=cutoff,
        )
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    if not stale_ids:
        return {"stale": 0, "failed": 0}
    message = "Automatic connection test timed out. Retry Test Connection."
    failed = SourceResource.all_objects.filter(
        id__in=stale_ids,
        is_deleted=False,
        connection_test_status__in=ConnectionTestStatus.ACTIVE,
        updated_at__lt=cutoff,
    ).update(
        connection_test_status=ConnectionTestStatus.FAILED,
        connection_probe_token=None,
        status=ResourceStatus.ERROR,
        status_message=message,
        connection_test_result=message,
        updated_at=timezone.now(),
    )
    if failed:
        logger.warning("reconciled stale source capacity probes count=%s", failed)
    return {"stale": len(stale_ids), "failed": int(failed)}


@shared_task(
    name=(
        "apps.source.tasks.connection_probe."
        "reconcile_stale_source_connection_probes_task"
    )
)
def reconcile_stale_source_connection_probes_task(*, limit: int = 100) -> dict[str, int]:
    return reconcile_stale_source_connection_probes(limit=int(limit))


__all__ = [
    "probe_source_resource_capacity",
    "queue_source_resource_capacity_probe",
    "queue_source_availability_probes_for_proxy",
    "queue_source_availability_probes_for_proxy_task",
    "reconcile_source_availability",
    "reconcile_source_availability_task",
    "reconcile_stale_source_connection_probes",
    "reconcile_stale_source_connection_probes_task",
    "run_source_resource_capacity_probe",
]
