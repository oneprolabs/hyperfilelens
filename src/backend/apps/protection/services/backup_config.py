from __future__ import annotations

import logging
import ntpath
import posixpath
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.capabilities import (
    REPOSITORY_OWNERSHIP_CAPABILITY,
    node_supports_capability,
)
from apps.node.services.internal.node_registry import node_is_available_for_work
from apps.node.services.internal.agent_log import log_agent_dispatch, log_agent_outcome
from apps.node.services.interface import run_agent_task_sync
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupPolicy,
    BackupSourceSnapshot,
    FileFilterRule,
)
from apps.protection.services.repository_compatibility import (
    validate_backup_repository_compatible,
)
from apps.restore.services.interface import create_restore_plan
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.source.services.internal.nas_display import (
    nfs_export_path,
    nas_protocol,
    smb_share,
)
from apps.source.services.internal.nas_path_normalize import (
    normalize_nfs_export_path,
    normalize_smb_share,
)
from apps.source.services.internal.nas_share_path import normalize_user_share_path
from apps.storage.repositories.models import (
    Repository,
    RepositoryLocationClaim,
    RepositoryUsageShard,
)
from apps.storage.services.internal.nas_repository import (
    mount_point_from_repo_status_result,
    nas_agent_repository_subdir,
    nas_proxy_repository_subdir,
    nas_repository_payload,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
    REPOSITORY_ALREADY_EXISTS_MESSAGE,
    agent_result_has_repository_conflict,
)
from apps.storage.services.internal.repository_location import (
    RepositoryLocationConflict,
    mark_repository_location_initialization_failed,
    mark_repository_location_initializing,
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    mark_repository_location_residual,
    repository_has_legacy_location,
    reserve_direct_nas_location,
)
from apps.storage.services.internal.repository_workload import (
    RepositoryWorkload,
    lock_repositories_for_workload,
)
from apps.storage.services.internal.repository_endpoints import (
    S3_ENDPOINT_EXTERNAL,
    S3_ENDPOINT_INTERNAL,
    normalize_s3_endpoint_host,
)
from common.errors import AppError

COMPRESSION_LEVELS = {"none", "balanced", "high"}
CONFLICT_MODES = {"skip", "overwrite"}
SOURCE_TYPES = {"agent", "nas"}
PATH_TYPES = {"directory", "file", "unknown"}

logger = logging.getLogger(__name__)


