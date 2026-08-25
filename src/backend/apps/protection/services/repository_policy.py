from __future__ import annotations

from typing import Any

from apps.node.services.interface import run_agent_task_sync
from apps.protection.models import BackupConfig
from apps.protection.services import backup_task as bt
from apps.protection.services.backup_runtime_policy import build_backup_runtime_policy
from apps.protection.services.backup_source_snapshot import backup_config_directories
from apps.protection.services.repository_compatibility import validate_backup_repository_compatible
from apps.source.services.internal.nas_share_path import to_mount_path
from apps.storage.services.internal.repository_access import repository_uses_bound_proxy


def sync_backup_config_repository_policy(*, config_id: int) -> dict[str, Any]:
    config = BackupConfig.objects.filter(id=config_id, status=BackupConfig.Status.ACTIVE).first()
    if config is None:
        return {"config_id": config_id, "status": "skipped"}
    target = bt._resolve_execution_target(source_snapshot=config)
    repository = validate_backup_repository_compatible(
        organization_id=config.organization_id,
        source_type=config.source_type,
        source_ref_id=config.source_ref_id,
        repository_id=config.repository_id,
    )
    if (
        repository_uses_bound_proxy(repository)
        and int(repository.bind_node_id or 0) != int(target.node.id)
    ):
        # Cross-proxy backups connect through a task-scoped Kopia server. The
        # managed backup command applies the complete runtime policy immediately
        # before every snapshot, using that same task-scoped identity.
        return {
            "config_id": config.id,
            "status": "skipped",
            "reason": "runtime_policy_applied_during_backup",
        }
    runtime_policy = build_backup_runtime_policy(config=config)
    repository_payload = bt._repository_runtime_payload(repository=repository, execution_target=target)
    results: list[dict[str, Any]] = []
    for directory in backup_config_directories(
        backup_config_id=config.id,
        organization_id=config.organization_id,
    ):
        source_path = bt._clean_path(directory.path)
        agent_path = to_mount_path(target.root_path, source_path) if target.root_path else source_path
        payload = bt._agent_backup_payload(
            source_path=agent_path,
            backup_config_dir_id=directory.id,
            repository_payload=repository_payload,
            nas_payload=target.nas_payload,
            file_filter_payload=runtime_policy["file_filter"],
            backup_policy_payload=runtime_policy["backup_policy"],
            compression_payload=runtime_policy["compression"],
            scope_mode=str(directory.scope_mode or "dynamic"),
            path_type=str(directory.path_type or "unknown"),
        )
        outcome = run_agent_task_sync(
            organization_id=config.organization_id,
            node_id=target.node.id,
            kind="repository.policy.apply",
            payload=payload,
            correlation_type="protection.repository_policy",
            correlation_id=f"backup-config:{config.id}:directory:{directory.id}",
            wait_timeout_seconds=300,
        )
        results.append({
            "directory_id": directory.id,
            "status": outcome.task.status,
            "error": outcome.task.last_error,
        })
    return {
        "config_id": config.id,
        "status": "success" if all(item["status"] == "success" for item in results) else "failed",
        "results": results,
    }
