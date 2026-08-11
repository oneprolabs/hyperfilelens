"""Persist protection wizard pipeline step for real backup-selectable sources."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.source.constants import PipelineStep, ResourceType, SelectableSourceKind
from apps.source.models import SourceBackupPipelineEntry, SourceResource
from apps.source.services.internal.selectable_ids import parse_selectable_id
from apps.source.services.internal.source_pipeline_projection import build_source_projection


def _selectable_key(source_kind: str, ref_id: int) -> str:
    return f"{source_kind}:{ref_id}"


def load_pipeline_step_map(*, organization_id: int) -> dict[str, int]:
    rows = SourceBackupPipelineEntry.objects.filter(
        organization_id=organization_id,
        is_deleted=False,
    ).values("source_kind", "ref_id", "step")
    return {_selectable_key(str(row["source_kind"]), int(row["ref_id"])): int(row["step"]) for row in rows}


def attach_pipeline_steps(items: list[dict], *, pipeline_map: dict[str, int]) -> list[dict]:
    for item in items:
        item["pipeline_step"] = pipeline_map.get(str(item["id"]), PipelineStep.SOURCE_POOL)
    return items


def filter_items_by_pipeline_step(items: list[dict], step: int | None) -> list[dict]:
    if step is None or step not in PipelineStep.VALID:
        return items
    return [item for item in items if int(item.get("pipeline_step", PipelineStep.SOURCE_POOL)) == step]


def _source_exists(*, organization_id: int, source_kind: str, ref_id: int) -> bool:
    if source_kind == SelectableSourceKind.AGENT:
        return Node.objects.filter(
            organization_id=organization_id,
            role=NodeRole.AGENT,
            id=ref_id,
            is_deleted=False,
        ).exists()
    if source_kind == SelectableSourceKind.NAS:
        return SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id=ref_id,
            is_deleted=False,
        ).exists()
    return False


def _backup_config_exists(*, organization_id: int, source_kind: str, ref_id: int) -> bool:
    from apps.protection.models import BackupConfig

    source_type = "agent" if source_kind == SelectableSourceKind.AGENT else source_kind
    return BackupConfig.objects.filter(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=ref_id,
    ).exists()


def _source_and_tasks(*, organization_id: int, source_kind: str, ref_id: int):
    source = None
    if source_kind == SelectableSourceKind.AGENT:
        source = Node.objects.filter(organization_id=organization_id, role=NodeRole.AGENT, id=ref_id).first()
    elif source_kind == SelectableSourceKind.NAS:
        source = SourceResource.objects.select_related("bound_node").filter(
            organization_id=organization_id, resource_type=ResourceType.NAS, id=ref_id
        ).first()
    if source is None:
        return None, None, None
    from apps.task.models import Task, TaskResource
    source_type = "agent" if source_kind == SelectableSourceKind.AGENT else source_kind
    tasks = Task.objects.filter(
        organization_id=organization_id,
        resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
        resources__resource_subtype=source_type,
        resources__resource_id=ref_id,
    ).order_by("-created_at", "-id")
    return source, tasks.filter(task_type=Task.Type.BACKUP).first(), tasks.filter(task_type=Task.Type.RESTORE).first()


def _projection_values(*, organization_id: int, source_kind: str, ref_id: int):
    source, backup_task, restore_task = _source_and_tasks(
        organization_id=organization_id, source_kind=source_kind, ref_id=ref_id
    )
    if source is None:
        return None, None
    values, _inconsistency = build_source_projection(
        source_kind=source_kind, source=source, backup_task=backup_task, restore_task=restore_task
    )
    return source, values


def _locked_source(*, organization_id: int, source_kind: str, ref_id: int):
    """Load a source using the projection service lock order."""
    if source_kind == SelectableSourceKind.AGENT:
        return Node.objects.select_for_update().filter(
            organization_id=organization_id,
            role=NodeRole.AGENT,
            id=ref_id,
            is_deleted=False,
        ).first()
    if source_kind != SelectableSourceKind.NAS:
        return None

    binding = SourceResource.objects.filter(
        organization_id=organization_id,
        resource_type=ResourceType.NAS,
        id=ref_id,
        is_deleted=False,
    ).values("bound_node_id").first()
    if binding is None:
        return None
    if binding["bound_node_id"]:
        Node.objects.select_for_update().filter(pk=binding["bound_node_id"]).first()
    return SourceResource.objects.select_for_update().filter(
        organization_id=organization_id,
        resource_type=ResourceType.NAS,
        id=ref_id,
        is_deleted=False,
    ).first()


def sync_pipeline_projection(
    *,
    organization_id: int,
    source_kind: str,
    ref_id: int,
    minimum_step: int = PipelineStep.SOURCE_POOL,
) -> SourceBackupPipelineEntry | None:
    """Synchronize one source read-model row in the caller's transaction."""
    if minimum_step not in PipelineStep.VALID:
        raise ValueError(f"invalid pipeline step: {minimum_step}")

    with transaction.atomic():
        source = _locked_source(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
        )
        if source is None:
            return None
        entry = SourceBackupPipelineEntry.all_objects.select_for_update().filter(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
        ).first()
        from apps.task.models import Task, TaskResource

        source_type = "agent" if source_kind == SelectableSourceKind.AGENT else source_kind
        tasks = Task.objects.filter(
            organization_id=organization_id,
            resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
            resources__resource_subtype=source_type,
            resources__resource_id=ref_id,
        ).order_by("-created_at", "-id")
        values, _inconsistency = build_source_projection(
            source_kind=source_kind,
            source=source,
            backup_task=tasks.filter(task_type=Task.Type.BACKUP).first(),
            restore_task=tasks.filter(task_type=Task.Type.RESTORE).first(),
        )
        current_step = (
            int(entry.step)
            if entry is not None and not entry.is_deleted
            else PipelineStep.SOURCE_POOL
        )
        step = max(current_step, minimum_step)
        if entry is None:
            return SourceBackupPipelineEntry.objects.create(
                organization_id=organization_id,
                source_kind=source_kind,
                ref_id=ref_id,
                step=step,
                created_at=source.created_at,
                **values,
            )
        entry.step = step
        entry.is_deleted = False
        entry.deleted_at = None
        for field, value in values.items():
            setattr(entry, field, value)
        entry.save(update_fields=["step", "is_deleted", "deleted_at", *values.keys(), "updated_at"])
        return entry