def _advance_pipeline(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> None:
    """Advance the source pipeline step to 3 after config creation."""
    from apps.source.services.internal.source_pipeline import set_pipeline_steps

    prefix = "agent" if source_type == "agent" else "nas"
    source_key = f"{prefix}:{source_ref_id}"
    updated = set_pipeline_steps(
        organization_id=organization_id,
        ids=[source_key],
        step=3,
    )
    if source_key not in updated:
        raise ValidationError({"source_ref_id": "Backup source not found."})


def _persist_backup_config_rows(
    *,
    organization_id: int,
    payload: dict[str, Any],
    directories_data: list[dict[str, Any]],
    recovery_plan_enabled: bool,
    recovery_plans_data: list[dict[str, Any]] | None,
) -> int:
    """Persist one configuration after quota and repository admission succeed."""
    config = BackupConfig.objects.create(organization_id=organization_id, **payload)

    dirs = [
        BackupConfigDirectory(
            organization_id=organization_id,
            backup_config=config,
            path=directory["path"],
            path_type=directory.get(
                "path_type", BackupConfigDirectory.PathType.UNKNOWN
            ),
            display_name=directory.get("display_name", ""),
            estimated_size_bytes=max(
                0, int(directory.get("estimated_size_bytes", 0) or 0)
            ),
            sort_order=index,
        )
        for index, directory in enumerate(directories_data)
    ]
    created_dirs = BackupConfigDirectory.objects.bulk_create(dirs)

    if recovery_plan_enabled and recovery_plans_data:
        for index, recovery_plan in enumerate(recovery_plans_data):
            directory_id = recovery_plan.get("backup_config_dir_id")
            if directory_id is None:
                matched = [
                    directory
                    for directory in created_dirs
                    if _same_or_ancestor_path(
                        directory.path,
                        recovery_plan["source_path"],
                    )
                ]
                directory_id = matched[0].id if matched else None
            if directory_id is None and recovery_plan.get("scope") != "snapshot":
                raise ValidationError(
                    {"recovery_plans": "Recovery plan directory not found."}
                )
            create_restore_plan(
                organization_id=organization_id,
                data={
                    "backup_config_id": config.id,
                    "backup_config_dir_id": directory_id,
                    "scope": recovery_plan.get("scope", "paths"),
                    "source_type": payload["source_type"],
                    "source_ref_id": payload["source_ref_id"],
                    "source_path": recovery_plan["source_path"],
                    "target_type": recovery_plan.get("target_type") or "agent",
                    "target_ref_id": recovery_plan.get("target_ref_id")
                    or recovery_plan.get("restore_host_id"),
                    "restore_dir": recovery_plan["restore_dir"],
                    "conflict_mode": recovery_plan["conflict_mode"],
                    "enabled": True,
                    "sort_order": index,
                },
            )

    config.refresh_from_db()
    _advance_pipeline(
        organization_id=organization_id,
        source_type=payload["source_type"],
        source_ref_id=payload["source_ref_id"],
    )
    return int(config.id)


def create_backup_config(
    *,
    organization_id: int,
    data: dict[str, Any],
) -> BackupConfig:
    payload = _config_payload(data, organization_id=organization_id)
    directories_data = payload.pop("directories", [])
    recovery_plans_data = payload.pop("recovery_plans", None)
    recovery_plan_enabled = payload.get("recovery_plan_enabled", False)

    if not directories_data:
        raise ValidationError({"directories": "At least one directory is required."})
    if recovery_plan_enabled and not recovery_plans_data:
        raise ValidationError(
            {
                "recovery_plans": "At least one recovery plan is required when recovery_plan_enabled is true."
            }
        )
    _validate_no_repository_self_backup(
        organization_id=organization_id,
        source_type=payload["source_type"],
        source_ref_id=payload["source_ref_id"],
        repository_id=payload["repository_id"],
        directories=directories_data,
    )
    logger.info(
        "backup config create started org_id=%s source_type=%s source_ref_id=%s repository_id=%s name=%s dir_count=%s",
        organization_id,
        payload["source_type"],
        payload["source_ref_id"],
        payload["repository_id"],
        payload["name"],
        len(directories_data),
    )
    from apps.iam.models import Organization
    from apps.subscription.services.interface import enforce_license_quota

    org = Organization.objects.filter(id=organization_id).first()

    provisioning_task_id: int | None = None
    direct_nas = False
    with transaction.atomic():
        from apps.source.services.internal.source_operation_fence import (
            assert_source_product_operation_allowed,
        )

        # Use the same Source-first lock ordering as Reset and Deregistration.
        # Once this check succeeds, those workflows will see the durable
        # provisioning task created below before they can acquire the Source.
        assert_source_product_operation_allowed(
            organization_id=organization_id,
            source_type=payload["source_type"],
            source_ref_id=payload["source_ref_id"],
        )
        repository = _lock_repository_for_backup_config(
            organization_id=organization_id,
            repository_id=payload["repository_id"],
        )
        # Keep admission and the consuming write in one transaction so instance
        # and organization quota locks remain held until commit.
        if org is not None:
            enforce_license_quota(org, "max_protected_sources", additional=1)
            enforce_license_quota(org, "max_storage_gb", additional=0)
        direct_nas = (
            repository.repo_type == Repository.Type.NAS
            and repository.bind_node_id is None
            and not str(repository.bind_node_type or "").strip()
        )
        if direct_nas:
            payload["status"] = BackupConfig.Status.PROVISIONING
        config_id = _persist_backup_config_rows(
            organization_id=organization_id,
            payload=payload,
            directories_data=directories_data,
            recovery_plan_enabled=recovery_plan_enabled,
            recovery_plans_data=recovery_plans_data,
        )
        if direct_nas:
            from apps.task.models import Task, TaskResource
            from apps.task.services.interface import create_task

            provision_task = create_task(
                organization_id=organization_id,
                task_type=Task.Type.BACKUP_CONFIG_PROVISION,
                display_name=f'Validate backup target for "{payload["name"]}"',
                trigger_type=Task.TriggerType.SYSTEM,
                request_payload={
                    "backup_config_id": config_id,
                    "repository_id": int(repository.id),
                },
                resources=[
                    {
                        "resource_type": TaskResource.Type.BACKUP_CONFIG,
                        "resource_id": config_id,
                        "is_primary": True,
                    },
                    {
                        "resource_type": TaskResource.Type.BACKUP_SOURCE,
                        "resource_subtype": payload["source_type"],
                        "resource_id": payload["source_ref_id"],
                    },
                    {
                        "resource_type": TaskResource.Type.REPOSITORY,
                        "resource_id": int(repository.id),
                    },
                ],
                idempotency_key=f"backup-config-provision:{config_id}",
            )
            provisioning_task_id = int(provision_task.id)
            BackupConfig.objects.filter(id=config_id).update(
                provisioning_task_uuid=provision_task.task_uuid,
            )

            def _queue_provision(task_id: int = provisioning_task_id) -> None:
                from apps.protection.services.backup_config_provision import (
                    queue_backup_config_provision_task,
                )

                queue_backup_config_provision_task(task_id=task_id)

            transaction.on_commit(_queue_provision)

    source_type = payload["source_type"]
    source_ref_id = payload["source_ref_id"]

    logger.info(
        "backup config create ok config_id=%s org_id=%s source_type=%s source_ref_id=%s repository_id=%s",
        config_id,
        organization_id,
        source_type,
        source_ref_id,
        payload["repository_id"],
    )
    if not direct_nas:
        _enqueue_direct_nas_usage_refresh(
            organization_id=organization_id,
            repository_ids=[payload["repository_id"]],
            trigger="protection.backup_config.create",
        )
    return BackupConfig.objects.get(pk=config_id)


def _config_payload(
    data: dict[str, Any],
    *,
    organization_id: int | None = None,
    current: BackupConfig | None = None,
) -> dict[str, Any]:
    effective_org_id = (
        organization_id
        if organization_id is not None
        else (current.organization_id if current is not None else None)
    )
    merged: dict[str, Any] = {}
    if current is not None:
        merged = {
            "name": current.name,
            "remark": current.remark,
            "source_type": current.source_type,
            "source_ref_id": current.source_ref_id,
            "repository_id": current.repository_id,
            "repository_endpoint_type": current.repository_endpoint_type,
            "backup_policy_id": current.backup_policy_id,
            "file_filter_rule_id": current.file_filter_rule_id,
            "compression_level": current.compression_level,
            "recovery_plan_enabled": current.recovery_plan_enabled,
        }
    merged.update(data)

    name = str(merged.get("name") or "").strip()
    if not name:
        raise ValidationError({"name": "Name is required."})

    source_type = str(merged.get("source_type") or "").strip().lower()
    if source_type not in SOURCE_TYPES:
        raise ValidationError(
            {"source_type": f"Must be one of: {', '.join(sorted(SOURCE_TYPES))}."}
        )

    source_ref_id = _int(merged, "source_ref_id")
    if source_ref_id <= 0:
        raise ValidationError({"source_ref_id": "Must be a positive integer."})

    repository_id = _int(merged, "repository_id")
    if repository_id <= 0:
        raise ValidationError({"repository_id": "Must be a positive integer."})

    backup_policy_id = _optional_int(merged, "backup_policy_id")
    file_filter_rule_id = _optional_int(merged, "file_filter_rule_id")

    repository = None
    if effective_org_id is not None:
        _validate_source_exists(
            organization_id=effective_org_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
        )
        repository = _validate_repository_exists(
            organization_id=effective_org_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
            repository_id=repository_id,
        )
        _validate_unique_source_config(
            organization_id=effective_org_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
            current_config_id=current.id if current is not None else None,
        )
        if backup_policy_id is not None:
            _validate_backup_policy_exists(
                organization_id=effective_org_id,
                policy_id=backup_policy_id,
            )
        if file_filter_rule_id is not None:
            _validate_file_filter_rule_exists(
                organization_id=effective_org_id,
                rule_id=file_filter_rule_id,
            )

    raw_compression = merged.get(
        "compression_level", BackupConfig.CompressionLevel.BALANCED
    )
    if not isinstance(raw_compression, str) or not raw_compression.strip():
        raise ValidationError(
            {"compression_level": "Must be one of: balanced, high, none."}
        )
    compression = raw_compression.strip().lower()
    if compression not in COMPRESSION_LEVELS:
        raise ValidationError(
            {"compression_level": "Must be one of: balanced, high, none."}
        )

    recovery_plan_enabled = bool(merged.get("recovery_plan_enabled", False))

    repository_endpoint_type = S3_ENDPOINT_EXTERNAL
    if repository is not None:
        repository_endpoint_type = _validated_repository_endpoint_type(
            repository=repository,
            requested=merged.get("repository_endpoint_type"),
            explicit="repository_endpoint_type" in data,
            require_explicit=current is None or current.repository_id != repository_id,
        )

    result = {
        "name": name,
        "remark": str(merged.get("remark") or "").strip(),
        "source_type": source_type,
        "source_ref_id": source_ref_id,
        "repository_id": repository_id,
        "repository_endpoint_type": repository_endpoint_type,
        "backup_policy_id": backup_policy_id,
        "file_filter_rule_id": file_filter_rule_id,
        "compression_level": compression,
        "recovery_plan_enabled": recovery_plan_enabled,
    }

    # Pass through directories and recovery_plans for creation
    directories: list[dict[str, Any]] | None = None
    if "directories" in data:
        directories = _validate_directories(data["directories"])
        if source_type == "nas" and effective_org_id is not None and directories:
            directories = _normalize_nas_directory_paths(
                organization_id=effective_org_id,
                source_ref_id=source_ref_id,
                directories=directories,
            )
        result["directories"] = directories
    if "recovery_plans" in data:
        result["recovery_plans"] = _validate_recovery_plans(
            data["recovery_plans"],
            organization_id=effective_org_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
            directories=directories or [],
        )

    return result


def _validate_source_exists(
    *, organization_id: int, source_type: str, source_ref_id: int
) -> None:
    if source_type == "agent":
        exists = Node.objects.filter(
            organization_id=organization_id,
            role=NodeRole.AGENT,
            id=source_ref_id,
            is_deleted=False,
        ).exists()
    else:
        exists = SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id=source_ref_id,
            is_deleted=False,
        ).exists()
    if not exists:
        raise ValidationError({"source_ref_id": "Backup source not found."})


