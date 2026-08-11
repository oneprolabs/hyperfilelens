from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.task.models import Task, TaskEvent, TaskStep


def list_tasks(
    *,
    organization_id: int,
    status: str | None = None,
    task_type: str | None = None,
    trigger_type: str | None = None,
    resource_type: str | None = None,
    resource_subtype: str | None = None,
    resource_id: int | None = None,
    search: str | None = None,
    search_field: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> QuerySet[Task]:
    queryset = (
        Task.objects.filter(organization_id=organization_id)
        .select_related(
            "replaces_task",
            "replacement_task",
            "repository_operation__execution_target",
            "repository_operation__triggered_by_task",
        )
        .prefetch_related("resources", "dependencies__blocking_task")
        .order_by("-created_at", "-id")
    )
    if status:
        queryset = queryset.filter(status=status)
    if task_type:
        queryset = queryset.filter(task_type=task_type)
    if trigger_type:
        queryset = queryset.filter(trigger_type=trigger_type)
    if resource_type:
        resource_filter = Q(resources__resource_type=resource_type)
        if resource_subtype:
            resource_filter &= Q(resources__resource_subtype=resource_subtype)
        if resource_id is not None:
            resource_filter &= Q(resources__resource_id=resource_id)
        queryset = queryset.filter(resource_filter).distinct()
    if search:
        text = search.strip()
        if text:
            if search_field == "name":
                search_filter = Q(display_name__icontains=text)
            elif search_field == "uuid":
                try:
                    search_filter = Q(task_uuid=UUID(text))
                except ValueError:
                    search_filter = Q(pk__in=[])
            else:
                search_filter = Q(display_name__icontains=text) | Q(error_code__icontains=text)
                try:
                    search_filter |= Q(task_uuid=UUID(text))
                except ValueError:
                    pass
            queryset = queryset.filter(search_filter)
    if created_after:
        queryset = queryset.filter(created_at__gte=created_after)
    if created_before:
        queryset = queryset.filter(created_at__lte=created_before)
    return queryset


def get_task(*, organization_id: int, task_uuid: UUID | str) -> Task | None:
    return (
        Task.objects.filter(organization_id=organization_id, task_uuid=task_uuid)
        .select_related(
            "replaces_task",
            "replacement_task",
            "repository_operation__execution_target",
            "repository_operation__triggered_by_task",
        )
        .prefetch_related(
            "resources",
            "steps",
            "events",
            "dependencies__blocking_task",
        )
        .first()
    )


def list_task_steps(*, task: Task) -> QuerySet[TaskStep]:
    return task.steps.order_by("step_index", "id")


def list_task_events(*, task: Task, level: str | None = None, after_seq: int | None = None) -> QuerySet[TaskEvent]:
    queryset = task.events.order_by("seq", "id")
    if level:
        queryset = queryset.filter(level=level)
    if after_seq is not None:
        queryset = queryset.filter(seq__gt=after_seq)
    return queryset


def task_statistics(*, organization_id: int, queryset: QuerySet[Task] | None = None) -> dict:
    queryset = queryset if queryset is not None else Task.objects.filter(organization_id=organization_id)
    counts = {
        row["status"]: row["count"]
        for row in queryset.order_by().values("status").annotate(count=Count("id"))
    }
    by_type = {
        row["task_type"]: row["count"]
        for row in queryset.order_by().values("task_type").annotate(count=Count("id"))
    }
    return {
        "total": sum(counts.values()),
        "running": counts.get(Task.Status.RUNNING, 0),
        "waiting": counts.get(Task.Status.WAITING, 0),
        "blocked": counts.get(Task.Status.BLOCKED, 0),
        "success": counts.get(Task.Status.SUCCESS, 0),
        "failed": counts.get(Task.Status.FAILED, 0),
        "cancelled": counts.get(Task.Status.CANCELLED, 0),
        "timeout": counts.get(Task.Status.TIMEOUT, 0),
        "by_status": counts,
        "by_task_type": by_type,
    }


def platform_task_counts(*, since: datetime) -> dict:
    failed_since = Task.objects.filter(
        status__in=[Task.Status.FAILED, Task.Status.TIMEOUT],
        finished_at__gte=since,
    ).count()
    return {
        "running": Task.objects.filter(status=Task.Status.RUNNING).count(),
        "failed_24h": failed_since,
        "failed_total": Task.objects.filter(
            status__in=[Task.Status.FAILED, Task.Status.TIMEOUT]
        ).count(),
    }


def recent_failed_tasks(*, limit: int = 10) -> QuerySet[Task]:
    fallback_since = timezone.now() - timedelta(days=30)
    return Task.objects.filter(
        status__in=[Task.Status.FAILED, Task.Status.TIMEOUT],
        created_at__gte=fallback_since,
    ).order_by("-finished_at", "-updated_at", "-id")[:limit]


def task_resource_options(*, organization_id: int, limit: int = 300) -> list[dict]:
    rows = Task.objects.filter(organization_id=organization_id).order_by("-created_at", "-id")[:limit]
    return [
        {
            "id": str(row.task_uuid),
            "name": f"{row.task_type} / {row.display_name}",
            "status": row.status,
        }
        for row in rows
    ]