def sync_bound_proxy_pipeline_projections(*, proxy_id: int, limit: int = 200) -> dict[str, int]:
    """Refresh bounded NAS projections whose displayed identity comes from a Proxy."""
    rows = list(
        SourceResource.objects.filter(
            bound_node_id=proxy_id,
            resource_type=ResourceType.NAS,
            is_deleted=False,
        ).order_by("id").values_list("organization_id", "id")[: max(1, limit)]
    )
    updated = 0
    for organization_id, ref_id in rows:
        if sync_pipeline_projection(
            organization_id=organization_id,
            source_kind=SelectableSourceKind.NAS,
            ref_id=ref_id,
        ) is not None:
            updated += 1
    return {"candidates": len(rows), "updated": updated}


def sync_task_pipeline_projection(*, task_id: int) -> int:
    """Project Backup/Restore task state without permitting an older task to win."""
    from apps.task.models import Task, TaskResource

    task = Task.objects.filter(
        id=task_id,
        task_type__in=(Task.Type.BACKUP, Task.Type.RESTORE),
    ).first()
    if task is None:
        return 0
    resources = TaskResource.objects.filter(
        task_id=task.id,
        resource_type=TaskResource.Type.BACKUP_SOURCE,
    ).values_list("resource_subtype", "resource_id")
    updated = 0
    for source_type, ref_id in resources:
        source_kind = SelectableSourceKind.AGENT if source_type in {"", "agent"} else source_type
        if source_kind not in {SelectableSourceKind.AGENT, SelectableSourceKind.NAS}:
            continue
        if sync_pipeline_projection(
            organization_id=task.organization_id,
            source_kind=source_kind,
            ref_id=int(ref_id),
        ) is not None:
            updated += 1
    return updated


