from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from celery import Task as CeleryTask
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from common.observability.celery_context import celery_trace

if TYPE_CHECKING:
    from apps.task.models import Task

logger = logging.getLogger(__name__)

_SOURCE_UNREGISTER_LEASE_SECONDS = 5 * 60
_SOURCE_UNREGISTER_CONFLICT_RETRY_SECONDS = 3
_SOURCE_UNREGISTER_LEASE_KEY = "_advance_lease"


class SourceUnregisterCeleryTask(CeleryTask):
    """Ensure exhausted worker retries also finalize the domain task."""

    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        domain_task_id = int((kwargs or {}).get("task_id") or 0)
        if domain_task_id > 0:
            try:
                from apps.source.services.internal.backup_source_delete import (
                    fail_source_unregister_task_unexpectedly,
                )

                fail_source_unregister_task_unexpectedly(
                    task_id=domain_task_id,
                    exc=exc,
                )
            except Exception:
                logger.exception(
                    "failed to finalize exhausted source unregister task id=%s",
                    domain_task_id,
                )
        super().on_failure(exc, task_id, args, kwargs, einfo)


class SourceUnregisterLeaseLost(RuntimeError):
    """Raised when a worker no longer owns the unregister execution lease."""


@dataclass(frozen=True)
class SourceUnregisterLease:
    """Short database-backed ownership lease for one task advance."""

    acquired: bool
    task_id: int
    owner_token: str = ""
    terminal: bool = False


def _lease_expires_at(payload: dict) -> timezone.datetime | None:
    lease = payload.get(_SOURCE_UNREGISTER_LEASE_KEY)
    if not isinstance(lease, dict):
        return None
    parsed = parse_datetime(str(lease.get("expires_at") or ""))
    if parsed is None:
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


@transaction.atomic
def _acquire_source_unregister_lease(
    *,
    task_id: int,
) -> tuple[SourceUnregisterLease, Task]:
    from apps.task.models import Task

    task = Task.objects.select_for_update().filter(pk=int(task_id)).first()
    if task is None:
        raise Task.DoesNotExist(f"source unregister task id={task_id} not found")
    if task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return SourceUnregisterLease(
            acquired=False,
            task_id=int(task.id),
            terminal=True,
        ), task
    if task.status in {Task.Status.WAITING, Task.Status.BLOCKED}:
        return SourceUnregisterLease(acquired=False, task_id=int(task.id)), task

    payload = dict(task.request_payload or {})
    expires_at = _lease_expires_at(payload)
    if expires_at is not None and expires_at > timezone.now():
        return SourceUnregisterLease(acquired=False, task_id=int(task.id)), task

    owner_token = str(uuid4())
    payload[_SOURCE_UNREGISTER_LEASE_KEY] = {
        "owner_token": owner_token,
        "expires_at": (
            timezone.now() + timedelta(seconds=_SOURCE_UNREGISTER_LEASE_SECONDS)
        ).isoformat(),
    }
    task.request_payload = payload
    task.save(update_fields=["request_payload", "updated_at"])
    return SourceUnregisterLease(
        acquired=True,
        task_id=int(task.id),
        owner_token=owner_token,
    ), task


@transaction.atomic
def _release_source_unregister_lease(*, lease: SourceUnregisterLease) -> None:
    if not lease.acquired or not lease.owner_token:
        return

    from apps.task.models import Task

    task = Task.objects.select_for_update().filter(pk=lease.task_id).first()
    if task is None:
        return
    payload = dict(task.request_payload or {})
    current = payload.get(_SOURCE_UNREGISTER_LEASE_KEY)
    if not isinstance(current, dict):
        return
    if str(current.get("owner_token") or "") != lease.owner_token:
        return
    payload.pop(_SOURCE_UNREGISTER_LEASE_KEY, None)
    task.request_payload = payload
    task.save(update_fields=["request_payload", "updated_at"])


