from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from apps.node.models.base import NodeRole
from apps.protection.models import (
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.protection.services.backup_target_validation import (
    TargetValidationInput,
    TargetValidationResult,
    validate_restore_repository_assignments,
)
from apps.protection.services.repository_compatibility import (
    _direct_nas_agent_platform,
)
from apps.protection.services.snapshot_repository_locator import (
    resolve_snapshot_repository_reader,
)
from apps.protection.services.source_execution import (
    ExecutionTarget,
    resolve_source_execution_target,
)
from apps.storage.repositories.models import Repository


def validate_restore_targets(
    *,
    organization_id: int,
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    route_assignments = []
    route_keys_by_target: dict[str, list[str]] = {}
    immediate_results: dict[str, TargetValidationResult] = {}

    for index, item in enumerate(targets):
        key = str(item["key"])
        try:
            target_route_assignments = []
            target_route_keys: list[str] = []
            snapshot = _restore_snapshot(
                organization_id=organization_id,
                snapshot_id=int(item["source_snapshot_id"]),
            )
            execution_target = resolve_source_execution_target(
                organization_id=organization_id,
                source_type=str(item["target_type"]),
                source_ref_id=int(item["target_ref_id"]),
            )
            directories = _snapshot_directories(snapshot=snapshot)
            repositories = _repositories_for_directories(
                organization_id=organization_id,
                directories=directories,
            )
            seen_routes: set[tuple[int, int, str, str]] = set()
            for directory in directories:
                repository = repositories[int(directory.repository_id)]
                _validate_direct_nas_restore_target(
                    repository=repository,
                    execution_target=execution_target,
                    target_type=str(item["target_type"]),
                )
                repository_access = resolve_snapshot_repository_reader(
                    directory=directory,
                    repository=repository,
                    fallback_node=execution_target.node,
                    source_type=str(item["target_type"]),
                    source_ref_id=int(item["target_ref_id"]),
                )
                signature = (
                    execution_target.node.id,
                    repository.id,
                    str(repository_access.node.id),
                    str(repository_access.repository_payload.get("subdir") or ""),
                )
                if signature in seen_routes:
                    continue
                seen_routes.add(signature)
                route_key = f"{index}:{repository.id}:{len(seen_routes)}"
                target_route_keys.append(route_key)
                target_route_assignments.append(
                    (
                        TargetValidationInput(
                            key=route_key,
                            source_type=str(item["target_type"]),
                            source_ref_id=int(item["target_ref_id"]),
                            repository_id=repository.id,
                        ),
                        execution_target,
                        repository,
                        repository_access,
                    )
                )
            route_keys_by_target[key] = target_route_keys
            route_assignments.extend(target_route_assignments)
        except Exception as exc:
            immediate_results[key] = _exception_result(exc)

    route_results = (
        validate_restore_repository_assignments(
            organization_id=organization_id,
            assignments=route_assignments,
        )
        if route_assignments
        else {}
    )
    ordered_results = []
    for item in targets:
        key = str(item["key"])
        result = immediate_results.get(key)
        if result is None:
            results = [
                route_results[route_key]
                for route_key in route_keys_by_target.get(key, [])
                if route_key in route_results
            ]
            result = next(
                (candidate for candidate in results if candidate.status != "success"),
                results[0]
                if results
                else TargetValidationResult(
                    status="failed",
                    code="RESTORE_REPOSITORY_NOT_FOUND",
                    message="The selected snapshot has no restorable repository data.",
                ),
            )
        ordered_results.append(
            {
                "key": key,
                "status": result.status,
                "code": result.code,
                "message": result.message,
                "details": result.details,
            }
        )
    status = (
        "success"
        if all(result["status"] == "success" for result in ordered_results)
        else "failed"
    )
    return {"status": status, "results": ordered_results}


def _restore_snapshot(
    *, organization_id: int, snapshot_id: int
) -> BackupSourceSnapshot:
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        id=snapshot_id,
    ).first()
    if snapshot is None:
        raise ValidationError(
            {"source_snapshot_id": "Selected source snapshot was not found."}
        )
    return snapshot


def _snapshot_directories(
    *, snapshot: BackupSourceSnapshot
) -> list[BackupSourceSnapshotDirectory]:
    directories = list(
        BackupSourceSnapshotDirectory.objects.filter(
            organization_id=snapshot.organization_id,
            source_snapshot=snapshot,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        .exclude(kopia_snapshot_id="")
        .order_by("id")
    )
    if not directories:
        raise ValidationError(
            {"source_snapshot_id": "Selected source snapshot has no restorable data."}
        )
    return directories


def _repositories_for_directories(
    *,
    organization_id: int,
    directories: list[BackupSourceSnapshotDirectory],
) -> dict[int, Repository]:
    repository_ids = {int(directory.repository_id) for directory in directories}
    repositories = {
        repository.id: repository
        for repository in Repository.objects.filter(
            organization_id=organization_id,
            id__in=repository_ids,
            status=Repository.Status.CREATED,
        )
    }
    missing = repository_ids - repositories.keys()
    if missing:
        raise ValidationError(
            {"repository_id": "A snapshot repository is unavailable."}
        )
    return repositories


def _validate_direct_nas_restore_target(
    *,
    repository: Repository,
    execution_target: ExecutionTarget,
    target_type: str,
) -> None:
    direct_nas = repository.repo_type == Repository.Type.NAS and not (
        repository.bind_node_type == Repository.BindNodeType.PROXY
        and repository.bind_node_id
    )
    if not direct_nas or target_type == "nas":
        return
    if (
        execution_target.node.role == NodeRole.AGENT
        and _direct_nas_agent_platform(execution_target.node) == "linux"
    ):
        return
    raise ValidationError(
        {
            "target_ref_id": (
                "Snapshots in a NAS repository without a bound Proxy can be "
                "restored only to a Linux Agent or a Source NAS."
            )
        }
    )


def _exception_result(exc: Exception) -> TargetValidationResult:
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            messages = [
                str(message)
                for values in exc.message_dict.values()
                for message in values
            ]
        else:
            messages = [str(message) for message in exc.messages]
        return TargetValidationResult(
            status="failed",
            code="RESTORE_TARGET_INCOMPATIBLE",
            message=" ".join(messages) or "Restore target validation failed.",
        )
    return TargetValidationResult(
        status="failed",
        code="TARGET_CONNECTION_FAILED",
        message="Restore target validation failed.",
    )


__all__ = ["validate_restore_targets"]