def reconcile_pipeline_projections(*, limit: int = 200) -> dict[str, int]:
    """Repair missing and stale active rows in bounded source order."""
    batch_size = max(1, limit)
    candidates: list[tuple[int, str, int]] = []
    candidates.extend(
        (organization_id, SelectableSourceKind.AGENT, ref_id)
        for organization_id, ref_id in Node.objects.filter(
            role=NodeRole.AGENT,
            is_deleted=False,
        ).order_by("id").values_list("organization_id", "id")[:batch_size]
    )
    remaining = max(0, batch_size - len(candidates))
    if remaining:
        candidates.extend(
            (organization_id, SelectableSourceKind.NAS, ref_id)
            for organization_id, ref_id in SourceResource.objects.filter(
                resource_type=ResourceType.NAS,
                is_deleted=False,
            ).order_by("id").values_list("organization_id", "id")[:remaining]
        )
    repaired = sum(
        sync_pipeline_projection(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
        ) is not None
        for organization_id, source_kind, ref_id in candidates
    )
    stale = 0
    for entry in SourceBackupPipelineEntry.objects.order_by("id")[:batch_size]:
        if not _source_exists(
            organization_id=entry.organization_id,
            source_kind=entry.source_kind,
            ref_id=entry.ref_id,
        ):
            delete_pipeline_entry(
                organization_id=entry.organization_id,
                source_kind=entry.source_kind,
                ref_id=entry.ref_id,
            )
            stale += 1
    active_sources = (
        Node.objects.filter(role=NodeRole.AGENT, is_deleted=False).count()
        + SourceResource.objects.filter(
            resource_type=ResourceType.NAS,
            is_deleted=False,
        ).count()
    )
    return {
        "scanned": len(candidates),
        "repaired": repaired,
        "stale": stale,
        "active_sources": active_sources,
        "active_pipeline_rows": SourceBackupPipelineEntry.objects.count(),
    }


def _pipeline_operation_fenced(
    *,
    organization_id: int,
    source_kind: str,
    ref_id: int,
    operation_task_uuid: str | None = None,
) -> bool:
    """Return True while Reset/Unregister exclusively owns Pipeline changes."""
    from apps.task.models import Task, TaskResource

    source_type = "agent" if source_kind == SelectableSourceKind.AGENT else source_kind
    operations = Task.objects.filter(
        organization_id=organization_id,
        task_type__in={
            Task.Type.BACKUP_CONFIG_RESET,
            Task.Type.SOURCE_UNREGISTER,
        },
        status__in={
            Task.Status.PENDING,
            Task.Status.WAITING,
            Task.Status.BLOCKED,
            Task.Status.RUNNING,
        },
        resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
        resources__resource_subtype=source_type,
        resources__resource_id=ref_id,
    ).distinct()
    if operation_task_uuid:
        operations = operations.exclude(task_uuid=operation_task_uuid)
    return operations.exists()


def _upsert_pipeline_step(
    *,
    organization_id: int,
    source_kind: str,
    ref_id: int,
    step: int,
    current_step: int,
    allow_backwards: bool,
) -> str:
    key = _selectable_key(source_kind, ref_id)
    if step < current_step and not allow_backwards:
        raise ValueError(f"pipeline step cannot move backwards for {key}: {current_step} -> {step}")

    entry = SourceBackupPipelineEntry.all_objects.filter(
        organization_id=organization_id,
        source_kind=source_kind,
        ref_id=ref_id,
    ).first()
    source, values = _projection_values(
        organization_id=organization_id, source_kind=source_kind, ref_id=ref_id
    )
    if source is None:
        return key
    if entry is None:
        SourceBackupPipelineEntry.objects.create(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
            step=step,
            created_at=source.created_at,
            **values,
        )
    else:
        entry.step = step
        entry.is_deleted = False
        entry.deleted_at = None
        entry.updated_at = timezone.now()
        for field, value in values.items():
            setattr(entry, field, value)
        entry.save(update_fields=["step", "is_deleted", "deleted_at", *values.keys(), "updated_at"])
    return key


def set_pipeline_steps(*, organization_id: int, ids: list[str], step: int) -> list[str]:
    if step not in PipelineStep.VALID:
        raise ValueError(f"invalid pipeline step: {step}")

    updated: list[str] = []
    for value in ids:
        parsed = parse_selectable_id(value)
        if not parsed:
            continue
        source_kind, ref_id = parsed
        if not _source_exists(organization_id=organization_id, source_kind=source_kind, ref_id=ref_id):
            continue
        if _pipeline_operation_fenced(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
        ):
            continue
        key = _selectable_key(source_kind, ref_id)
        if step == PipelineStep.READY and not _backup_config_exists(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
        ):
            raise ValueError(f"backup config is required before moving {key} to step 3")

        entry = SourceBackupPipelineEntry.all_objects.filter(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
        ).first()
        current_step = (
            int(entry.step)
            if entry is not None and not entry.is_deleted
            else PipelineStep.SOURCE_POOL
        )
        updated.append(_upsert_pipeline_step(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
            step=step,
            current_step=current_step,
            allow_backwards=False,
        ))
    return updated