@transaction.atomic
def renew_source_unregister_lease(*, task_id: int, owner_token: str) -> bool:
    """Renew the lease only while the caller remains its fenced owner."""
    from apps.task.models import Task

    if not owner_token:
        return False
    task = Task.objects.select_for_update().filter(pk=int(task_id)).first()
    if task is None or task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return False
    payload = dict(task.request_payload or {})
    current = payload.get(_SOURCE_UNREGISTER_LEASE_KEY)
    if not isinstance(current, dict):
        return False
    if str(current.get("owner_token") or "") != owner_token:
        return False
    current["expires_at"] = (
        timezone.now() + timedelta(seconds=_SOURCE_UNREGISTER_LEASE_SECONDS)
    ).isoformat()
    payload[_SOURCE_UNREGISTER_LEASE_KEY] = current
    task.request_payload = payload
    task.save(update_fields=["request_payload", "updated_at"])
    return True


def queue_source_unregister_task(*, task_id: int, countdown_seconds: int = 0) -> None:
    """Queue one idempotent source-unregister advance."""
    from apps.source.tasks.source_unregister import execute_source_unregister_task

    execute_source_unregister_task.apply_async(
        kwargs={"task_id": int(task_id)},
        countdown=max(0, int(countdown_seconds)),
    )


@shared_task(
    name="apps.source.tasks.source_unregister.execute_source_unregister_task",
    base=SourceUnregisterCeleryTask,
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def execute_source_unregister_task(self, *, task_id: int) -> dict:
    from apps.source.services.internal.backup_source_delete import run_source_unregister_task

    lease, task = _acquire_source_unregister_lease(task_id=int(task_id))
    if lease.terminal:
        return {
            "status": str(task.status),
            "task_id": int(task.id),
            "idempotent": True,
        }
    if not lease.acquired:
        if task.status in {"waiting", "blocked"}:
            return {
                "status": str(task.status),
                "task_id": int(task.id),
                "idempotent": True,
            }
        queue_source_unregister_task(
            task_id=int(task.id),
            countdown_seconds=_SOURCE_UNREGISTER_CONFLICT_RETRY_SECONDS,
        )
        return {
            "status": "rescheduled",
            "task_id": int(task.id),
            "retry_in_seconds": _SOURCE_UNREGISTER_CONFLICT_RETRY_SECONDS,
        }

    try:
        trace_id = str(task.task_uuid or getattr(self.request, "id", "") or "")
        with celery_trace(
            trace_id,
            task_name="apps.source.tasks.source_unregister.execute_source_unregister_task",
        ):
            logger.info(
                "celery task started "
                "name=apps.source.tasks.source_unregister.execute_source_unregister_task "
                "task_id=%s task_uuid=%s org_id=%s",
                task_id,
                task.task_uuid,
                task.organization_id,
            )
            return run_source_unregister_task(
                organization_id=int(task.organization_id),
                task_uuid=str(task.task_uuid),
                lease_owner_token=lease.owner_token,
            )
    except SourceUnregisterLeaseLost:
        queue_source_unregister_task(
            task_id=int(task.id),
            countdown_seconds=_SOURCE_UNREGISTER_CONFLICT_RETRY_SECONDS,
        )
        return {
            "status": "lease_lost",
            "task_id": int(task.id),
            "retry_in_seconds": _SOURCE_UNREGISTER_CONFLICT_RETRY_SECONDS,
        }
    finally:
        _release_source_unregister_lease(lease=lease)


@shared_task(
    name="apps.source.tasks.source_unregister.reconcile_stuck_source_unregister_tasks_task",
    bind=True,
)
def reconcile_stuck_source_unregister_tasks_task(self, *, limit: int = 50) -> dict[str, int]:
    from apps.source.services.internal.backup_source_delete import reconcile_stuck_source_unregister_tasks

    summary = reconcile_stuck_source_unregister_tasks(limit=int(limit))
    if summary.get("redispatched"):
        logger.info("reconcile_stuck_source_unregister_tasks complete %s", summary)
    return summary


@shared_task(
    name="apps.source.tasks.source_unregister.reevaluate_source_unregister_task_task",
)
def reevaluate_source_unregister_task_task(*, task_id: int) -> dict:
    """Reevaluate one deferred deregistration after a dependency state change."""
    from apps.source.services.internal.backup_source_delete import (
        reevaluate_source_unregister_task,
    )

    return reevaluate_source_unregister_task(task_id=int(task_id))