def _validate_repository_exists(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    repository_id: int,
) -> Repository:
    return validate_backup_repository_compatible(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        repository_id=repository_id,
    )


def _lock_repository_for_backup_config(
    *,
    organization_id: int,
    repository_id: int,
) -> Repository:
    """Lock and revalidate a repository immediately before config persistence."""
    try:
        return lock_repositories_for_workload(
            organization_id=organization_id,
            repository_ids=[repository_id],
            workload=RepositoryWorkload.BACKUP_WRITE,
        )[0]
    except ValidationError as exc:
        raise ValidationError(
            {"repository_id": "Repository is no longer available for backup."}
        ) from exc


def _validated_repository_endpoint_type(
    *,
    repository: Repository,
    requested: object,
    explicit: bool,
    require_explicit: bool,
) -> str:
    requested_type = str(requested or "").strip().lower()
    if repository.repo_type != Repository.Type.S3:
        if explicit and requested_type not in ("", S3_ENDPOINT_EXTERNAL):
            raise ValidationError(
                {
                    "repository_endpoint_type": "Only object storage supports Endpoint selection."
                }
            )
        return S3_ENDPOINT_EXTERNAL

    config = repository.config if isinstance(repository.config, dict) else {}
    external = normalize_s3_endpoint_host(
        config.get("endpoint") or config.get("external_endpoint")
    )
    internal = normalize_s3_endpoint_host(config.get("internal_endpoint")) or external
    if not external:
        raise ValidationError(
            {"repository_endpoint_type": "Object storage external Endpoint is missing."}
        )
    if internal == external:
        if explicit and requested_type == S3_ENDPOINT_INTERNAL:
            raise ValidationError(
                {
                    "repository_endpoint_type": (
                        "Internal Endpoint is not available for this repository; use external."
                    )
                }
            )
        return S3_ENDPOINT_EXTERNAL

    if require_explicit and not explicit:
        raise ValidationError(
            {
                "repository_endpoint_type": (
                    "Select external or internal Endpoint for this object storage repository."
                )
            }
        )
    if requested_type not in {S3_ENDPOINT_EXTERNAL, S3_ENDPOINT_INTERNAL}:
        raise ValidationError(
            {"repository_endpoint_type": "Select external or internal Endpoint."}
        )
    return requested_type


def _normalized_nas_endpoint(
    *, protocol: str, server: object, share_path: object
) -> tuple[str, str, str]:
    normalized_protocol = str(protocol or "").strip().lower()
    normalized_server = str(server or "").strip().rstrip(".").lower()
    if normalized_protocol == Repository.NasProtocol.SMB:
        normalized_share = normalize_smb_share(str(share_path or "")).lower()
    else:
        normalized_share = normalize_nfs_export_path(str(share_path or ""))
    return normalized_protocol, normalized_server, normalized_share


def _validate_no_repository_self_backup(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    repository_id: int,
    directories: list[dict[str, Any]],
) -> None:
    if source_type != "nas" or not directories:
        return
    source = SourceResource.objects.filter(
        organization_id=organization_id,
        id=source_ref_id,
        resource_type=ResourceType.NAS,
        is_deleted=False,
    ).first()
    repository = (
        Repository.objects.filter(
            organization_id=organization_id,
            id=repository_id,
            repo_type=Repository.Type.NAS,
        )
        .exclude(status=Repository.Status.REMOVED)
        .first()
    )
    if source is None or repository is None:
        return

    source_config = source.config if isinstance(source.config, dict) else {}
    repository_config = repository.config if isinstance(repository.config, dict) else {}
    source_protocol = nas_protocol(source_config)
    source_share = (
        smb_share(source_config)
        if source_protocol == Repository.NasProtocol.SMB
        else nfs_export_path(resource_type=source.resource_type, config=source_config)
    )
    source_endpoint = _normalized_nas_endpoint(
        protocol=source_protocol,
        server=source_config.get("server") or source_config.get("server_address"),
        share_path=source_share,
    )
    repository_endpoint = _normalized_nas_endpoint(
        protocol=str(repository.nas_protocol or ""),
        server=repository_config.get("server_address"),
        share_path=repository_config.get("share_path"),
    )
    if not all(source_endpoint) or source_endpoint != repository_endpoint:
        return

    if repository.bind_node_type == Repository.BindNodeType.PROXY:
        repository_subdir = nas_proxy_repository_subdir(repository)
    else:
        if source.bound_node_id is None:
            return
        repository_subdir = nas_agent_repository_subdir(int(source.bound_node_id))
    protected_path = normalize_user_share_path(
        mount_root="",
        export_path="",
        user_path=repository_subdir,
    )
    for item in directories:
        path = str(item.get("path") or "")
        if _same_or_ancestor_path(path, protected_path) or _same_or_ancestor_path(
            protected_path, path
        ):
            raise ValidationError(
                {
                    "directories": (
                        f'Source path "{path}" overlaps target repository data at "{protected_path}". '
                        "Select a directory outside the target repository."
                    )
                }
            )