def force_set_pipeline_steps(
    *,
    organization_id: int,
    ids: list[str],
    step: int,
    operation_task_uuid: str | None = None,
) -> list[str]:
    if step not in PipelineStep.VALID:
        raise ValueError(f"invalid pipeline step: {step}")

    updated: list[str] = []
    for value in ids:
        parsed = parse_selectable_id(value)
        if not parsed:
            continue
        source_kind, ref_id = parsed
        if not _source_exists(organization_id=organization_id, source_kind=source_kind, ref_id=ref_id):
            continue
        if _pipeline_operation_fenced(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
            operation_task_uuid=operation_task_uuid,
        ):
            continue
        key = _selectable_key(source_kind, ref_id)
        if step == PipelineStep.READY and not _backup_config_exists(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
        ):
            raise ValueError(f"backup config is required before moving {key} to step 3")

        entry = SourceBackupPipelineEntry.all_objects.filter(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
        ).first()
        current_step = (
            int(entry.step)
            if entry is not None and not entry.is_deleted
            else PipelineStep.SOURCE_POOL
        )
        updated.append(_upsert_pipeline_step(
            organization_id=organization_id,
            source_kind=source_kind,
            ref_id=ref_id,
            step=step,
            current_step=current_step,
            allow_backwards=True,
        ))
    return updated


def revert_backup_flow_sources(
    *,
    organization_id: int,
    ids: list[str],
    target_step: int,
) -> list[str]:
    """Move sources back to an earlier backup wizard step."""
    if target_step not in (PipelineStep.SOURCE_POOL, PipelineStep.CONFIG):
        raise ValueError("revert target_step must be 1 or 2")

    from apps.protection.services.backup_config import purge_backup_config_data_for_source

    updated: list[str] = []
    for value in ids:
        parsed = parse_selectable_id(value)
        if not parsed:
            continue
        source_kind, ref_id = parsed
        key = _selectable_key(source_kind, ref_id)
        if not _source_exists(organization_id=organization_id, source_kind=source_kind, ref_id=ref_id):
            continue

        if target_step == PipelineStep.CONFIG:
            source_type = "agent" if source_kind == SelectableSourceKind.AGENT else source_kind
            purge_backup_config_data_for_source(
                organization_id=organization_id,
                source_type=source_type,
                source_ref_id=ref_id,
            )
            updated.append(_upsert_pipeline_step(
                organization_id=organization_id,
                source_kind=source_kind,
                ref_id=ref_id,
                step=PipelineStep.CONFIG,
                current_step=PipelineStep.READY,
                allow_backwards=True,
            ))
        else:
            _upsert_pipeline_step(
                organization_id=organization_id,
                source_kind=source_kind,
                ref_id=ref_id,
                step=PipelineStep.SOURCE_POOL,
                current_step=PipelineStep.CONFIG,
                allow_backwards=True,
            )
            updated.append(key)
    return updated


def ensure_pipeline_entry(
    *,
    organization_id: int,
    source_kind: str,
    ref_id: int,
    step: int = PipelineStep.SOURCE_POOL,
) -> SourceBackupPipelineEntry | None:
    """Create the explicit pipeline row for a source without moving it backwards."""
    if step not in PipelineStep.VALID:
        raise ValueError(f"invalid pipeline step: {step}")
    if not _source_exists(organization_id=organization_id, source_kind=source_kind, ref_id=ref_id):
        return None

    entry = SourceBackupPipelineEntry.all_objects.filter(
        organization_id=organization_id,
        source_kind=source_kind,
        ref_id=ref_id,
    ).first()
    if _pipeline_operation_fenced(
        organization_id=organization_id,
        source_kind=source_kind,
        ref_id=ref_id,
    ):
        return entry
    return sync_pipeline_projection(
        organization_id=organization_id,
        source_kind=source_kind,
        ref_id=ref_id,
        minimum_step=step,
    )


def delete_pipeline_entry(*, organization_id: int, source_kind: str, ref_id: int) -> None:
    for entry in SourceBackupPipelineEntry.all_objects.filter(
        organization_id=organization_id,
        source_kind=source_kind,
        ref_id=ref_id,
    ):
        if not entry.is_deleted:
            entry.soft_delete()


def purge_pipeline_entry(*, organization_id: int, source_kind: str, ref_id: int) -> None:
    """Hard-delete pipeline rows when the backing source identity is removed."""
    SourceBackupPipelineEntry.all_objects.filter(
        organization_id=organization_id,
        source_kind=source_kind,
        ref_id=ref_id,
    ).delete()
