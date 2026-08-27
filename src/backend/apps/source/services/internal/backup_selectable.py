"""Unified backup-selectable catalog: agent nodes + NAS source resources."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupPolicy,
    BackupSourceSnapshot,
    FileFilterRule,
)
from apps.protection.services import (
    backup_policy_related_count,
    backup_policy_retention_summary,
    backup_policy_schedule_summary,
    file_filter_related_count,
    file_filter_summary,
)
from apps.restore.models import RestorePlan, RestoreRecord
from apps.source.constants import (
    Availability,
    PipelineStep,
    PipelineTaskStatus,
    ResourceType,
    SelectableSourceKind,
)
from apps.source.metrics import (
    BACKUP_SELECTABLE_QUERY_DURATION,
    BACKUP_SELECTABLE_QUERY_REQUESTS,
    BACKUP_SELECTABLE_SHADOW_COMPARISONS,
)
from apps.source.models import SourceBackupPipelineEntry, SourceResource
from apps.source.services.internal.nas_display import nas_mount_source_uri
from apps.source.services.internal.selectable_ids import parse_selectable_id
from apps.source.services.internal.source_pipeline import (
    attach_pipeline_steps,
    filter_items_by_pipeline_step,
    load_pipeline_step_map,
)
from apps.source.services.internal.source_operation_fence import (
    product_task_is_stopping,
)
from apps.storage.models import Repository
from apps.task.models import Task, TaskResource

__all__ = [
    "fetch_backup_selectable_by_ids",
    "list_backup_selectable_sources",
    "parse_selectable_id",
]

_EXPAND_BACKUP_CONFIGS = "backup_configs"
_EXPAND_POLICIES = "policies"
_EXPAND_RUNTIME = "runtime"
_RESTORABLE_SNAPSHOT_STATUSES = (
    BackupSourceSnapshot.Status.AVAILABLE,
    BackupSourceSnapshot.Status.PARTIAL,
)
_ACTIVE_PIPELINE_TASK_STATUSES = (
    PipelineTaskStatus.QUEUED,
    PipelineTaskStatus.RUNNING,
    PipelineTaskStatus.STOPPING,
)
_SEARCH_FIELDS = {
    "source_name": "source_name",
    "source_hostname": "source_hostname",
    "source_ip": "source_ip",
}
_QUERY_MODES = {"legacy", "shadow", "pipeline"}
logger = logging.getLogger(__name__)


def _merged_inventory(node: Node) -> dict[str, Any]:
    meta = node.metadata if isinstance(node.metadata, dict) else {}
    inv = meta.get("inventory")
    if isinstance(inv, dict):
        return {**meta, **inv}
    return meta


def _nas_protocol(config: dict[str, Any]) -> str:
    explicit = str(config.get("protocol") or "").strip().lower()
    if explicit in ("smb", "nfs"):
        return explicit
    if config.get("share"):
        return "smb"
    if config.get("export_path"):
        return "nfs"
    return "nfs"


def _sort_key(item: dict[str, Any]) -> datetime:
    raw = item.get("registered_at")
    if not raw:
        return datetime.min.replace(tzinfo=timezone.get_current_timezone())
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.get_current_timezone())
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.get_current_timezone())
    return parsed


def _agent_platform(node: Node) -> str | None:
    inv = _merged_inventory(node)
    raw = (
        str(inv.get("os") or inv.get("platform") or node.os_name or "").strip().lower()
    )
    if "darwin" in raw or "mac" in raw:
        return "macos"
    if "windows" in raw or raw in {"win32", "win64"} or raw.startswith("win "):
        return "windows"
    if "linux" in raw:
        return "linux"
    return None


def _inventory_int(inv: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        raw = inv.get(key)
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _agent_item(node: Node) -> dict[str, Any]:
    inv = _merged_inventory(node)
    hostname = str(inv.get("hostname") or node.name or "").strip()
    item: dict[str, Any] = {
        "id": f"agent:{node.id}",
        "kind": "agent",
        "ref_id": node.id,
        "type": "host",
        "name": node.name,
        "hostname": hostname,
        "node_name": hostname or node.name,
        "node_ip": str(node.ip_address or "").strip(),
        "status": str(node.status or Node.Status.ACTIVE),
        "availability": str(node.availability or Node.Availability.OFFLINE),
        "installation_mode": str(
            node.installation_mode or Node.InstallationMode.SYSTEM
        ),
        "registered_at": node.created_at.isoformat() if node.created_at else None,
    }
    platform = _agent_platform(node)
    if platform:
        item["platform"] = platform
    cpu_cores = _inventory_int(inv, "cpu_cores", "cpu_logical_cores", "logical_cores")
    memory_total_bytes = _inventory_int(inv, "memory_total_bytes")
    disk_count = _inventory_int(inv, "disk_count")
    if cpu_cores is not None:
        item["cpu_cores"] = cpu_cores
    if memory_total_bytes is not None:
        item["memory_total_bytes"] = memory_total_bytes
    if disk_count is not None:
        item["disk_count"] = disk_count
    return item


def _nas_item(resource: SourceResource) -> dict[str, Any]:
    cfg = resource.config if isinstance(resource.config, dict) else {}
    node = resource.bound_node
    return {
        "id": f"nas:{resource.id}",
        "kind": "nas",
        "ref_id": resource.id,
        "type": "nas",
        "name": resource.name,
        "hostname": str(cfg.get("server") or "").strip(),
        "node_name": (node.name if node else "")
        or str(cfg.get("server") or "").strip(),
        "node_ip": str(node.ip_address or "").strip() if node else "",
        "status": str(resource.status or Availability.OFFLINE),
        "availability": str(resource.availability or Availability.OFFLINE),
        "protocol": _nas_protocol(cfg),
        "mount_options": str(cfg.get("options") or "").strip(),
        "connection_uri": nas_mount_source_uri(
            resource_type=resource.resource_type, config=cfg
        ),
        "bound_node_id": node.id if node else None,
        "mount_status": resource.mount_status,
        "mount_point": resource.mount_point,
        "registered_at": resource.created_at.isoformat()
        if resource.created_at
        else None,
    }


def _build_catalog(*, organization_id: int) -> list[dict[str, Any]]:
    agents = Node.objects.filter(
        organization_id=organization_id, role=NodeRole.AGENT, is_deleted=False
    ).order_by("-created_at", "-id")
    nas_resources = (
        SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            is_deleted=False,
        )
        .select_related("bound_node")
        .order_by("-created_at", "-id")
    )
    items = [_agent_item(node) for node in agents]
    items.extend(_nas_item(resource) for resource in nas_resources)
    items.sort(key=_sort_key, reverse=True)
    return items


def _source_key(source_type: str, source_ref_id: int) -> str:
    return f"{source_type}:{source_ref_id}"


def _configured_source_keys(*, organization_id: int) -> set[str]:
    rows = BackupConfig.objects.filter(organization_id=organization_id).values(
        "source_type", "source_ref_id"
    )
    return {
        _source_key(str(row["source_type"]), int(row["source_ref_id"])) for row in rows
    }


def _parse_expand(expand: str | None) -> set[str]:
    allowed = {_EXPAND_BACKUP_CONFIGS, _EXPAND_POLICIES, _EXPAND_RUNTIME}
    return {
        part.strip() for part in str(expand or "").split(",") if part.strip() in allowed
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _number(value: Any) -> int | float:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _config_string(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    return str(value or "").strip() if value is not None else ""


def _repository_location(repo: Repository) -> str:
    config = repo.config if isinstance(repo.config, dict) else {}
    repo_type = str(repo.repo_type or "").lower()
    if repo_type == Repository.Type.S3:
        bucket = str(
            repo.s3_bucket or _config_string(config, "bucket") or repo.name or ""
        ).strip()
        prefix = _config_string(config, "prefix").lstrip("/")
        if not bucket:
            return ""
        return f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"
    if repo_type == Repository.Type.PROXY_FS:
        return _config_string(config, "proxy_node_dir") or _config_string(
            config, "mount_path"
        )

    mount_path = _config_string(config, "mount_path")
    server_address = _config_string(config, "server_address")
    share_path = _config_string(config, "share_path")
    protocol = str(repo.nas_protocol or config.get("protocol") or "").lower()
    if mount_path:
        return mount_path
    if server_address and share_path:
        return (
            f"//{server_address}/{share_path.lstrip('/')}"
            if protocol == "smb"
            else f"{server_address}:{share_path}"
        )
    return ""


def _directory_payload(directory: BackupConfigDirectory) -> dict[str, Any]:
    return {
        "id": directory.id,
        "path": directory.path,
        "path_type": directory.path_type,
        "display_name": directory.display_name,
        "estimated_size_bytes": directory.estimated_size_bytes,
        "sort_order": directory.sort_order,
    }


def _recovery_plan_payload(plan: RestorePlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "backup_config_id": plan.backup_config_id,
        "scope": plan.scope,
        "source_type": plan.source_type,
        "source_ref_id": plan.source_ref_id,
        "backup_config_dir_id": plan.backup_config_dir_id,
        "source_path": plan.source_path,
        "target_type": plan.target_type,
        "target_ref_id": plan.target_ref_id,
        "restore_host_id": plan.target_ref_id if plan.target_type == "agent" else None,
        "restore_dir": plan.restore_dir,
        "conflict_mode": plan.conflict_mode,
        "enabled": plan.enabled,
        "sort_order": plan.sort_order,
    }


def _config_payload(
    config: BackupConfig,
    *,
    directories: list[BackupConfigDirectory],
    recovery_plans: list[RestorePlan],
) -> dict[str, Any]:
    return {
        "id": config.id,
        "name": config.name,
        "remark": config.remark,
        "source_type": config.source_type,
        "source_ref_id": config.source_ref_id,
        "repository_id": config.repository_id,
        "backup_policy_id": config.backup_policy_id,
        "file_filter_rule_id": config.file_filter_rule_id,
        "directory_count": len(directories),
        "compression_level": config.compression_level,
        "status": config.status,
        "reset_task_uuid": str(config.reset_task_uuid)
        if config.reset_task_uuid
        else None,
        "provisioning_task_uuid": (
            str(config.provisioning_task_uuid)
            if config.provisioning_task_uuid
            else None
        ),
        "provisioning_error_code": config.provisioning_error_code,
        "provisioning_error_message": config.provisioning_error_message,
        "recovery_plan_enabled": config.recovery_plan_enabled,
        "directories": [_directory_payload(directory) for directory in directories],
        "recovery_plans": [_recovery_plan_payload(plan) for plan in recovery_plans],
        "created_at": _iso(config.created_at),
        "updated_at": _iso(config.updated_at),
    }


def _repository_payload(
    config: BackupConfig, repo: Repository | None
) -> dict[str, Any]:
    return {
        "id": f"{config.id}:{config.repository_id}",
        "config_id": config.id,
        "repository_id": config.repository_id,
        "name": repo.name if repo else f"#{config.repository_id}",
        "repo_type": repo.repo_type if repo else "",
        "location": _repository_location(repo) if repo else "",
        "health": repo.health if repo else "",
        "status": repo.status if repo else "",
        "used_bytes": repo.estimated_usage_bytes if repo else 0,
        "capacity_bytes": repo.capacity_bytes if repo else 0,
        "last_checked_at": _iso(repo.last_checked_at) if repo else None,
    }


def _policy_payload(policy: BackupPolicy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "name": policy.name,
        "is_active": policy.is_active,
        "schedule": policy.schedule or {},
        "retention": policy.retention or {},
        "throttling": policy.throttling or {},
        "error_handling": policy.error_handling or {},
        "schedule_summary": backup_policy_schedule_summary(policy),
        "retention_summary": backup_policy_retention_summary(policy),
        "related_backup_count": backup_policy_related_count(policy=policy),
        "created_at": _iso(policy.created_at),
        "updated_at": _iso(policy.updated_at),
    }


def _filter_payload(rule: FileFilterRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "is_active": rule.is_active,
        "ignore_patterns": rule.ignore_patterns,
        "large_file_limit_enabled": rule.large_file_limit_enabled,
        "large_file_bytes_max": rule.large_file_bytes_max,
        "ignore_cache_directories": rule.ignore_cache_directories,
        "current_filesystem_only": rule.current_filesystem_only,
        "summary": file_filter_summary(rule),
        "related_backup_count": file_filter_related_count(rule=rule),
        "created_at": _iso(rule.created_at),
        "updated_at": _iso(rule.updated_at),
    }


def _snapshot_payload(snapshot: BackupSourceSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    recoverable = snapshot.status in (
        BackupSourceSnapshot.Status.AVAILABLE,
        BackupSourceSnapshot.Status.PARTIAL,
    )
    return {
        "id": snapshot.id,
        "snapshot_uid": snapshot.snapshot_uid,
        "source_type": snapshot.source_type,
        "source_ref_id": snapshot.source_ref_id,
        "backup_config_id": snapshot.backup_config_id,
        "repository_id": snapshot.repository_id,
        "task_id": snapshot.task_id,
        "task_uuid": str(snapshot.task_uuid),
        "trigger_type": snapshot.trigger_type,
        "status": snapshot.status,
        "started_at": _iso(snapshot.started_at),
        "finished_at": _iso(snapshot.finished_at),
        "created_at": _iso(snapshot.created_at),
        "directory_count": snapshot.directory_count,
        "successful_directory_count": snapshot.successful_directory_count,
        "failed_directory_count": snapshot.failed_directory_count,
        "kopia_snapshot_count": int(snapshot.successful_directory_count or 0),
        "total_size_bytes": snapshot.total_size_bytes,
        "file_count": snapshot.file_count,
        "dir_count": snapshot.dir_count,
        "error_code": snapshot.error_code,
        "error_message": snapshot.error_message,
        "recoverable": recoverable,
    }


def _task_payload(task: Task | None) -> dict[str, Any] | None:
    if task is None:
        return None
    return {
        "id": task.id,
        "task_uuid": str(task.task_uuid),
        "task_type": task.task_type,
        "display_name": task.display_name,
        "status": task.status,
        "progress": _number(task.progress),
        "current_step": task.current_step,
        "trigger_type": task.trigger_type,
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "error_message": task.error_message,
        "request_payload": task.request_payload,
        "transfer_progress": (
            task.result_payload.get("transfer_progress")
            if isinstance(task.result_payload, dict)
            else None
        ),
    }


def _restore_record_payload(record: RestoreRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "restore_uid": record.restore_uid,
        "source_mode": record.source_mode,
        "plan_id": record.plan_id,
        "task_id": record.task_id,
        "task_uuid": str(record.task_uuid),
        "source_type": record.source_type,
        "source_ref_id": record.source_ref_id,
        "backup_config_id": record.backup_config_id,
        "source_snapshot_id": record.source_snapshot_id,
        "target_type": record.target_type,
        "target_ref_id": record.target_ref_id,
        "target_path": record.target_path,
        "scope": record.scope,
        "conflict_mode": record.conflict_mode,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }


def _empty_runtime() -> dict[str, Any]:
    return {
        "backup": {
            "running": False,
            "stopping": False,
            "cancelled": False,
            "failed": False,
            "progress": 0,
            "last_backup_at": None,
            "total": 0,
            "running_count": 0,
            "latest_task": None,
        },
        "restore": {
            "running": False,
            "stopping": False,
            "cancelled": False,
            "failed": False,
            "progress": 0,
            "last_restore_at": None,
            "total": 0,
            "running_count": 0,
            "latest_task": None,
            "latest_record": None,
        },
        "latest_snapshot": None,
        "has_restorable_snapshot": False,
        "restorable_snapshot_count": 0,
        "latest_restorable_snapshot": None,
    }


def _matches_search(item: dict[str, Any], search: str) -> bool:
    term = search.strip().lower()
    if not term:
        return True
    haystack = " ".join(
        str(item.get(key) or "")
        for key in (
            "id",
            "name",
            "hostname",
            "node_name",
            "node_ip",
            "protocol",
            "type",
        )
    ).lower()
    return term in haystack


def _source_filters_for_items(items: list[dict[str, Any]]) -> Q:
    query = Q(pk__in=[])
    for item in items:
        source_type = (
            "agent"
            if item.get("kind") == "agent"
            else str(item.get("kind") or item.get("type") or "")
        )
        ref_id = int(item.get("ref_id") or 0)
        if source_type and ref_id:
            query |= Q(source_type=source_type, source_ref_id=ref_id)
    return query


def _item_key(item: dict[str, Any]) -> str:
    source_type = (
        "agent"
        if item.get("kind") == "agent"
        else str(item.get("kind") or item.get("type") or "")
    )
    return _source_key(source_type, int(item.get("ref_id") or 0))


def _task_resource_filters_for_items(items: list[dict[str, Any]]) -> Q:
    query = Q(pk__in=[])
    for item in items:
        source_type = (
            "agent"
            if item.get("kind") == "agent"
            else str(item.get("kind") or item.get("type") or "")
        )
        ref_id = int(item.get("ref_id") or 0)
        if not source_type or not ref_id:
            continue
        subtype_query = Q(resources__resource_subtype=source_type)
        if source_type == "agent":
            subtype_query |= Q(resources__resource_subtype="")
        query |= (
            Q(resources__resource_type=TaskResource.Type.BACKUP_SOURCE)
            & subtype_query
            & Q(resources__resource_id=ref_id)
        )
    return query


def _attach_backup_config_expansion(
    *,
    organization_id: int,
    items: list[dict[str, Any]],
    include_policies: bool,
) -> tuple[list[BackupConfig], dict[int, list[BackupConfigDirectory]]]:
    if not items:
        return [], {}

    configs = list(
        BackupConfig.objects.filter(organization_id=organization_id)
        .filter(_source_filters_for_items(items))
        .order_by("source_type", "source_ref_id", "-created_at", "-id")
    )
    config_ids = [config.id for config in configs]
    directories_by_config: dict[int, list[BackupConfigDirectory]] = defaultdict(list)
    if config_ids:
        directories = BackupConfigDirectory.objects.filter(
            organization_id=organization_id,
            backup_config_id__in=config_ids,
        ).order_by("backup_config_id", "sort_order", "id")
        for directory in directories:
            directories_by_config[directory.backup_config_id].append(directory)

    plans_by_config: dict[int, list[RestorePlan]] = defaultdict(list)
    if config_ids:
        plans = RestorePlan.objects.filter(
            organization_id=organization_id,
            backup_config_id__in=config_ids,
        ).order_by("backup_config_id", "sort_order", "id")
        for plan in plans:
            plans_by_config[plan.backup_config_id].append(plan)

    repository_ids = {
        config.repository_id for config in configs if config.repository_id
    }
    repositories = (
        {
            repo.id: repo
            for repo in Repository.objects.filter(
                organization_id=organization_id,
                id__in=repository_ids,
            )
        }
        if repository_ids
        else {}
    )

    policy_ids = {
        int(config.backup_policy_id) for config in configs if config.backup_policy_id
    }
    filter_ids = {
        int(config.file_filter_rule_id)
        for config in configs
        if config.file_filter_rule_id
    }
    policies = (
        {
            policy.id: policy
            for policy in BackupPolicy.objects.filter(
                organization_id=organization_id, id__in=policy_ids
            )
        }
        if include_policies and policy_ids
        else {}
    )
    filters = (
        {
            rule.id: rule
            for rule in FileFilterRule.objects.filter(
                organization_id=organization_id, id__in=filter_ids
            )
        }
        if include_policies and filter_ids
        else {}
    )

    configs_by_source: dict[str, list[BackupConfig]] = defaultdict(list)
    for config in configs:
        configs_by_source[_source_key(config.source_type, config.source_ref_id)].append(
            config
        )

    for item in items:
        key = _item_key(item)
        source_configs = configs_by_source.get(key, [])
        config_payloads = [
            _config_payload(
                config,
                directories=directories_by_config.get(config.id, []),
                recovery_plans=plans_by_config.get(config.id, []),
            )
            for config in source_configs
        ]
        dir_rows: list[dict[str, Any]] = []
        repo_rows: list[dict[str, Any]] = []
        policy_rows: list[dict[str, Any]] = []
        filter_rows: list[dict[str, Any]] = []
        for config in source_configs:
            repo_rows.append(
                _repository_payload(config, repositories.get(config.repository_id))
            )
            for directory in directories_by_config.get(config.id, []):
                dir_rows.append(
                    {
                        "config_id": config.id,
                        "config_name": config.name,
                        "path": directory.path,
                        "path_type": directory.path_type,
                    }
                )
            if config.backup_policy_id and config.backup_policy_id in policies:
                policy_rows.append(_policy_payload(policies[config.backup_policy_id]))
            if config.file_filter_rule_id and config.file_filter_rule_id in filters:
                filter_rows.append(_filter_payload(filters[config.file_filter_rule_id]))

        item["backup_configs"] = {
            "count": len(source_configs),
            "ids": [config.id for config in source_configs],
            "configs": config_payloads,
            "dirs_preview": dir_rows[:2],
            "dirs_overflow_count": max(0, len(dir_rows) - 2),
            "repos_preview": repo_rows[:2],
            "repos_overflow_count": max(0, len(repo_rows) - 2),
        }
        if include_policies:
            item["policies"] = {
                "count": len(policy_rows),
                "names": [row["name"] for row in policy_rows],
                "items": policy_rows,
            }
            item["filters"] = {
                "count": len(filter_rows),
                "names": [row["name"] for row in filter_rows],
                "items": filter_rows,
            }

    return configs, directories_by_config


def _task_source_keys(task: Task) -> list[str]:
    keys: list[str] = []
    for resource in task.resources.all():
        if resource.resource_type != TaskResource.Type.BACKUP_SOURCE:
            continue
        source_type = resource.resource_subtype or "agent"
        keys.append(_source_key(source_type, int(resource.resource_id)))
    return keys


def _task_time(task: Task) -> datetime:
    return (
        task.finished_at
        or task.started_at
        or task.created_at
        or datetime.min.replace(tzinfo=datetime_timezone.utc)
    )


def _restore_record_status(record: RestoreRecord, task: Task | None) -> str:
    status = str(task.status if task else "").lower()
    if status in {"success", "completed", "done"}:
        return "completed"
    if status == "cancelled":
        return "cancelled"
    if status in {"failed", "timeout"}:
        return "failed"
    item_statuses = [str(item.status or "").lower() for item in record.items.all()]
    if item_statuses and all(status == "success" for status in item_statuses):
        return "completed"
    if any(status == "cancelled" for status in item_statuses):
        return "cancelled"
    if any(status == "failed" for status in item_statuses):
        return "failed"
    return "running"


def _attach_runtime_expansion(
    *, organization_id: int, items: list[dict[str, Any]]
) -> None:
    if not items:
        return

    keys = {_item_key(item) for item in items}
    source_query = _source_filters_for_items(items)

    snapshots_by_source: dict[str, BackupSourceSnapshot] = {}
    restorable_snapshots_by_source: dict[str, BackupSourceSnapshot] = {}
    restorable_snapshot_counts_by_source: dict[str, int] = defaultdict(int)
    snapshots = (
        BackupSourceSnapshot.objects.filter(
            organization_id=organization_id, deleted_at__isnull=True
        )
        .exclude(status=BackupSourceSnapshot.Status.DELETED)
        .filter(source_query)
        .order_by("-created_at", "-id")
    )
    for snapshot in snapshots:
        key = _source_key(snapshot.source_type, snapshot.source_ref_id)
        if key not in snapshots_by_source:
            snapshots_by_source[key] = snapshot
        if snapshot.status in _RESTORABLE_SNAPSHOT_STATUSES:
            restorable_snapshot_counts_by_source[key] += 1
            if key not in restorable_snapshots_by_source:
                restorable_snapshots_by_source[key] = snapshot

    backup_tasks_by_source: dict[str, list[Task]] = defaultdict(list)
    backup_tasks = (
        Task.objects.filter(
            organization_id=organization_id,
            task_type=Task.Type.BACKUP,
        )
        .filter(_task_resource_filters_for_items(items))
        .filter(
            Q(
                status__in=[
                    Task.Status.PENDING,
                    Task.Status.WAITING,
                    Task.Status.BLOCKED,
                    Task.Status.RUNNING,
                ]
            )
            | Q(created_at__gte=timezone.now() - timedelta(days=30))
        )
        .prefetch_related("resources")
        .order_by("-created_at", "-id")
        .distinct()
    )
    for task in backup_tasks:
        for key in _task_source_keys(task):
            if key in keys:
                backup_tasks_by_source[key].append(task)

    records_by_source: dict[str, list[RestoreRecord]] = defaultdict(list)
    restore_records = (
        RestoreRecord.objects.filter(
            organization_id=organization_id,
            purpose=RestoreRecord.Purpose.USER_DATA,
        )
        .filter(source_query)
        .prefetch_related("items")
        .order_by("-created_at", "-id")
    )
    task_uuids = [record.task_uuid for record in restore_records if record.task_uuid]
    restore_tasks_by_uuid = (
        {
            str(task.task_uuid): task
            for task in Task.objects.filter(
                organization_id=organization_id,
                task_uuid__in=task_uuids,
            )
        }
        if task_uuids
        else {}
    )
    for record in restore_records:
        records_by_source[_source_key(record.source_type, record.source_ref_id)].append(
            record
        )

    for item in items:
        key = _item_key(item)
        runtime = _empty_runtime()
        backup_tasks_for_source = backup_tasks_by_source.get(key, [])
        active_backup = [
            task
            for task in backup_tasks_for_source
            if task.status
            in (
                Task.Status.PENDING,
                Task.Status.WAITING,
                Task.Status.BLOCKED,
                Task.Status.RUNNING,
            )
        ]
        from apps.node.services.internal.task_offline_reconcile import (
            product_task_blocks_cleanup,
            task_execution_state,
        )

        active_backup = [
            task
            for task in active_backup
            if task.status in (Task.Status.WAITING, Task.Status.BLOCKED)
            or product_task_blocks_cleanup(task=task)
        ]
        latest_backup_task = (
            backup_tasks_for_source[0] if backup_tasks_for_source else None
        )
        latest_snapshot = snapshots_by_source.get(key)
        latest_restorable_snapshot = restorable_snapshots_by_source.get(key)
        execution_node = None
        if item.get("kind") == "agent":
            execution_node = Node.objects.filter(
                pk=int(item.get("ref_id") or 0),
                organization_id=organization_id,
            ).first()
        if active_backup:
            runtime["backup"]["running"] = True
            runtime["backup"]["running_count"] = len(active_backup)
            primary_task = active_backup[0]
            runtime["backup"]["progress"] = max(
                _number(task.progress) for task in active_backup
            )
            runtime["backup"]["execution_state"] = task_execution_state(
                node=execution_node,
                task=primary_task,
            )
            try:
                from apps.protection.services.progress.backup_runtime import (
                    build_backup_kopia_progress,
                )

                kopia_payload = build_backup_kopia_progress(task=primary_task)
                runtime["backup"]["transfer_progress"] = kopia_payload.get(
                    "transfer_progress"
                )
                if not runtime["backup"]["transfer_progress"]:
                    result_payload = (
                        primary_task.result_payload
                        if isinstance(primary_task.result_payload, dict)
                        else {}
                    )
                    runtime["backup"]["transfer_progress"] = result_payload.get(
                        "transfer_progress"
                    )
                runtime["backup"]["kopia_progress"] = kopia_payload
            except Exception:
                pass
        runtime["backup"]["total"] = len(backup_tasks_for_source)
        backup_stopping = (
            not active_backup
            and latest_backup_task is not None
            and product_task_is_stopping(
                organization_id=organization_id, task=latest_backup_task
            )
        )
        runtime["backup"]["stopping"] = backup_stopping
        runtime["backup"]["cancelled"] = (
            not active_backup
            and not backup_stopping
            and latest_backup_task is not None
            and latest_backup_task.status == Task.Status.CANCELLED
        )
        runtime["backup"]["failed"] = (
            not active_backup
            and not backup_stopping
            and latest_backup_task is not None
            and latest_backup_task.status in (Task.Status.FAILED, Task.Status.TIMEOUT)
        )
        runtime["backup"]["latest_task"] = _task_payload(latest_backup_task)
        runtime["backup"]["last_backup_at"] = (
            _iso(
                latest_snapshot.finished_at
                or latest_snapshot.started_at
                or latest_snapshot.created_at
            )
            if latest_snapshot
            else _iso(_task_time(latest_backup_task))
            if latest_backup_task
            else None
        )
        runtime["latest_snapshot"] = _snapshot_payload(latest_snapshot)
        runtime["has_restorable_snapshot"] = (
            restorable_snapshot_counts_by_source.get(key, 0) > 0
        )
        runtime["restorable_snapshot_count"] = restorable_snapshot_counts_by_source.get(
            key, 0
        )
        runtime["latest_restorable_snapshot"] = _snapshot_payload(
            latest_restorable_snapshot
        )

        restore_records_for_source = records_by_source.get(key, [])
        running_restore: list[tuple[RestoreRecord, Task | None]] = []
        latest_restore_pair: tuple[RestoreRecord, Task | None] | None = None
        for record in restore_records_for_source:
            task = restore_tasks_by_uuid.get(str(record.task_uuid))
            if latest_restore_pair is None:
                latest_restore_pair = (record, task)
            if _restore_record_status(record, task) == "running":
                if task is None or product_task_blocks_cleanup(task=task):
                    running_restore.append((record, task))
        if running_restore:
            runtime["restore"]["running"] = True
            runtime["restore"]["running_count"] = len(running_restore)
            record, task = running_restore[0]
            runtime["restore"]["progress"] = max(
                _number(task.progress) if task else 0 for _, task in running_restore
            )
            restore_node = None
            if str(record.target_type or "") == "agent":
                restore_node = Node.objects.filter(
                    pk=int(record.target_ref_id or 0),
                    organization_id=organization_id,
                ).first()
            runtime["restore"]["execution_state"] = task_execution_state(
                node=restore_node, task=task
            )
            try:
                from apps.protection.services.progress.restore_runtime import (
                    build_restore_kopia_progress,
                )

                kopia_payload = build_restore_kopia_progress(record=record, task=task)
                runtime["restore"]["transfer_progress"] = kopia_payload.get(
                    "transfer_progress"
                )
                if not runtime["restore"]["transfer_progress"] and task is not None:
                    result_payload = (
                        task.result_payload
                        if isinstance(task.result_payload, dict)
                        else {}
                    )
                    runtime["restore"]["transfer_progress"] = result_payload.get(
                        "transfer_progress"
                    )
                runtime["restore"]["kopia_progress"] = kopia_payload
            except Exception:
                pass
        runtime["restore"]["total"] = len(restore_records_for_source)
        latest_restore_status = (
            _restore_record_status(latest_restore_pair[0], latest_restore_pair[1])
            if latest_restore_pair is not None
            else ""
        )
        restore_stopping = (
            not running_restore
            and latest_restore_pair is not None
            and latest_restore_pair[1] is not None
            and _restore_task_is_stopping(latest_restore_pair[1])
        )
        runtime["restore"]["stopping"] = restore_stopping
        runtime["restore"]["cancelled"] = (
            not running_restore
            and not restore_stopping
            and latest_restore_status == "cancelled"
        )
        runtime["restore"]["failed"] = (
            not running_restore
            and not restore_stopping
            and latest_restore_status == "failed"
        )
        if latest_restore_pair:
            record, task = latest_restore_pair
            runtime["restore"]["latest_task"] = _task_payload(task)
            runtime["restore"]["latest_record"] = _restore_record_payload(record)
            runtime["restore"]["last_restore_at"] = _iso(
                (task.finished_at if task else None)
                or record.updated_at
                or record.created_at
            )
        item["runtime"] = runtime


def _restore_task_is_stopping(task: Task) -> bool:
    # Import lazily to keep the source catalog independent from restore service
    # initialization while using the restore-specific NodeTask correlation.
    from apps.restore.services.interface import restore_task_is_stopping

    return restore_task_is_stopping(task=task)


def _attach_expansions(
    *, organization_id: int, items: list[dict[str, Any]], expand: str | None
) -> None:
    expands = _parse_expand(expand)
    if not expands:
        return
    if _EXPAND_BACKUP_CONFIGS in expands or _EXPAND_POLICIES in expands:
        _attach_backup_config_expansion(
            organization_id=organization_id,
            items=items,
            include_policies=_EXPAND_POLICIES in expands,
        )
    if _EXPAND_RUNTIME in expands:
        _attach_runtime_expansion(organization_id=organization_id, items=items)


def fetch_backup_selectable_by_ids(
    *, organization_id: int, ids: list[str], expand: str | None = None
) -> list[dict[str, Any]]:
    wanted: dict[str, int] = {}
    for value in ids:
        parsed = parse_selectable_id(value)
        if parsed:
            wanted[f"{parsed[0]}:{parsed[1]}"] = parsed[1]

    if not wanted:
        return []

    agent_ids = [
        parsed[1]
        for parsed in (parse_selectable_id(v) for v in ids)
        if parsed and parsed[0] == "agent"
    ]
    nas_ids = [
        parsed[1]
        for parsed in (parse_selectable_id(v) for v in ids)
        if parsed and parsed[0] == "nas"
    ]

    items: list[dict[str, Any]] = []
    if agent_ids:
        for node in Node.objects.filter(
            organization_id=organization_id,
            role=NodeRole.AGENT,
            id__in=agent_ids,
            is_deleted=False,
        ):
            items.append(_agent_item(node))
    if nas_ids:
        for resource in SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id__in=nas_ids,
            is_deleted=False,
        ).select_related("bound_node"):
            items.append(_nas_item(resource))

    order = {value: index for index, value in enumerate(ids)}
    items.sort(key=lambda row: order.get(str(row["id"]), 10_000))
    pipeline_map = load_pipeline_step_map(organization_id=organization_id)
    items = attach_pipeline_steps(items, pipeline_map=pipeline_map)
    _attach_expansions(organization_id=organization_id, items=items, expand=expand)
    return items


def _filter_pipeline_ip(
    queryset: QuerySet, value: str, *, field: str = "source_ip"
) -> QuerySet:
    term = value.strip().lower()
    if not term:
        return queryset
    try:
        normalized = str(ipaddress.ip_address(term))
    except ValueError:
        return queryset.filter(**{f"{field}__startswith": term})
    return queryset.filter(**{field: normalized})


def _pipeline_queryset(
    *,
    organization_id: int,
    search: str | None,
    search_field: str | None,
    source_name: str | None,
    source_hostname: str | None,
    source_ip: str | None,
    status: str | None,
    source_status: str | None,
    availability: str | None,
    source_type: str | None,
    exclude_ids: list[str] | None,
    pipeline_step: int | None,
    running_task: str | None,
    backup_running: bool | None,
    restore_running: bool | None,
    backup_task_status: str | None = None,
    restore_task_status: str | None = None,
    backup_policy_id: int | None,
    file_filter_rule_id: int | None,
    repository_id: int | None,
) -> QuerySet[SourceBackupPipelineEntry]:
    queryset = SourceBackupPipelineEntry.objects.filter(
        organization_id=organization_id,
        is_deleted=False,
    )
    if pipeline_step in PipelineStep.VALID:
        queryset = queryset.filter(step=pipeline_step)
    if source_type:
        source_kind = (
            SelectableSourceKind.AGENT if source_type == "host" else source_type
        )
        queryset = queryset.filter(source_kind=source_kind)

    excluded: dict[str, list[int]] = defaultdict(list)
    for raw_id in exclude_ids or []:
        parsed = parse_selectable_id(raw_id)
        if parsed:
            excluded[parsed[0]].append(parsed[1])
    for source_kind, ref_ids in excluded.items():
        queryset = queryset.exclude(source_kind=source_kind, ref_id__in=ref_ids)

    if search and search.strip():
        selected_field = _SEARCH_FIELDS.get(
            search_field or "source_name", "source_name"
        )
        if selected_field == "source_ip":
            queryset = _filter_pipeline_ip(queryset, search, field=selected_field)
        else:
            queryset = queryset.filter(
                **{f"{selected_field}__icontains": search.strip()}
            )
    if source_name:
        queryset = queryset.filter(source_name__icontains=source_name.strip())
    if source_hostname:
        queryset = queryset.filter(source_hostname__icontains=source_hostname.strip())
    if source_ip:
        queryset = _filter_pipeline_ip(queryset, source_ip)

    if source_status:
        queryset = queryset.filter(source_status=source_status)
    elif status:
        queryset = queryset.filter(source_status=status)
    if availability:
        queryset = queryset.filter(source_availability=availability)

    def filter_task_status(queryset: QuerySet, *, field: str, value: str) -> QuerySet:
        if value == "running":
            return queryset.filter(**{f"{field}__in": _ACTIVE_PIPELINE_TASK_STATUSES})
        if value == "failed":
            return queryset.filter(
                **{f"{field}__in": ("failed", "timeout", "cancelled")}
            )
        return queryset.filter(**{field: value})

    if backup_task_status:
        queryset = filter_task_status(
            queryset, field="last_backup_status", value=backup_task_status
        )
    else:
        backup_is_running = backup_running is True or running_task == "backup"
        if backup_is_running:
            queryset = queryset.filter(
                last_backup_status__in=_ACTIVE_PIPELINE_TASK_STATUSES
            )
        elif backup_running is False:
            queryset = queryset.exclude(
                last_backup_status__in=_ACTIVE_PIPELINE_TASK_STATUSES
            )
    if restore_task_status:
        queryset = filter_task_status(
            queryset, field="last_restore_status", value=restore_task_status
        )
    else:
        restore_is_running = restore_running is True or running_task == "restore"
        if restore_is_running:
            queryset = queryset.filter(
                last_restore_status__in=_ACTIVE_PIPELINE_TASK_STATUSES
            )
        elif restore_running is False:
            queryset = queryset.exclude(
                last_restore_status__in=_ACTIVE_PIPELINE_TASK_STATUSES
            )

    if any(
        value is not None
        for value in (backup_policy_id, file_filter_rule_id, repository_id)
    ):
        configs = BackupConfig.objects.filter(
            organization_id=organization_id,
            source_type=OuterRef("source_kind"),
            source_ref_id=OuterRef("ref_id"),
        )
        if backup_policy_id is not None:
            configs = configs.filter(backup_policy_id=backup_policy_id)
        if file_filter_rule_id is not None:
            configs = configs.filter(file_filter_rule_id=file_filter_rule_id)
        if repository_id is not None:
            configs = configs.filter(repository_id=repository_id)
        queryset = queryset.annotate(has_matching_backup_config=Exists(configs)).filter(
            has_matching_backup_config=True
        )
    return queryset.order_by("-created_at", "-id")


def _materialize_pipeline_page(
    *,
    organization_id: int,
    entries: list[SourceBackupPipelineEntry],
    expand: str | None,
) -> list[dict[str, Any]]:
    agent_ids = [
        entry.ref_id
        for entry in entries
        if entry.source_kind == SelectableSourceKind.AGENT
    ]
    nas_ids = [
        entry.ref_id
        for entry in entries
        if entry.source_kind == SelectableSourceKind.NAS
    ]
    items_by_id: dict[str, dict[str, Any]] = {}
    if agent_ids:
        agents = Node.objects.filter(
            organization_id=organization_id,
            role=NodeRole.AGENT,
            id__in=agent_ids,
            is_deleted=False,
        )
        items_by_id.update((str(item["id"]), item) for item in map(_agent_item, agents))
    if nas_ids:
        resources = SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id__in=nas_ids,
            is_deleted=False,
        ).select_related("bound_node")
        items_by_id.update(
            (str(item["id"]), item) for item in map(_nas_item, resources)
        )

    items: list[dict[str, Any]] = []
    for entry in entries:
        item = items_by_id.get(f"{entry.source_kind}:{entry.ref_id}")
        if item is None:
            continue
        item["pipeline_step"] = int(entry.step)
        items.append(item)
    _attach_expansions(organization_id=organization_id, items=items, expand=expand)
    return items


def _list_pipeline_backup_selectable_sources(
    *,
    organization_id: int,
    page: int,
    page_size: int,
    expand: str | None,
    **filters: Any,
) -> tuple[list[dict[str, Any]], int]:
    queryset = _pipeline_queryset(organization_id=organization_id, **filters)
    total = queryset.count()
    start = (page - 1) * page_size
    entries = list(queryset[start : start + page_size])
    return _materialize_pipeline_page(
        organization_id=organization_id,
        entries=entries,
        expand=expand,
    ), total


def _pipeline_filter_is_requested(
    filters: dict[str, Any], *, search: str | None
) -> bool:
    return bool(search and filters.get("search_field")) or any(
        filters.get(name) not in (None, "", [])
        for name in (
            "source_name",
            "source_hostname",
            "source_ip",
            "source_status",
            "availability",
            "running_task",
            "backup_running",
            "restore_running",
            "backup_task_status",
            "restore_task_status",
            "backup_policy_id",
            "file_filter_rule_id",
            "repository_id",
        )
    )


def _list_legacy_backup_selectable_sources(
    *,
    organization_id: int,
    page: int,
    page_size: int,
    search: str | None,
    status: str | None,
    source_type: str | None,
    exclude_ids: list[str] | None,
    pipeline_step: int | None,
    expand: str | None,
    **filters: Any,
) -> tuple[list[dict[str, Any]], int]:
    exclude = {
        value.strip() for value in (exclude_ids or []) if value and value.strip()
    }
    items = _build_catalog(organization_id=organization_id)
    pipeline_map = load_pipeline_step_map(organization_id=organization_id)
    items = attach_pipeline_steps(items, pipeline_map=pipeline_map)

    if pipeline_step is not None and pipeline_step in PipelineStep.VALID:
        if pipeline_step == PipelineStep.READY:
            configured = _configured_source_keys(organization_id=organization_id)
            items = [
                item
                for item in items
                if int(item.get("pipeline_step", PipelineStep.SOURCE_POOL))
                == PipelineStep.READY
                or str(item.get("id")) in configured
            ]
            for item in items:
                if str(item.get("id")) in configured:
                    item["pipeline_step"] = PipelineStep.READY
        else:
            items = filter_items_by_pipeline_step(items, pipeline_step)
    elif exclude:
        items = [item for item in items if item["id"] not in exclude]

    pipeline_filter_requested = _pipeline_filter_is_requested(filters, search=search)
    if pipeline_filter_requested:
        matching_rows = _pipeline_queryset(
            organization_id=organization_id,
            search=search if filters.get("search_field") else None,
            status=None,
            source_type=None,
            exclude_ids=None,
            pipeline_step=None,
            **filters,
        ).values_list("source_kind", "ref_id")
        matching_keys = {
            _source_key(str(kind), int(ref_id)) for kind, ref_id in matching_rows
        }
        items = [item for item in items if str(item.get("id")) in matching_keys]
    elif search and search.strip():
        items = [item for item in items if _matches_search(item, search)]
    if status and status.strip():
        expected_status = status.strip().lower()
        items = [
            item
            for item in items
            if str(item.get("status") or "").lower() == expected_status
        ]
    if source_type and source_type.strip():
        expected_type = source_type.strip().lower()
        items = [
            item
            for item in items
            if str(item.get("type") or "").lower() == expected_type
        ]

    total = len(items)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    _attach_expansions(organization_id=organization_id, items=page_items, expand=expand)
    return page_items, total


def _query_mode() -> str:
    mode = str(
        getattr(settings, "SOURCE_BACKUP_SELECTABLE_QUERY_MODE", "legacy") or "legacy"
    ).lower()
    return mode if mode in _QUERY_MODES else "legacy"


def _ordered_id_digest(items: list[dict[str, Any]]) -> str:
    payload = "\n".join(str(item.get("id") or "") for item in items).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def list_backup_selectable_sources(
    *,
    organization_id: int,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    search_field: str | None = None,
    source_name: str | None = None,
    source_hostname: str | None = None,
    source_ip: str | None = None,
    status: str | None = None,
    source_status: str | None = None,
    availability: str | None = None,
    source_type: str | None = None,
    exclude_ids: list[str] | None = None,
    pipeline_step: int | None = None,
    running_task: str | None = None,
    backup_running: bool | None = None,
    restore_running: bool | None = None,
    backup_task_status: str | None = None,
    restore_task_status: str | None = None,
    backup_policy_id: int | None = None,
    file_filter_rule_id: int | None = None,
    repository_id: int | None = None,
    expand: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    filters = {
        "search": search,
        "search_field": search_field,
        "source_name": source_name,
        "source_hostname": source_hostname,
        "source_ip": source_ip,
        "status": status,
        "source_status": source_status,
        "availability": availability,
        "source_type": source_type,
        "exclude_ids": exclude_ids,
        "pipeline_step": pipeline_step,
        "running_task": running_task,
        "backup_running": backup_running,
        "restore_running": restore_running,
        "backup_task_status": backup_task_status,
        "restore_task_status": restore_task_status,
        "backup_policy_id": backup_policy_id,
        "file_filter_rule_id": file_filter_rule_id,
        "repository_id": repository_id,
    }
    mode = _query_mode()
    BACKUP_SELECTABLE_QUERY_REQUESTS.labels(mode=mode).inc()
    if mode == "pipeline":
        started = time.monotonic()
        try:
            return _list_pipeline_backup_selectable_sources(
                organization_id=organization_id,
                page=page,
                page_size=page_size,
                expand=expand,
                **filters,
            )
        finally:
            BACKUP_SELECTABLE_QUERY_DURATION.labels(engine="pipeline").observe(
                time.monotonic() - started
            )

    started = time.monotonic()
    legacy_items, legacy_total = _list_legacy_backup_selectable_sources(
        organization_id=organization_id,
        page=page,
        page_size=page_size,
        expand=expand,
        **filters,
    )
    BACKUP_SELECTABLE_QUERY_DURATION.labels(engine="legacy").observe(
        time.monotonic() - started
    )
    if mode != "shadow":
        return legacy_items, legacy_total

    shadow_started = time.monotonic()
    pipeline_items, pipeline_total = _list_pipeline_backup_selectable_sources(
        organization_id=organization_id,
        page=page,
        page_size=page_size,
        expand=None,
        **filters,
    )
    BACKUP_SELECTABLE_QUERY_DURATION.labels(engine="pipeline_shadow").observe(
        time.monotonic() - shadow_started
    )
    legacy_ids = [str(item.get("id")) for item in legacy_items]
    pipeline_ids = [str(item.get("id")) for item in pipeline_items]
    if legacy_total != pipeline_total:
        outcome = "count_mismatch"
    elif legacy_ids != pipeline_ids:
        outcome = "ordered_ids_mismatch"
    else:
        outcome = "match"
    BACKUP_SELECTABLE_SHADOW_COMPARISONS.labels(outcome=outcome).inc()
    if outcome != "match":
        logger.warning(
            "backup selectable shadow mismatch organization_id=%s page=%s page_size=%s step=%s "
            "outcome=%s legacy_count=%s pipeline_count=%s "
            "legacy_ids_sha256=%s pipeline_ids_sha256=%s",
            organization_id,
            page,
            page_size,
            pipeline_step,
            outcome,
            legacy_total,
            pipeline_total,
            _ordered_id_digest(legacy_items),
            _ordered_id_digest(pipeline_items),
        )
    return legacy_items, legacy_total