def _validate_unique_source_config(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    current_config_id: int | None = None,
) -> None:
    queryset = BackupConfig.objects.filter(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if current_config_id is not None:
        queryset = queryset.exclude(id=current_config_id)
    if queryset.exists():
        raise ValidationError(
            {"source_ref_id": "Backup source already has a backup configuration."}
        )


def _direct_nas_execution_node(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> Node:
    if source_type == "agent":
        node = Node.objects.filter(
            organization_id=organization_id,
            role=NodeRole.AGENT,
            id=source_ref_id,
            availability=Node.Availability.ONLINE,
            is_deleted=False,
        ).first()
        if node is None or not node_is_available_for_work(node):
            raise ValidationError(
                {"source_ref_id": "Agent source is unavailable or busy."}
            )
        return node
    if source_type == "nas":
        source = (
            SourceResource.objects.filter(
                organization_id=organization_id,
                id=source_ref_id,
                resource_type=ResourceType.NAS,
                is_deleted=False,
            )
            .select_related("bound_node")
            .first()
        )
        node = source.bound_node if source is not None else None
        if node is None or node.role != NodeRole.PROXY:
            raise ValidationError(
                {"source_ref_id": "NAS source is not bound to a proxy node."}
            )
        if source.availability != "online" or not node_is_available_for_work(node):
            raise ValidationError(
                {
                    "source_ref_id": "NAS source or bound proxy node is unavailable or busy."
                }
            )
        return node
    raise ValidationError({"source_type": "Unsupported backup source type."})


def _initialize_direct_nas_repository(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    repository_id: int,
    verify_existing: bool = True,
    parent_task=None,
    require_ownership_capability: bool = False,
) -> tuple[Repository, int, str] | None:
    """Initialize Direct NAS and identify claims to retain on caller rollback.

    A returned claim was newly initialized or recovered from a non-authoritative
    state during this call. If later local persistence fails, the caller must
    retain that claim as residual because the remote storage side effect cannot
    be rolled back with the database savepoint.
    """
    repository = (
        Repository.objects.filter(
            organization_id=organization_id,
            id=repository_id,
            repo_type=Repository.Type.NAS,
            bind_node_id__isnull=True,
        )
        .filter(Q(bind_node_type__isnull=True) | Q(bind_node_type=""))
        .first()
    )
    if repository is None:
        return None
    if repository.status != Repository.Status.CREATED:
        raise ValidationError(
            {"repository_id": "Repository is no longer available for backup."}
        )
    node = _direct_nas_execution_node(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    supports_ownership = node_supports_capability(
        node,
        REPOSITORY_OWNERSHIP_CAPABILITY,
    )
    if require_ownership_capability and not supports_ownership:
        raise AppError(
            code="AGENT_UPGRADE_REQUIRED",
            status=409,
            retryable=False,
            title="Agent upgrade required",
            diagnostic=(
                f'Agent "{node.name}" (version {str(node.version or "unknown")}) '
                "does not support repository ownership validation. "
                "Upgrade the Agent, then retry storage validation."
            ),
            meta={
                "node_id": int(node.id),
                "node_name": node.name,
                "current_version": str(node.version or ""),
                "missing_capability": "repository_ownership_v1",
            },
        )
    payload = nas_repository_payload(
        repository=repository,
        subdir=nas_agent_repository_subdir(node.id),
        node_id=node.id,
    )
    repository_subdir = str(payload["subdir"])
    previously_initialized = RepositoryUsageShard.objects.filter(
        organization_id=organization_id,
        repository_id=repository.id,
        usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
        node_id=node.id,
        repository_subdir=repository_subdir,
        is_active=True,
        last_success_checked_at__isnull=False,
    ).exists()
    location_requires_verification = False
    may_recover_existing_location = False
    allow_ownership_adoption = False
    try:
        with transaction.atomic():
            repository = (
                Repository.objects.select_for_update()
                .filter(
                    organization_id=organization_id,
                    id=repository.id,
                    repo_type=Repository.Type.NAS,
                    bind_node_id__isnull=True,
                )
                .filter(Q(bind_node_type__isnull=True) | Q(bind_node_type=""))
                .first()
            )
            if repository is None or repository.status != Repository.Status.CREATED:
                raise ValidationError(
                    {"repository_id": ("Repository is no longer available for backup.")}
                )
            claim = reserve_direct_nas_location(
                repository=repository,
                node_id=node.id,
                repository_subdir=repository_subdir,
            )
            may_recover_existing_location = claim.state in {
                RepositoryLocationClaim.State.INITIALIZING,
                RepositoryLocationClaim.State.OWNED,
                RepositoryLocationClaim.State.RESIDUAL,
            }
            # Legacy adoption is reserved for locations that were already
            # authoritative before the ownership-marker rollout. A newly
            # reserved/failed location must present this Repository's marker;
            # retrying may never adopt unknown pre-existing Kopia data.
            allow_ownership_adoption = (
                claim.state == RepositoryLocationClaim.State.OWNED
                and claim.ownership_verified_at is None
                and claim.legacy_adoption_required
            )
            if (
                previously_initialized
                and claim.state == RepositoryLocationClaim.State.OWNED
                and claim.ownership_verified_at is not None
                and not verify_existing
            ):
                return None
            location_requires_verification = (
                claim.state != RepositoryLocationClaim.State.OWNED
            )
            mark_repository_location_initializing(
                repository,
                owner_node_id=node.id,
                repository_subdir=repository_subdir,
                include_residual=True,
            )
    except RepositoryLocationConflict as exc:
        raise AppError(
            code=REPOSITORY_ALREADY_EXISTS_CODE,
            status=409,
            retryable=False,
            title="Repository location is already in use",
            diagnostic=str(exc.messages[0]),
            meta={"repository_type": repository.repo_type},
        ) from exc
    task_kind = "repo.status" if previously_initialized else "repo.initialize"
    correlation_id = f"{source_type}:{source_ref_id}:{repository_id}"
    log_agent_dispatch(
        "backup_config nas repo init",
        node_id=node.id,
        kind=task_kind,
        correlation_type="protection.backup_config",
        correlation_id=correlation_id,
        repository_id=repository_id,
    )
    try:
        outcome = run_agent_task_sync(
            organization_id=organization_id,
            node_id=node.id,
            kind=task_kind,
            payload={
                "repository": payload,
                "allow_ownership_adoption": allow_ownership_adoption,
                # Repository initialization and explicit retry are write-intent
                # paths. Revalidate a stale managed mount instead of silently
                # reusing a read-only mount.
                "repair_mount": True,
            },
            correlation_type="protection.backup_config",
            correlation_id=correlation_id,
            parent_task=parent_task,
            wait_timeout_seconds=180,
        )
    except Exception:
        if not previously_initialized or location_requires_verification:
            mark_repository_location_initialization_failed(
                repository,
                owner_node_id=node.id,
                repository_subdir=repository_subdir,
            )
        raise
    log_agent_outcome(
        "backup_config nas repo init",
        outcome=outcome,
        node_id=node.id,
        kind=task_kind,
        correlation_type="protection.backup_config",
        correlation_id=correlation_id,
        repository_id=repository_id,
    )
    if outcome.task.status != "success":
        if agent_result_has_repository_conflict(outcome.result):
            if not previously_initialized and may_recover_existing_location:
                verification_kind = "repo.status"
                log_agent_dispatch(
                    "backup_config nas repo ownership verify",
                    node_id=node.id,
                    kind=verification_kind,
                    correlation_type="protection.backup_config",
                    correlation_id=correlation_id,
                    repository_id=repository_id,
                )
                try:
                    verification = run_agent_task_sync(
                        organization_id=organization_id,
                        node_id=node.id,
                        kind=verification_kind,
                        payload={
                            "repository": payload,
                            "allow_ownership_adoption": allow_ownership_adoption,
                            "repair_mount": True,
                        },
                        correlation_type="protection.backup_config",
                        correlation_id=correlation_id,
                        parent_task=parent_task,
                        wait_timeout_seconds=180,
                    )
                except Exception as exc:
                    mark_repository_location_initialization_failed(
                        repository,
                        owner_node_id=node.id,
                        repository_subdir=repository_subdir,
                    )
                    raise AppError(
                        code=REPOSITORY_ALREADY_EXISTS_CODE,
                        status=409,
                        retryable=False,
                        title="Repository already exists",
                        diagnostic=(
                            "An existing repository was found, but ownership could not "
                            "be verified. The location was retained for review."
                        ),
                        meta={"repository_type": repository.repo_type},
                    ) from exc
                log_agent_outcome(
                    "backup_config nas repo ownership verify",
                    outcome=verification,
                    node_id=node.id,
                    kind=verification_kind,
                    correlation_type="protection.backup_config",
                    correlation_id=correlation_id,
                    repository_id=repository_id,
                )
                if verification.task.status == "success":
                    outcome = verification
                else:
                    mark_repository_location_initialization_failed(
                        repository,
                        owner_node_id=node.id,
                        repository_subdir=repository_subdir,
                    )
            elif not previously_initialized:
                # A first attempt cannot prove that pre-existing physical data
                # belongs to this repository. Retain the location for review;
                # a later explicit retry may verify the interrupted attempt.
                mark_repository_location_initialization_failed(
                    repository,
                    owner_node_id=node.id,
                    repository_subdir=repository_subdir,
                )
            if outcome.task.status != "success":
                raise AppError(
                    code=REPOSITORY_ALREADY_EXISTS_CODE,
                    status=409,
                    retryable=False,
                    title="Repository already exists",
                    diagnostic=REPOSITORY_ALREADY_EXISTS_MESSAGE,
                    meta={"repository_type": repository.repo_type},
                )
        if outcome.task.status != "success":
            if not previously_initialized or location_requires_verification:
                mark_repository_location_initialization_failed(
                    repository,
                    owner_node_id=node.id,
                    repository_subdir=repository_subdir,
                )
            message = str(getattr(outcome.task, "last_error", "") or "").strip()
            if not message and isinstance(outcome.result, dict):
                message = str(
                    outcome.result.get("error") or outcome.result.get("stderr") or ""
                ).strip()
            raise ValidationError(
                {
                    "repository_id": _sanitize_repository_error(
                        message or "NAS repository initialization failed.",
                        repository.config,
                    )
                }
            )
    ownership_verified = (
        isinstance(outcome.result, dict)
        and outcome.result.get("ownership_verified") is True
    )
    legacy_non_destructive_access = (
        not supports_ownership
        and previously_initialized
        and repository_has_legacy_location(
            repository,
            owner_node_id=node.id,
            repository_subdir=repository_subdir,
        )
    )
    if not ownership_verified and not legacy_non_destructive_access:
        mark_repository_location_initialization_failed(
            repository,
            owner_node_id=node.id,
            repository_subdir=repository_subdir,
        )
        error_code = (
            "AGENT_PROTOCOL_INVALID"
            if supports_ownership
            else "AGENT_UPGRADE_REQUIRED"
        )
        diagnostic = (
            "Agent declared repository ownership support but did not return "
            "an ownership result. Upgrade the Agent and retry storage validation."
            if supports_ownership
            else (
                "This repository has no verified legacy ownership record and the "
                "Agent cannot validate repository ownership. Upgrade the Agent and retry."
            )
        )
        raise AppError(
            code=error_code,
            status=409,
            retryable=False,
            title=(
                "Agent protocol is incompatible"
                if supports_ownership
                else "Agent upgrade required"
            ),
            diagnostic=diagnostic,
            meta={
                "node_id": int(node.id),
                "node_name": node.name,
                "current_version": str(node.version or ""),
                "missing_result": "ownership_verified",
            },
        )
    repository_unavailable = False
    with transaction.atomic():
        locked_repository = (
            Repository.objects.select_for_update()
            .filter(
                organization_id=organization_id,
                id=repository.id,
            )
            .first()
        )
        if (
            locked_repository is None
            or locked_repository.status != Repository.Status.CREATED
        ):
            if locked_repository is not None:
                mark_repository_location_residual(
                    locked_repository,
                    owner_node_id=node.id,
                    repository_subdir=repository_subdir,
                )
            repository_unavailable = True
        else:
            checked_at = timezone.now()
            mark_repository_location_owned(
                locked_repository,
                owner_node_id=node.id,
                repository_subdir=repository_subdir,
            )
            if ownership_verified:
                mark_repository_location_ownership_verified(
                    locked_repository,
                    owner_node_id=node.id,
                    repository_subdir=repository_subdir,
                )
            shard_defaults = {
                "is_active": True,
                "status": RepositoryUsageShard.Status.SUCCESS,
                "last_error": "",
                "last_checked_at": checked_at,
                "last_success_checked_at": checked_at,
            }
            mount_point = mount_point_from_repo_status_result(outcome.result)
            if mount_point:
                shard_defaults["mount_point"] = mount_point
            RepositoryUsageShard.objects.update_or_create(
                organization_id=organization_id,
                repository_id=locked_repository.id,
                usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
                node_id=node.id,
                repository_subdir=repository_subdir,
                defaults=shard_defaults,
            )
            if locked_repository.health != Repository.Health.ONLINE:
                locked_repository.health = Repository.Health.ONLINE
                locked_repository.last_checked_at = checked_at
                locked_repository.save(
                    update_fields=["health", "last_checked_at", "updated_at"]
                )

    if repository_unavailable:
        raise ValidationError(
            {"repository_id": "Repository is no longer available for backup."}
        )
    if not previously_initialized or location_requires_verification:
        return repository, int(node.id), repository_subdir
    return None


def ensure_direct_nas_repository_for_backup(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    repository_id: int,
) -> None:
    """Initialize a legacy direct-NAS backup config on its execution node."""
    _initialize_direct_nas_repository(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        repository_id=repository_id,
        verify_existing=False,
    )


def _should_initialize_direct_nas_repository(
    *,
    current: BackupConfig | None,
    payload: dict[str, Any],
) -> bool:
    if current is None:
        return True
    return any(
        payload.get(field) != getattr(current, field)
        for field in ("source_type", "source_ref_id", "repository_id")
    )


def _enqueue_direct_nas_usage_refresh(
    *,
    organization_id: int,
    repository_ids: list[int],
    trigger: str,
) -> None:
    direct_nas_ids = list(
        Repository.objects.filter(
            organization_id=organization_id,
            id__in=repository_ids,
            repo_type=Repository.Type.NAS,
            bind_node_id__isnull=True,
        )
        .filter(Q(bind_node_type__isnull=True) | Q(bind_node_type=""))
        .exclude(status=Repository.Status.REMOVED)
        .values_list("id", flat=True)
    )
    if not direct_nas_ids:
        return
    try:
        from apps.storage.services.internal.repository_usage import (
            enqueue_repository_usage_refresh,
        )

        enqueue_repository_usage_refresh(
            organization_id=organization_id,
            repository_ids=direct_nas_ids,
            force=True,
            trigger=trigger,
        )
    except Exception:
        logger.exception(
            "failed to enqueue direct NAS repository usage refresh org_id=%s repository_ids=%s trigger=%s",
            organization_id,
            direct_nas_ids,
            trigger,
        )


def _sanitize_repository_error(message: str, config: dict | None) -> str:
    sanitized = str(message or "")
    if not isinstance(config, dict):
        return sanitized
    for key, value in config.items():
        text = str(value or "")
        if not text:
            continue
        key_text = str(key).lower()
        if "password" in key_text or "secret" in key_text or len(text) >= 6:
            sanitized = sanitized.replace(text, "***")
    return sanitized


def _validate_backup_policy_exists(*, organization_id: int, policy_id: int) -> None:
    if not BackupPolicy.objects.filter(
        organization_id=organization_id, id=policy_id
    ).exists():
        raise ValidationError({"backup_policy_id": "Backup policy not found."})


def _validate_file_filter_rule_exists(*, organization_id: int, rule_id: int) -> None:
    if not FileFilterRule.objects.filter(
        organization_id=organization_id, id=rule_id
    ).exists():
        raise ValidationError({"file_filter_rule_id": "File filter rule not found."})


def _validate_restore_host_exists(
    *, organization_id: int | None, restore_host_id: int | None
) -> None:
    if organization_id is None or restore_host_id is None:
        return
    exists = Node.objects.filter(
        organization_id=organization_id,
        role=NodeRole.AGENT,
        id=restore_host_id,
        is_deleted=False,
    ).exists()
    if not exists:
        raise ValidationError({"restore_host_id": "Restore host not found."})


def _validate_restore_target_exists(
    *,
    organization_id: int | None,
    target_type: str,
    target_ref_id: int | None,
) -> None:
    if target_ref_id is None:
        raise ValidationError({"target_ref_id": "Restore target is required."})
    if organization_id is None:
        return
    if target_type == "agent":
        exists = Node.objects.filter(
            organization_id=organization_id,
            role=NodeRole.AGENT,
            id=target_ref_id,
            is_deleted=False,
        ).exists()
    elif target_type == "nas":
        exists = SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id=target_ref_id,
            is_deleted=False,
        ).exists()
    else:
        raise ValidationError({"target_type": "Must be one of: agent, nas."})
    if not exists:
        raise ValidationError({"target_ref_id": "Restore target not found."})


def _normalize_nas_directory_paths(
    *,
    organization_id: int,
    source_ref_id: int,
    directories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resource = SourceResource.objects.filter(
        organization_id=organization_id,
        id=source_ref_id,
        resource_type=ResourceType.NAS,
        is_deleted=False,
    ).first()
    if resource is None:
        return directories
    mount_root = _clean_dir_path(resource.effective_mount_point())
    if not mount_root:
        return directories
    config = resource.config if isinstance(resource.config, dict) else {}
    export_path = nfs_export_path(resource_type=resource.resource_type, config=config)
    normalized: list[dict[str, Any]] = []
    for item in directories:
        row = dict(item)
        row["path"] = normalize_user_share_path(
            mount_root=mount_root,
            export_path=export_path,
            user_path=str(row.get("path") or ""),
        )
        normalized.append(row)
    return normalized


def _validate_directories(directories: Any) -> list[dict[str, Any]]:
    if not isinstance(directories, list) or len(directories) == 0:
        raise ValidationError({"directories": "At least one source path is required."})
    seen_paths: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in directories:
        if not isinstance(item, dict):
            raise ValidationError(
                {"directories": "Each source path must be an object."}
            )
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            raise ValidationError({"directories": "Source path is required."})
        path = _clean_dir_path(raw_path)
        if not _is_absolute_source_path(path):
            raise ValidationError(
                {"directories": f"Source path must be absolute: {path}."}
            )
        path_type = str(item.get("path_type") or "unknown").strip().lower()
        if path_type not in PATH_TYPES:
            path_type = "unknown"
        if path in seen_paths:
            raise ValidationError({"directories": f"Duplicate source path: {path}."})
        for existing in seen_paths:
            if _same_or_ancestor_path(existing, path) or _same_or_ancestor_path(
                path, existing
            ):
                raise ValidationError(
                    {"directories": f"Parent/child source path conflict: {path}."}
                )
        seen_paths.add(path)
        result.append(
            {
                "path": path,
                "path_type": path_type,
                "display_name": str(item.get("display_name") or "").strip(),
                "estimated_size_bytes": _int(item, "estimated_size_bytes"),
            }
        )
    return result


def _validate_recovery_plans(
    recovery_plans: Any,
    *,
    organization_id: int | None,
    source_type: str,
    source_ref_id: int,
    directories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if recovery_plans in (None, ""):
        return []
    if not isinstance(recovery_plans, list):
        raise ValidationError({"recovery_plans": "recovery_plans must be a list."})

    configured_paths = [item["path"] for item in directories if item.get("path")]
    result: list[dict[str, Any]] = []
    for item in recovery_plans:
        if not isinstance(item, dict):
            raise ValidationError(
                {"recovery_plans": "Each recovery plan must be an object."}
            )
        scope = str(item.get("scope") or "paths").strip().lower()
        if scope not in {"snapshot", "paths"}:
            raise ValidationError({"scope": "Must be one of: paths, snapshot."})
        raw_source_path = str(item.get("source_path") or "").strip()
        raw_restore_dir = str(item.get("restore_dir") or "").strip()
        if not raw_restore_dir:
            raise ValidationError({"restore_dir": "Restore directory is required."})
        restore_dir = _clean_dir_path(raw_restore_dir)
        conflict_mode = str(item.get("conflict_mode") or "").strip().lower()
        if conflict_mode not in CONFLICT_MODES:
            raise ValidationError(
                {
                    "conflict_mode": f"Must be one of: {', '.join(sorted(CONFLICT_MODES))}."
                }
            )
        target_type = str(item.get("target_type") or "agent").strip().lower()
        target_ref_id = _optional_int(item, "target_ref_id")
        restore_host_id = _optional_int(item, "restore_host_id")
        if target_ref_id is None:
            target_ref_id = restore_host_id
        if restore_host_id is None and target_type == "agent":
            restore_host_id = target_ref_id
        _validate_restore_target_exists(
            organization_id=organization_id,
            target_type=target_type,
            target_ref_id=target_ref_id,
        )
        source_paths = (
            [""]
            if scope == "snapshot"
            else [_clean_dir_path(raw_source_path)]
            if raw_source_path
            else configured_paths
        )
        if not source_paths:
            raise ValidationError({"source_path": "Source path is required."})
        for source_path in source_paths:
            if scope != "snapshot" and not _is_absolute_source_path(source_path):
                raise ValidationError(
                    {"source_path": "Recovery source path must be absolute."}
                )
            if (
                scope != "snapshot"
                and configured_paths
                and not any(
                    _same_or_ancestor_path(path, source_path)
                    for path in configured_paths
                )
            ):
                raise ValidationError(
                    {
                        "source_path": f"Recovery source path is outside configured directories: {source_path}."
                    }
                )
            result.append(
                {
                    "scope": scope,
                    "source_type": source_type,
                    "source_ref_id": source_ref_id,
                    "source_path": source_path,
                    "backup_config_dir_id": None
                    if scope == "snapshot"
                    else _optional_int(item, "backup_config_dir_id"),
                    "target_type": target_type,
                    "target_ref_id": target_ref_id,
                    "restore_host_id": restore_host_id,
                    "restore_dir": restore_dir,
                    "conflict_mode": conflict_mode,
                }
            )
    return result


def _is_windows_path(path: str) -> bool:
    return "\\" in path or (len(path) >= 2 and path[1] == ":")


def _clean_dir_path(path: str) -> str:
    if _is_windows_path(path):
        return ntpath.normpath(path)
    return posixpath.normpath(path)


def _same_or_ancestor_path(ancestor_path: str, child_path: str) -> bool:
    if _is_windows_path(ancestor_path) or _is_windows_path(child_path):
        ancestor = ntpath.normcase(_clean_dir_path(ancestor_path).rstrip("\\/"))
        child = ntpath.normcase(_clean_dir_path(child_path).rstrip("\\/"))
        return child == ancestor or child.startswith(ancestor + "\\")
    ancestor = _clean_dir_path(ancestor_path).rstrip("/") or "/"
    child = _clean_dir_path(child_path).rstrip("/") or "/"
    return child == ancestor or child.startswith(ancestor + "/")


def _int(data: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(data.get(key, default) or default)
    except (TypeError, ValueError) as exc:
        raise ValidationError({key: f"{key} must be an integer."}) from exc


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    raw = data.get(key)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError({key: f"{key} must be an integer."}) from exc
    if value <= 0:
        raise ValidationError({key: "Must be a positive integer."})
    return value


@transaction.atomic
def delete_backup_config(*, config: BackupConfig) -> dict[str, Any]:
    config = BackupConfig.objects.select_for_update().get(
        id=config.id,
        organization_id=config.organization_id,
    )
    if config.status == BackupConfig.Status.PROVISION_FAILED:
        from apps.restore.models import RestorePlan
        from apps.task.models import Task

        task = Task.objects.select_for_update().filter(
            task_uuid=config.provisioning_task_uuid,
            organization_id=config.organization_id,
        ).first()
        if task is not None and task.status in {
            Task.Status.PENDING,
            Task.Status.WAITING,
            Task.Status.BLOCKED,
            Task.Status.RUNNING,
        }:
            raise ValidationError(
                {"detail": "Target storage validation is still running."}
            )
        if BackupSourceSnapshot.objects.filter(backup_config_id=config.id).exists():
            raise ValidationError(
                {"detail": "A backup configuration with snapshots cannot be discarded."}
            )
        if task is not None:
            result_payload = (
                dict(task.result_payload)
                if isinstance(task.result_payload, dict)
                else {}
            )
            result_payload["backup_config_discarded"] = True
            task.result_payload = result_payload
            task.save(update_fields=["result_payload", "updated_at"])
        config_id = int(config.id)
        organization_id = int(config.organization_id)
        source_type = config.source_type
        source_ref_id = int(config.source_ref_id)
        RestorePlan.objects.filter(
            organization_id=config.organization_id,
            backup_config_id=config.id,
        ).delete()
        config.delete()
        from apps.source.services.internal.source_pipeline import (
            PipelineStep,
            force_set_pipeline_steps,
        )

        force_set_pipeline_steps(
            organization_id=organization_id,
            ids=[f"{source_type}:{source_ref_id}"],
            step=PipelineStep.CONFIG,
        )
        return {"deleted": True, "id": config_id}
    raise ValidationError(
        {
            "detail": "Backup config deletion is not supported. Clean up the source endpoint instead."
        }
    )


def purge_backup_config_data_for_source(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> dict[str, int]:
    """Internal source cleanup path for removing backup artifacts tied to a source."""
    from apps.restore.models import RestorePlan

    configs = list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
        ).values_list("id", "repository_id")
    )
    config_ids = [row[0] for row in configs]
    if not config_ids:
        return {
            "backup_configs_removed": 0,
            "snapshots_removed": 0,
            "restore_plans_removed": 0,
        }
    repository_ids = sorted({int(row[1]) for row in configs})

    snapshots_removed = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        backup_config_id__in=config_ids,
    ).delete()[0]
    restore_plans_removed = RestorePlan.objects.filter(
        organization_id=organization_id,
        backup_config_id__in=config_ids,
    ).delete()[0]
    backup_configs_removed = BackupConfig.objects.filter(id__in=config_ids).delete()[0]
    _enqueue_direct_nas_usage_refresh(
        organization_id=organization_id,
        repository_ids=repository_ids,
        trigger="protection.backup_config.purge",
    )
    return {
        "backup_configs_removed": backup_configs_removed,
        "snapshots_removed": snapshots_removed,
        "restore_plans_removed": restore_plans_removed,
    }


def _sync_backup_config_directories(
    *,
    config: BackupConfig,
    directories_data: list[dict[str, Any]],
) -> None:
    if not directories_data:
        raise ValidationError({"directories": "At least one directory is required."})

    existing_by_path = {
        directory.path: directory
        for directory in BackupConfigDirectory.objects.filter(backup_config=config)
    }
    next_by_path = {directory["path"]: directory for directory in directories_data}

    created_or_updated: list[BackupConfigDirectory] = []
    for idx, directory_data in enumerate(directories_data):
        path = directory_data["path"]
        directory = existing_by_path.get(path)
        is_new = directory is None
        if directory is None:
            directory = BackupConfigDirectory(
                organization_id=config.organization_id,
                backup_config=config,
                path=path,
            )
        previous_path_type = (
            str(directory.path_type or "").strip().lower() if directory.pk else ""
        )
        incoming_path_type = (
            str(
                directory_data.get("path_type", BackupConfigDirectory.PathType.UNKNOWN)
                or BackupConfigDirectory.PathType.UNKNOWN
            )
            .strip()
            .lower()
        )
        # Clients often omit path_type; validator then sends "unknown". Keep the
        # stored concrete type so we do not falsely invalidate du caches.
        if (
            directory.pk is not None
            and incoming_path_type in {"", "unknown"}
            and previous_path_type in {"directory", "file"}
        ):
            directory.path_type = previous_path_type
        else:
            directory.path_type = (
                incoming_path_type or BackupConfigDirectory.PathType.UNKNOWN
            )
        directory.display_name = directory_data.get("display_name", "")
        # Preserve cached du estimates when the client omits/zeros the field on
        # unchanged paths. New paths, explicit directory<->file changes, and
        # verified positive->zero changes invalidate the cache so async
        # pre-cache can refresh them. Verified zero estimates are tracked by
        # size_estimated_at and remain valid across unchanged saves.
        incoming_estimate = directory_data.get("estimated_size_bytes", None)
        next_path_type = str(directory.path_type or "").strip().lower()
        previous_estimate = int(directory.estimated_size_bytes or 0)
        path_type_changed = (
            previous_path_type in {"directory", "file"}
            and next_path_type in {"directory", "file"}
            and previous_path_type != next_path_type
        )
        if incoming_estimate is not None and int(incoming_estimate or 0) > 0:
            directory.estimated_size_bytes = int(incoming_estimate)
            if (
                is_new
                or path_type_changed
                or previous_estimate != int(incoming_estimate)
            ):
                directory.size_estimated_at = None
        elif directory.pk is None:
            directory.estimated_size_bytes = max(0, int(incoming_estimate or 0))
            directory.size_estimated_at = None
        elif path_type_changed:
            directory.estimated_size_bytes = 0
            directory.size_estimated_at = None
        elif int(directory.estimated_size_bytes or 0) < 0:
            # Unavailable marker from a failed precache: an explicit directories
            # sync re-opens async retry (positive caches above stay intact).
            directory.estimated_size_bytes = 0
            directory.size_estimated_at = None
        elif (
            incoming_estimate is not None
            and int(incoming_estimate or 0) <= 0
            and previous_estimate > 0
            and directory.size_estimated_at is not None
        ):
            directory.estimated_size_bytes = 0
            directory.size_estimated_at = None
        directory.sort_order = idx
        directory.save()
        created_or_updated.append(directory)

    from apps.restore.models import RestorePlan

    for plan in RestorePlan.objects.filter(
        organization_id=config.organization_id,
        backup_config_id=config.id,
    ):
        if plan.scope == RestorePlan.Scope.SNAPSHOT:
            continue
        if not _is_absolute_source_path(plan.source_path):
            raise ValidationError(
                {
                    "directories": (
                        "Existing recovery plan source path must be absolute: "
                        f"{plan.source_path}."
                    )
                }
            )
        matched = [
            directory
            for directory in created_or_updated
            if _same_or_ancestor_path(directory.path, plan.source_path)
        ]
        if not matched:
            raise ValidationError(
                {
                    "directories": (
                        "Existing recovery plan source path is outside configured directories: "
                        f"{plan.source_path}."
                    )
                }
            )
        matched.sort(key=lambda directory: len(directory.path), reverse=True)
        directory = matched[0]
        if plan.backup_config_dir_id != directory.id:
            plan.backup_config_dir_id = directory.id
            plan.save(update_fields=["backup_config_dir_id", "updated_at"])

    removed_paths = set(existing_by_path) - set(next_by_path)
    if removed_paths:
        BackupConfigDirectory.objects.filter(
            backup_config=config,
            path__in=removed_paths,
        ).delete()


def update_backup_config(
    *,
    config: BackupConfig,
    data: dict[str, Any],
) -> BackupConfig:
    if config.status in {
        BackupConfig.Status.PROVISIONING,
        BackupConfig.Status.PROVISION_FAILED,
    }:
        raise ValidationError(
            {
                "detail": (
                    "Finish or retry target storage validation before editing this "
                    "backup configuration."
                )
            }
        )
    requested_repository_id = int(data.get("repository_id") or config.repository_id)
    if requested_repository_id != config.repository_id:
        raise ValidationError(
            {"repository_id": "Backup repository cannot be modified."}
        )

    previous_repository_id = config.repository_id
    previous_source_type = config.source_type
    previous_source_ref_id = config.source_ref_id
    logger.info(
        "backup config update started config_id=%s org_id=%s fields=%s",
        config.id,
        config.organization_id,
        sorted(data.keys()),
    )
    preflight_payload = _config_payload(data, current=config)
    source_identities = [
        (config.source_type, int(config.source_ref_id)),
        (
            str(preflight_payload["source_type"]),
            int(preflight_payload["source_ref_id"]),
        ),
    ]
    from apps.source.services.internal.source_operation_fence import (
        assert_no_active_backup_for_sources,
    )

    with transaction.atomic():
        assert_no_active_backup_for_sources(
            organization_id=config.organization_id,
            sources=source_identities,
        )
    if _should_initialize_direct_nas_repository(
        current=config, payload=preflight_payload
    ):
        _initialize_direct_nas_repository(
            organization_id=config.organization_id,
            source_type=preflight_payload["source_type"],
            source_ref_id=preflight_payload["source_ref_id"],
            repository_id=preflight_payload["repository_id"],
        )

    with transaction.atomic():
        assert_no_active_backup_for_sources(
            organization_id=config.organization_id,
            sources=source_identities,
        )
        config = BackupConfig.objects.select_for_update().get(pk=config.pk)
        requested_repository_id = int(data.get("repository_id") or config.repository_id)
        if requested_repository_id != config.repository_id:
            raise ValidationError(
                {"repository_id": "Backup repository cannot be modified."}
            )
        payload = _config_payload(data, current=config)
        directories_data = payload.pop("directories", None)
        payload.pop("recovery_plans", None)
        effective_directories = directories_data
        if effective_directories is None:
            effective_directories = list(
                config.directories.order_by("sort_order", "id").values(
                    "path", "path_type"
                )
            )
        _validate_no_repository_self_backup(
            organization_id=config.organization_id,
            source_type=payload["source_type"],
            source_ref_id=payload["source_ref_id"],
            repository_id=config.repository_id,
            directories=effective_directories,
        )
        for field in (
            "name",
            "remark",
            "source_type",
            "source_ref_id",
            "repository_endpoint_type",
            "backup_policy_id",
            "file_filter_rule_id",
            "compression_level",
            "recovery_plan_enabled",
        ):
            if field in payload:
                setattr(config, field, payload[field])
        config.save()
        if directories_data is not None:
            _sync_backup_config_directories(
                config=config,
                directories_data=directories_data,
            )
        source_changed = str(payload["source_type"]) != str(
            previous_source_type
        ) or int(payload["source_ref_id"]) != int(previous_source_ref_id)
        if source_changed:
            from apps.protection.services.directory_size_estimate import (
                invalidate_backup_config_directory_estimates,
            )

            invalidate_backup_config_directory_estimates(config=config)
        config.refresh_from_db()
    logger.info(
        "backup config update ok config_id=%s org_id=%s repository_id=%s",
        config.id,
        config.organization_id,
        config.repository_id,
    )
    if any(
        (
            previous_repository_id != config.repository_id,
            previous_source_type != config.source_type,
            previous_source_ref_id != config.source_ref_id,
        )
    ):
        _enqueue_direct_nas_usage_refresh(
            organization_id=config.organization_id,
            repository_ids=[previous_repository_id, config.repository_id],
            trigger="protection.backup_config.update",
        )
    return config


def _is_absolute_source_path(path: str) -> bool:
    clean_path = str(path or "").strip()
    if not clean_path:
        return False
    if _is_windows_path(clean_path):
        return ntpath.isabs(clean_path)
    return posixpath.isabs(clean_path)


__all__ = [
    "create_backup_config",
    "delete_backup_config",
    "purge_backup_config_data_for_source",
    "update_backup_config",
]
