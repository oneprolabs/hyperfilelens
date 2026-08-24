"""Service helpers for the NAS storage repository "repair" flow.

The repair page allows operators to:

* Edit the mutable fields of a NAS repository (display name, mount options,
  quota, quota monitor, SMB credentials) and persist the changes.
* Bind a Proxy when none is bound yet, which initializes the Kopia repository
  on that Proxy.
* Replace the bound Proxy with another online Proxy. We do **not** re-initialize
  the Kopia repository data, but we remount the NAS share on the new Proxy
  and unmount/delete the mount point on the previously bound Proxy.

The flow assumes that "busy" means: there is at least one backup configuration
that targets this repository and that currently has a running or pending
``Task`` of type ``backup``. The check is performed before the Proxy swap to
avoid mid-backup mount-point changes.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.interface import run_agent_task_sync
from apps.protection.models import BackupConfig
from apps.storage.repositories.models import (
    Credential,
    Repository,
    RepositoryLocationClaim,
    RepositoryTask,
)
from apps.storage.services.internal.nas_repository import (
    NASRepositoryError,
    check_proxy_nas_repository,
    nas_mount_point,
    nas_proxy_repository_subdir,
    nas_repository_payload,
    sync_proxy_mount_path_from_repo_status,
)
from apps.storage.services.internal.repository_create import (
    enqueue_repository_create_task,
)
from apps.storage.services.internal.repository_cleanup import (
    repository_cleanup_preflight,
)
from apps.storage.services.internal.repository_location import (
    ACTIVE_CLAIM_STATES,
    RepositoryLocationConflict,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from apps.storage.services.internal.repository_usage import (
    apply_capacity_from_config,
    enqueue_repository_usage_refresh,
)
from apps.storage.services.internal.repository_secrets import (
    build_credential_metadata,
    build_secret_payload,
    resolve_repository_secrets,
    sanitize_repository_config,
)
from apps.task.models import Task
from common.errors import AppError, FieldError

logger = logging.getLogger(__name__)

# Sentinel used to detect "bind_node_id not provided" (different from None
# which means "explicitly unbind").
_UNSET = object()

NAS_REPAIR_MUTABLE_CONFIG_FIELDS = frozenset(
    {
        "mount_options",
        "quota_gb",
        "quota_unit",
        "quota_alert_enabled",
        "quota_alert_threshold",
        "smb_username",
        "smb_password",
        "smb_domain",
        "proxy_repository_server_host",
    }
)

_ACTIVE_BACKUP_STATUSES = (Task.Status.PENDING, Task.Status.RUNNING)

NAS_BIND_BLOCKED_CODE = "STORAGE.NAS_BIND_BLOCKED"
NAS_BIND_RECOVERY_CONFIRMATION = "CLEAN UP AND BIND"
_RECOVERY_CAPABILITIES = frozenset(
    {
        "repository_cleanup_v1",
        "repository_cleanup_ownership_v1",
    }
)
_FAILED_PROVISION_STATUSES = frozenset(
    {
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }
)


def _enqueue_usage_refresh(repository: Repository, *, trigger: str) -> None:
    enqueue_repository_usage_refresh(
        organization_id=repository.organization_id,
        repository_ids=[repository.id],
        force=True,
        trigger=trigger,
    )


class NASRepositoryBusyError(DRFValidationError):
    """Raised when a backup is currently running against the repository."""


def _sanitize(message: str, config: dict | None) -> str:
    sanitized = str(message or "")
    if not isinstance(config, dict):
        return sanitized
    for value in config.values():
        text = str(value or "")
        if text and ("password" in sanitized.lower() or len(text) >= 6):
            sanitized = sanitized.replace(text, "***")
    return sanitized


def lookup_active_backup_task(
    *, organization_id: int, repository_id: int
) -> Task | None:
    """Return a running/pending backup ``Task`` for any backup config that
    targets this repository, or ``None`` if no such task exists.
    """
    config_ids = list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            repository_id=repository_id,
        ).values_list("id", flat=True)
    )
    if not config_ids:
        return None
    candidates = Task.objects.filter(
        organization_id=organization_id,
        task_type=Task.Type.BACKUP,
        status__in=_ACTIVE_BACKUP_STATUSES,
    ).order_by("-created_at", "-id")
    for task in candidates:
        payload = task.request_payload if isinstance(task.request_payload, dict) else {}
        try:
            config_id = int(payload.get("backup_config_id") or 0)
        except (TypeError, ValueError):
            config_id = 0
        if config_id in config_ids:
            return task
    return None


def _validate_proxy_node(*, organization_id: int, node_id: int) -> Node:
    node = Node.objects.filter(
        id=node_id,
        organization_id=organization_id,
        role=NodeRole.PROXY,
        is_deleted=False,
    ).first()
    if node is None:
        raise DRFValidationError(
            {"bind_node_id": "Bound proxy node not found in this organization."}
        )
    if node.availability != Node.Availability.ONLINE:
        raise DRFValidationError(
            {"bind_node_id": 'Proxy node "%s" is not online.' % node.name}
        )
    return node


def _check_associated_backups_idle(*, organization_id: int, repository_id: int) -> None:
    active = lookup_active_backup_task(
        organization_id=organization_id,
        repository_id=repository_id,
    )
    if active is None:
        return
    raise NASRepositoryBusyError(
        {
            "detail": (
                "A backup task is currently running for this repository. "
                "Please retry after it finishes."
            ),
            "task_id": active.id,
            "task_uuid": str(active.task_uuid),
            "task_status": active.status,
            "task_display_name": active.display_name,
        }
    )


def _check_unbound_nas_has_no_associated_sources(
    *, organization_id: int, repository_id: int
) -> None:
    """Preserve the existing first-bind guard for associated backup sources."""
    if BackupConfig.objects.filter(
        organization_id=organization_id,
        repository_id=repository_id,
    ).exists():
        raise DRFValidationError(
            {
                "bind_node_id": (
                    "Cannot bind a proxy node after this NAS repository has "
                    "associated backup sources."
                )
            }
        )


def _node_capabilities(node: Node) -> set[str]:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    values = inventory.get("capabilities", metadata.get("capabilities", []))
    return {str(value) for value in values} if isinstance(values, list) else set()


def _claim_failed_provisioning_evidence(
    *, repository: Repository, claim: RepositoryLocationClaim
) -> bool:
    """Return whether a residual Claim came from a failed durable provision.

    The physical path is intentionally never returned to an API caller.  The
    parent product task and the Agent payload must both point at this exact
    repository generation and Direct NAS subrepository before destructive
    recovery is offered.
    """
    if not claim.owner_node_id:
        return False
    node_tasks = (
        NodeTask.objects.filter(
            organization_id=repository.organization_id,
            node_id=claim.owner_node_id,
            parent_task__task_type=Task.Type.BACKUP_CONFIG_PROVISION,
            parent_task__status__in=_FAILED_PROVISION_STATUSES,
            kind__in=["repo.initialize", "repo.status"],
        )
        .select_related("parent_task")
        .order_by("-created_at", "-id")[:50]
    )
    expected_root = str(claim.root_path or "").strip("/")
    for node_task in node_tasks:
        parent_payload = (
            node_task.parent_task.request_payload
            if isinstance(node_task.parent_task.request_payload, dict)
            else {}
        )
        if str(parent_payload.get("repository_id") or "") != str(repository.id):
            continue
        payload = node_task.payload if isinstance(node_task.payload, dict) else {}
        repository_payload = payload.get("repository")
        repository_payload = (
            repository_payload if isinstance(repository_payload, dict) else {}
        )
        payload_repository_id = (
            payload.get("repository_id") or repository_payload.get("id")
        )
        if payload_repository_id not in (None, "") and str(payload_repository_id) != str(
            repository.id
        ):
            continue
        if str(repository_payload.get("subdir") or "").strip("/") != expected_root:
            continue
        return True
    return False


def nas_proxy_binding_preflight(
    *, repository: Repository, bind_node_id: int
) -> dict[str, Any]:
    """Describe whether an unbound NAS repository can bind a Proxy safely."""
    selected_proxy = _validate_proxy_node(
        organization_id=repository.organization_id,
        node_id=int(bind_node_id),
    )
    direct_claims = list(
        RepositoryLocationClaim.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
            scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
            state__in=ACTIVE_CLAIM_STATES,
        ).order_by("id")
    )
    dependency_preflight = repository_cleanup_preflight(repository=repository)
    dependency_blockers = [
        item
        for item in dependency_preflight["blockers"]
        if item.get("code") != "repository_ownership_unverified"
    ]
    owners = {
        node.id: node
        for node in Node.objects.filter(
            organization_id=repository.organization_id,
            id__in=[claim.owner_node_id for claim in direct_claims if claim.owner_node_id],
            is_deleted=False,
        )
    }
    owner_details = []
    for claim in direct_claims:
        owner = owners.get(claim.owner_node_id)
        owner_details.append(
            {
                "node_id": claim.owner_node_id,
                "node_name": owner.name if owner is not None else "",
                "node_role": owner.role if owner is not None else "unknown",
                "node_online": bool(
                    owner is not None
                    and owner.availability == Node.Availability.ONLINE
                ),
                "claim_state": claim.state,
            }
        )

    if not direct_claims and not dependency_blockers:
        return {
            "allowed": True,
            "recovery_eligible": False,
            "blocker_code": "",
            "required_action": "bind",
            "claim_count": 0,
            "claim_states": [],
            "owners": [],
            "selected_proxy": {
                "node_id": selected_proxy.id,
                "node_name": selected_proxy.name,
                "node_role": selected_proxy.role,
            },
            "confirmation_required": False,
            "message": "The selected Proxy can be bound to this NAS repository.",
        }

    residual_claims = [
        claim
        for claim in direct_claims
        if claim.state == RepositoryLocationClaim.State.RESIDUAL
    ]
    failed_provisioning_claims = [
        claim
        for claim in residual_claims
        if _claim_failed_provisioning_evidence(repository=repository, claim=claim)
    ]
    missing_capability = []
    unavailable_owners = []
    for claim in residual_claims:
        owner = owners.get(claim.owner_node_id)
        if owner is None or owner.availability != Node.Availability.ONLINE:
            unavailable_owners.append(claim.id)
            continue
        capabilities = _node_capabilities(owner)
        required = sorted(_RECOVERY_CAPABILITIES - capabilities)
        if required:
            missing_capability.append(
                {
                    "node_id": owner.id,
                    "node_name": owner.name,
                    "node_role": owner.role,
                    "missing_capabilities": required,
                }
            )

    recovery_eligible = bool(
        residual_claims
        and len(residual_claims) == len(direct_claims)
        and len(failed_provisioning_claims) == len(residual_claims)
        and not dependency_blockers
        and not missing_capability
        and not unavailable_owners
    )
    if dependency_blockers:
        blocker_code = "DIRECT_NAS_BIND_DEPENDENCIES"
        required_action = "remove_dependencies"
        message = (
            "This NAS repository still has dependent sources, snapshots, restores, "
            "or active operations. Remove those dependencies before binding a Proxy."
        )
    elif missing_capability:
        blocker_code = "DIRECT_NAS_OWNER_UPGRADE_REQUIRED"
        required_action = "upgrade_owner_node"
        first = missing_capability[0]
        role = str(first["node_role"] or "node").title()
        message = (
            f'{role} "{first["node_name"]}" cannot verify and clean the retained '
            "repository target. Upgrade that node, then retry."
        )
    elif unavailable_owners:
        blocker_code = "DIRECT_NAS_OWNER_UNAVAILABLE"
        required_action = "bring_owner_online"
        message = (
            "The node that owns a retained Direct NAS target is unavailable. "
            "Bring that node online, then retry."
        )
    elif len(residual_claims) != len(direct_claims):
        blocker_code = "DIRECT_NAS_ACTIVE_TARGETS"
        required_action = "retry_or_remove_sources"
        message = (
            "This Direct NAS repository still has active physical targets. Retry or "
            "remove the related backup sources before binding a Proxy."
        )
    elif len(failed_provisioning_claims) != len(residual_claims):
        blocker_code = "DIRECT_NAS_RESIDUAL_UNVERIFIED"
        required_action = "recreate_repository"
        message = (
            "The retained physical target cannot be tied to a failed provisioning "
            "attempt. Recreate the NAS repository or contact support for review."
        )
    else:
        blocker_code = "DIRECT_NAS_FAILED_PROVISIONING_RESIDUAL"
        required_action = "cleanup_and_bind"
        message = (
            f"{len(residual_claims)} retained Direct NAS target(s) from a failed "
            "provisioning attempt must be ownership-verified and cleaned before "
            "the selected Proxy can be bound."
        )
    return {
        "allowed": False,
        "recovery_eligible": recovery_eligible,
        "blocker_code": blocker_code,
        "required_action": required_action,
        "claim_count": len(direct_claims),
        "claim_states": sorted({claim.state for claim in direct_claims}),
        "owners": owner_details,
        "selected_proxy": {
            "node_id": selected_proxy.id,
            "node_name": selected_proxy.name,
            "node_role": selected_proxy.role,
        },
        "confirmation_required": recovery_eligible,
        "message": message,
        "dependency_blockers": dependency_blockers,
        "missing_capability": missing_capability,
        "recovery_claim_ids": (
            [claim.id for claim in residual_claims] if recovery_eligible else []
        ),
    }


def _raise_bind_blocked(preflight: dict[str, Any]) -> None:
    public_preflight = {
        key: value for key, value in preflight.items() if key != "recovery_claim_ids"
    }
    raise AppError(
        code=NAS_BIND_BLOCKED_CODE,
        status=409,
        retryable=False,
        title="NAS Proxy binding blocked",
        diagnostic=str(preflight.get("message") or "NAS Proxy binding is blocked."),
        meta={"binding_blocker": public_preflight},
        field_errors=[
            FieldError(
                field="bind_node_id",
                code=str(preflight.get("blocker_code") or NAS_BIND_BLOCKED_CODE),
                message=str(preflight.get("message") or "NAS Proxy binding is blocked."),
            )
        ],
    )


def _unmount_on_old_proxy(
    *, organization_id: int, repository: Repository, old_node_id: int
) -> None:
    """Best-effort unmount and cleanup of the old proxy mount point.

    We do not raise when the unmount task fails because the repository's
    authoritative state (the DB row) is already updated. We only log the
    failure so operators can act on it.
    """
    mount_point = nas_mount_point(repository, node_id=old_node_id)
    payload = {
        "mount_point": mount_point,
        "repository_id": repository.id,
        "remove_mount_point": True,
    }
    try:
        run_agent_task_sync(
            organization_id=organization_id,
            node_id=old_node_id,
            kind="nas.unmount",
            payload=payload,
            correlation_type="storage_repository",
            correlation_id=str(repository.id),
            wait_timeout_seconds=60,
        )
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning(
            "Failed to unmount NAS repository %s on old proxy %s: %s",
            repository.id,
            old_node_id,
            exc,
        )


def _remount_on_new_proxy(
    *, organization_id: int, repository: Repository, new_node: Node
) -> None:
    """Validate mount + kopia status on the new proxy. We do not recreate the
    repository; we just ensure the new proxy can mount the share and run
    ``kopia repository status`` against the existing kopia repository.
    """
    payload = {
        "repository": nas_repository_payload(
            repository=repository,
            subdir=nas_proxy_repository_subdir(repository),
            node_id=new_node.id,
        ),
        # Rebinding is an explicit storage revalidation.  If the new Proxy
        # has a stale read-only/source-mismatched managed mount, let the Agent
        # repair it before probing the existing repository.
        "repair_mount": True,
        "allow_ownership_adoption": RepositoryLocationClaim.objects.filter(
            repository=repository,
            scope=RepositoryLocationClaim.Scope.REPOSITORY,
            state=RepositoryLocationClaim.State.OWNED,
            ownership_verified_at__isnull=True,
            legacy_adoption_required=True,
        ).exists(),
    }
    logger.info(
        "NAS repository remount on new proxy repository_id=%s node_id=%s",
        repository.id,
        new_node.id,
    )
    try:
        outcome = run_agent_task_sync(
            organization_id=organization_id,
            node_id=new_node.id,
            kind="repo.status",
            payload=payload,
            correlation_type="storage_repository",
            correlation_id=str(repository.id),
            wait_timeout_seconds=180,
        )
    except Exception as exc:
        raise NASRepositoryError(str(exc)) from exc
    if outcome.task.status != "success":
        message = str(getattr(outcome.task, "last_error", "") or "").strip()
        if not message and isinstance(outcome.result, dict):
            message = str(
                outcome.result.get("error") or outcome.result.get("stderr") or ""
            ).strip()
        raise NASRepositoryError(message or "NAS repository mount failed on new proxy.")
    if not (
        isinstance(outcome.result, dict)
        and outcome.result.get("ownership_verified") is True
    ):
        raise NASRepositoryError(
            "Proxy did not verify repository ownership. Upgrade the Proxy and retry.",
            error_code="REPOSITORY_OWNERSHIP_INVALID",
        )
    mark_repository_location_ownership_verified(
        repository,
        owner_node_id=new_node.id,
        repository_subdir=nas_proxy_repository_subdir(repository),
    )
    sync_proxy_mount_path_from_repo_status(repository, outcome.result)


def _apply_config_updates(
    repository: Repository, config_updates: dict[str, Any]
) -> None:
    """Merge the partial config updates into the repository's config dict.

    Special handling:

    * ``smb_password`` is left untouched when the incoming value is an empty
      string (frontend semantics: "leave blank to keep current password").
    """
    base_config = dict(repository.config or {})
    for key, value in config_updates.items():
        if key == "smb_password" and (value is None or str(value).strip() == ""):
            continue
        if key == "smb_password":
            continue
        base_config[key] = value
    if repository.credential_id:
        repository.config = sanitize_repository_config(base_config)
    else:
        repository.config = base_config
    apply_capacity_from_config(repository)


def _rotate_smb_credential_if_needed(
    repository: Repository,
    config_updates: dict[str, Any],
) -> None:
    credential_fields = {"smb_username", "smb_password", "smb_domain"} & set(
        config_updates
    )
    if not credential_fields:
        return
    if (
        credential_fields == {"smb_password"}
        and not str(config_updates.get("smb_password") or "").strip()
    ):
        return
    credential = None
    existing = {}
    if repository.credential_id:
        credential = Credential.objects.filter(
            id=repository.credential_id,
            organization_id=repository.organization_id,
        ).first()
    if credential is not None:
        existing = credential.get_secret_payload()
    else:
        existing = resolve_repository_secrets(repository)
    secret_payload = build_secret_payload(
        repository_type=repository.repo_type,
        nas_protocol=repository.nas_protocol,
        config=repository.config,
        credential_payload={
            "smb_password": config_updates.get("smb_password"),
        },
        existing_secrets=existing,
    )
    metadata = build_credential_metadata(
        repository_type=repository.repo_type,
        config=repository.config,
        credential_payload={},
    )
    if credential is None:
        credential = Credential(
            organization_id=repository.organization_id,
            credential_type=Credential.Type.SMB,
            metadata=metadata,
        )
        credential.set_secret_payload(secret_payload)
        credential.save()
        repository.credential_id = credential.id
        repository.config = sanitize_repository_config(repository.config)
        repository.save(update_fields=["config", "credential_id", "updated_at"])
        return
    credential.credential_type = Credential.Type.SMB
    credential.metadata = metadata
    credential.set_secret_payload(secret_payload)
    credential.save(
        update_fields=["credential_type", "metadata", "secret_cipher", "updated_at"]
    )


def _set_proxy_mount_path(
    repository: Repository, *, node_id: int, overwrite: bool = False
) -> None:
    config = dict(repository.config or {})
    if not overwrite and str(config.get("proxy_mount_path") or "").strip():
        return
    config["proxy_mount_path"] = nas_mount_point(repository, node_id=node_id)
    repository.config = config


def _lock_repository_for_repair(repository: Repository) -> Repository:
    """Lock the same repository state that was validated by the API request."""
    locked = Repository.objects.select_for_update().get(
        pk=repository.id,
        organization_id=repository.organization_id,
    )
    expected = (
        repository.status,
        repository.bind_node_type,
        repository.bind_node_id,
    )
    current = (
        locked.status,
        locked.bind_node_type,
        locked.bind_node_id,
    )
    if current != expected:
        raise DRFValidationError(
            {
                "detail": (
                    "Repository state changed while the repair was being accepted. "
                    "Refresh and retry."
                )
            }
        )
    return locked


def _apply_mutable_updates(
    repository: Repository,
    *,
    name: str | None,
    config_updates: dict[str, Any],
) -> None:
    if name is not None and str(name).strip():
        repository.name = str(name).strip()
    if config_updates:
        _apply_config_updates(repository, config_updates)
        _rotate_smb_credential_if_needed(repository, config_updates)


def repair_nas_repository(
    *,
    repository: Repository,
    name: str | None = None,
    config_updates: dict[str, Any] | None = None,
    bind_node_id: Any = _UNSET,
    cleanup_failed_provisioning_targets: bool = False,
    cleanup_confirmation: str = "",
    requested_by=None,
) -> Repository:
    """Repair a NAS storage repository.

    :param bind_node_id: ``None`` to explicitly clear the binding (only valid
        when the repository is currently unbound), an int to set/replace the
        binding, or the sentinel ``_UNSET`` (default) to leave the binding
        unchanged.
    """
    if repository.repo_type != Repository.Type.NAS:
        raise DRFValidationError(
            {"detail": "Repair is only supported for NAS repositories."}
        )
    if repository.status == Repository.Status.CREATING:
        raise DRFValidationError(
            {"detail": "Repository create or remount is still in progress."}
        )
    if repository.status in {
        Repository.Status.REMOVING,
        Repository.Status.REMOVED,
    }:
        raise DRFValidationError(
            {"detail": "Repository cannot be repaired during or after removal."}
        )
    config_updates = dict(config_updates or {})
    unsupported_fields = sorted(set(config_updates) - NAS_REPAIR_MUTABLE_CONFIG_FIELDS)
    if unsupported_fields:
        raise DRFValidationError(
            {
                "config": (
                    "These NAS repository fields cannot be modified: "
                    + ", ".join(unsupported_fields)
                )
            }
        )
    if repository.nas_protocol != Repository.NasProtocol.SMB:
        smb_fields = sorted(
            {"smb_username", "smb_password", "smb_domain"} & set(config_updates)
        )
        if smb_fields:
            raise DRFValidationError(
                {
                    "config": (
                        "SMB fields are not accepted for NFS: " + ", ".join(smb_fields)
                    )
                }
            )

    organization_id = repository.organization_id
    currently_bound = bool(
        repository.bind_node_type == Repository.BindNodeType.PROXY
        and repository.bind_node_id
    )
    initial_bind_node_id = repository.bind_node_id
    initial_proxy_mount_path = str(
        (repository.config or {}).get("proxy_mount_path") or ""
    ).strip()
    bind_node_provided = bind_node_id is not _UNSET
    if bind_node_provided:
        new_bind_node_id = bind_node_id
    else:
        new_bind_node_id = initial_bind_node_id
    bind_node_changed = bind_node_provided and (new_bind_node_id or None) != (
        initial_bind_node_id or None
    )
    if (
        bind_node_provided
        and currently_bound
        and new_bind_node_id
        and int(new_bind_node_id) == int(initial_bind_node_id or 0)
    ):
        raise DRFValidationError(
            {"bind_node_id": "The selected proxy node is the same as the current one."}
        )
    binding_preflight = None
    if not currently_bound and bind_node_changed and new_bind_node_id:
        _check_unbound_nas_has_no_associated_sources(
            organization_id=organization_id,
            repository_id=repository.id,
        )
        binding_preflight = nas_proxy_binding_preflight(
            repository=repository,
            bind_node_id=int(new_bind_node_id),
        )
        if not binding_preflight["allowed"] and not (
            cleanup_failed_provisioning_targets
            and cleanup_confirmation == NAS_BIND_RECOVERY_CONFIRMATION
            and binding_preflight["recovery_eligible"]
        ):
            _raise_bind_blocked(binding_preflight)

    # No binding intent and not currently bound: pure config save.
    if not currently_bound and not bind_node_changed:
        with transaction.atomic():
            repository = _lock_repository_for_repair(repository)
            _apply_mutable_updates(
                repository,
                name=name,
                config_updates=config_updates,
            )
            repository.save()
        _enqueue_usage_refresh(repository, trigger="storage.repository.repair_nas")
        return repository

    # First-time bind (currently unbound and binding to a new proxy).
    if not currently_bound and bind_node_changed:
        if not new_bind_node_id:
            raise DRFValidationError({"bind_node_id": "Select a proxy node to bind."})
        new_node = _validate_proxy_node(
            organization_id=organization_id, node_id=int(new_bind_node_id)
        )
        if binding_preflight and binding_preflight["recovery_eligible"]:
            with transaction.atomic():
                repository = _lock_repository_for_repair(repository)
                locked_preflight = nas_proxy_binding_preflight(
                    repository=repository,
                    bind_node_id=new_node.id,
                )
                if not locked_preflight["recovery_eligible"]:
                    _raise_bind_blocked(locked_preflight)
                _apply_mutable_updates(
                    repository,
                    name=name,
                    config_updates=config_updates,
                )
                repository.save()
                enqueue_repository_create_task(
                    repository=repository,
                    operation_type=RepositoryTask.OperationType.REPAIR_BIND,
                    requested_by=requested_by,
                    residual_recovery_claim_ids=locked_preflight[
                        "recovery_claim_ids"
                    ],
                    intended_bind_node_id=new_node.id,
                )
            repository.refresh_from_db()
            return repository
        try:
            with transaction.atomic():
                repository = _lock_repository_for_repair(repository)
                locked_preflight = nas_proxy_binding_preflight(
                    repository=repository,
                    bind_node_id=new_node.id,
                )
                if not locked_preflight["allowed"]:
                    _raise_bind_blocked(locked_preflight)
                _apply_mutable_updates(
                    repository,
                    name=name,
                    config_updates=config_updates,
                )
                repository.bind_node_type = Repository.BindNodeType.PROXY
                repository.bind_node_id = new_node.id
                repository.status = Repository.Status.CREATING
                _set_proxy_mount_path(repository, node_id=new_node.id)
                repository.save(
                    update_fields=[
                        "name",
                        "config",
                        "bind_node_type",
                        "bind_node_id",
                        "status",
                        "updated_at",
                    ]
                )
                reserve_repository_location(repository)
                enqueue_repository_create_task(
                    repository=repository,
                    operation_type=RepositoryTask.OperationType.REPAIR_BIND,
                )
        except RepositoryLocationConflict as exc:
            repository.refresh_from_db()
            raise DRFValidationError({"bind_node_id": str(exc.messages[0])}) from exc
        return repository

    # Currently bound. Either replacing the proxy or staying on the same one.
    if not bind_node_changed:
        with transaction.atomic():
            repository = _lock_repository_for_repair(repository)
            _apply_mutable_updates(
                repository,
                name=name,
                config_updates=config_updates,
            )
            if repository.bind_node_id:
                _set_proxy_mount_path(repository, node_id=int(repository.bind_node_id))
            repository.save()
        # Best-effort health refresh via repo.status on the existing proxy.
        try:
            check_proxy_nas_repository(repository)
        except (NASRepositoryError, ValidationError) as exc:
            logger.warning(
                "NAS repository %s health check failed after config update: %s",
                repository.id,
                exc,
            )
        repository.refresh_from_db()
        _enqueue_usage_refresh(repository, trigger="storage.repository.repair_nas")
        return repository

    # Bind change on an already-bound repository.
    if not new_bind_node_id:
        raise DRFValidationError(
            {
                "bind_node_id": "Select a different proxy node to replace the current one."
            }
        )
    # Busy check: any backup config tied to this repository must not have a
    # running/pending backup task.
    _check_associated_backups_idle(
        organization_id=organization_id, repository_id=repository.id
    )

    new_node = _validate_proxy_node(
        organization_id=organization_id, node_id=int(new_bind_node_id)
    )

    try:
        with transaction.atomic():
            repository = _lock_repository_for_repair(repository)
            _apply_mutable_updates(
                repository,
                name=name,
                config_updates=config_updates,
            )
            old_node_id = int(initial_bind_node_id or 0) or None
            repository.bind_node_type = Repository.BindNodeType.PROXY
            repository.bind_node_id = new_node.id
            repository.status = Repository.Status.CREATING
            _set_proxy_mount_path(repository, node_id=new_node.id, overwrite=True)
            repository.save(
                update_fields=[
                    "name",
                    "config",
                    "bind_node_type",
                    "bind_node_id",
                    "status",
                    "updated_at",
                ]
            )
            new_claim = reserve_repository_location(repository)
            logger.info(
                "NAS repository proxy swap remount accepted "
                "repository_id=%s old_node_id=%s new_node_id=%s "
                "new_claim_id=%s",
                repository.id,
                old_node_id,
                new_node.id,
                new_claim.id if new_claim else None,
            )
            enqueue_repository_create_task(
                repository=repository,
                operation_type=RepositoryTask.OperationType.REPAIR_REMOUNT,
                remount_previous_node_id=old_node_id,
                remount_previous_mount_path=initial_proxy_mount_path or None,
                remount_new_claim_id=new_claim.id if new_claim else None,
            )
    except RepositoryLocationConflict as exc:
        raise DRFValidationError({"bind_node_id": str(exc.messages[0])}) from exc
    return repository
