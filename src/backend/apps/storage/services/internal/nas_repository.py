from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ValidationError

from apps.node import agent_paths
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_log import (
    log_agent_dispatch,
    log_agent_exception,
    log_agent_outcome,
)
from apps.node.services.interface import run_agent_task_sync
from apps.node.services.capabilities import (
    REPOSITORY_OWNERSHIP_CAPABILITY,
    node_supports_capability,
)
from apps.storage.repositories.models import Repository
from apps.storage.repositories.models import RepositoryLocationClaim, RepositoryTask
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_MESSAGE,
    RepositoryAlreadyExistsError,
    RepositoryHealthTransportUnconfirmed,
    agent_task_transport_unconfirmed,
    agent_repository_failure_message,
    agent_result_has_repository_conflict,
)
from apps.storage.services.internal.repository_secrets import resolve_repository_secrets
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryAgentOperationError,
    RepositoryAgentOperationStateUnknown,
    repository_create_has_durable_agent_task,
    resolve_or_dispatch_repository_create_agent_task,
)

logger = logging.getLogger(__name__)


NAS_REPOSITORY_ROOT = "hp-repos"
NAS_PROXY_REPOSITORY_SUBDIR_TEMPLATE = (
    f"{NAS_REPOSITORY_ROOT}/storage-{{repository_id}}"
)
NAS_AGENT_REPOSITORY_SUBDIR_TEMPLATE = f"{NAS_REPOSITORY_ROOT}/agent-{{node_id}}"


class NASRepositoryError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "REPOSITORY_CREATE_FAILED"):
        super().__init__(message)
        self.error_code = error_code


def nas_agent_repository_subdir(node_id: int) -> str:
    return NAS_AGENT_REPOSITORY_SUBDIR_TEMPLATE.format(node_id=int(node_id))


def nas_proxy_repository_subdir(repository: Repository) -> str:
    return NAS_PROXY_REPOSITORY_SUBDIR_TEMPLATE.format(repository_id=int(repository.id))


def nas_mount_point(repository: Repository, *, node_id: int | None = None) -> str:
    data_dir = _node_data_dir(node_id)
    return agent_paths.repository_mount_point(
        repository.id, node_id=node_id, data_dir=data_dir
    )


def nas_restore_mount_point(repository: Repository, *, node_id: int) -> str:
    """Return the node-local temporary mount used to read a NAS for restore."""

    return agent_paths.restore_repository_mount_point(
        repository.id,
        node_id=node_id,
        data_dir=_node_data_dir(node_id),
    )


def nas_validation_mount_point(
    repository: Repository,
    *,
    validation_id: str,
    node_id: int,
) -> str:
    """Return an isolated node-local mount for one repository validation."""

    return agent_paths.validation_mount_point(
        validation_id,
        repository.id,
        node_id,
        data_dir=_node_data_dir(node_id),
    )


def _node_data_dir(node_id: int | None) -> str | None:
    data_dir = None
    if node_id:
        node = Node.objects.filter(pk=node_id).only("metadata").first()
        metadata = node.metadata if node and isinstance(node.metadata, dict) else {}
        inventory = metadata.get("inventory") if isinstance(metadata, dict) else {}
        if isinstance(inventory, dict):
            data_dir = str(inventory.get("root_path") or "").strip() or None
    return data_dir


def nas_repository_payload(
    *,
    repository: Repository,
    subdir: str,
    node_id: int | None = None,
    secrets_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from apps.storage.services.internal.repository_ownership import (
        ownership_payload_for_node,
    )

    config = repository.config if isinstance(repository.config, dict) else {}
    secrets_payload = (
        secrets_payload
        if isinstance(secrets_payload, dict)
        else resolve_repository_secrets(repository)
    )
    protocol = str(repository.nas_protocol or "").strip().lower()
    payload: dict[str, Any] = {
        "id": repository.id,
        "type": Repository.Type.NAS,
        "subdir": subdir,
        "kopia_password": str(secrets_payload.get("kopia_password") or "").strip(),
        "ownership": ownership_payload_for_node(
            repository,
            repository_subdir=subdir,
        ),
        "nas": {
            "resource_id": repository.id,
            "protocol": protocol,
            "server": str(config.get("server_address") or "").strip(),
            "mount_point": nas_mount_point(repository, node_id=node_id),
            "options": str(config.get("mount_options") or "").strip(),
            "storage_type": "nas_repository",
        },
    }
    nas = payload["nas"]
    if protocol == Repository.NasProtocol.SMB:
        nas["share"] = str(config.get("share_path") or "").strip().lstrip("/")
        nas["username"] = str(config.get("smb_username") or "").strip()
        nas["password"] = str(secrets_payload.get("smb_password") or "")
        domain = str(config.get("smb_domain") or "").strip()
        if domain:
            nas["domain"] = domain
    else:
        nas["export_path"] = str(config.get("share_path") or "").strip()
    return payload


def mount_point_from_repo_status_result(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    candidates: list[Any] = [
        result.get("mount_point"),
        result.get("resolved_mount_point"),
    ]
    for key in ("nas", "repository"):
        nested = result.get(key)
        if isinstance(nested, dict):
            candidates.append(nested.get("mount_point"))
            nas = nested.get("nas")
            if isinstance(nas, dict):
                candidates.append(nas.get("mount_point"))
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def sync_proxy_mount_path_from_repo_status(repository: Repository, result: Any) -> bool:
    mount_point = mount_point_from_repo_status_result(result)
    if not mount_point:
        return False
    config = dict(repository.config or {})
    if config.get("proxy_mount_path") == mount_point:
        return False
    config["proxy_mount_path"] = mount_point
    repository.config = config
    repository.save(update_fields=["config", "updated_at"])
    return True


def validate_proxy_for_repository(
    repository: Repository,
    *,
    require_online: bool = True,
) -> Node:
    if (
        repository.bind_node_type != Repository.BindNodeType.PROXY
        or not repository.bind_node_id
    ):
        raise ValidationError("NAS repository is not bound to a proxy node.")
    node = Node.objects.filter(
        id=repository.bind_node_id,
        organization_id=repository.organization_id,
        role=NodeRole.PROXY,
        is_deleted=False,
    ).first()
    if node is None:
        raise ValidationError("Bound proxy node not found.")
    if require_online and node.availability != Node.Availability.ONLINE:
        raise ValidationError(f'Bound proxy node "{node.name}" is not online.')
    return node


def initialize_proxy_nas_repository(
    repository: Repository,
    *,
    repository_task: RepositoryTask | None = None,
):
    return _run_proxy_nas_repository_task(
        repository,
        kind="repo.initialize",
        log_scope="storage nas repo init",
        repository_task=repository_task,
    )


def check_proxy_nas_repository(
    repository: Repository,
    *,
    health_only: bool = False,
    adopt_legacy_ownership: bool = True,
):
    return _run_proxy_nas_repository_task(
        repository,
        kind="repo.status",
        log_scope="storage nas repo check",
        health_only=health_only,
        adopt_legacy_ownership=adopt_legacy_ownership,
    )


def _run_proxy_nas_repository_task(
    repository: Repository,
    *,
    kind: str,
    log_scope: str,
    health_only: bool = False,
    adopt_legacy_ownership: bool = True,
    repository_task: RepositoryTask | None = None,
):
    async_create = kind == "repo.initialize" and repository_task is not None
    can_resume_offline = bool(
        async_create
        and repository_task is not None
        and repository_create_has_durable_agent_task(repository_task=repository_task)
    )
    try:
        node = validate_proxy_for_repository(
            repository,
            require_online=not can_resume_offline,
        )
    except ValidationError as exc:
        if health_only:
            raise RepositoryHealthTransportUnconfirmed(str(exc)) from exc
        raise
    supports_ownership = node_supports_capability(
        node,
        REPOSITORY_OWNERSHIP_CAPABILITY,
    )
    if kind == "repo.initialize" and not supports_ownership:
        raise NASRepositoryError(
            f'Agent "{node.name}" must be upgraded before creating this repository.',
            error_code="AGENT_UPGRADE_REQUIRED",
        )
    payload = {
        "repository": nas_repository_payload(
            repository=repository,
            subdir=nas_proxy_repository_subdir(repository),
            node_id=node.id,
        )
    }
    if health_only:
        payload["health_only"] = True
    payload["allow_ownership_adoption"] = (
        adopt_legacy_ownership
        and RepositoryLocationClaim.objects.filter(
            repository=repository,
            scope=RepositoryLocationClaim.Scope.REPOSITORY,
            state=RepositoryLocationClaim.State.OWNED,
            ownership_verified_at__isnull=True,
            legacy_adoption_required=True,
        ).exists()
    )
    log_agent_dispatch(
        log_scope,
        node_id=node.id,
        kind=kind,
        correlation_type="storage_repository",
        correlation_id=str(repository.id),
        repository_id=repository.id,
        org_id=repository.organization_id,
    )
    try:
        if async_create:
            outcome = resolve_or_dispatch_repository_create_agent_task(
                repository_task=repository_task,
                node=node,
                payload=payload,
                persisted_payload={
                    "repository_id": repository.id,
                    "operation_type": repository_task.operation_type,
                    "owner_node_id": node.id,
                },
            )
            if outcome.waiting or outcome.state_unknown:
                return outcome
        else:
            outcome = run_agent_task_sync(
                organization_id=repository.organization_id,
                node_id=node.id,
                kind=kind,
                payload=payload,
                correlation_type="storage_repository",
                correlation_id=str(repository.id),
                wait_timeout_seconds=180,
            )
    except Exception as exc:
        log_agent_exception(
            log_scope,
            node_id=node.id,
            kind=kind,
            exc=exc,
            correlation_type="storage_repository",
            correlation_id=str(repository.id),
            repository_id=repository.id,
        )
        if isinstance(exc, RepositoryAgentOperationStateUnknown):
            raise
        if isinstance(exc, RepositoryAgentOperationError):
            if agent_result_has_repository_conflict(exc.result):
                raise RepositoryAlreadyExistsError(
                    REPOSITORY_ALREADY_EXISTS_MESSAGE
                ) from exc
            message = agent_repository_failure_message(
                exc.result,
                last_error=str(exc),
            )
            raise NASRepositoryError(
                message or "NAS repository initialization failed.",
                error_code=str(
                    exc.result.get("error_code") or "REPOSITORY_CREATE_FAILED"
                ),
            ) from exc
        if health_only:
            raise RepositoryHealthTransportUnconfirmed(str(exc)) from exc
        raise NASRepositoryError(str(exc)) from exc
    log_agent_outcome(
        log_scope,
        outcome=outcome,
        node_id=node.id,
        kind=kind,
        correlation_type="storage_repository",
        correlation_id=str(repository.id),
        repository_id=repository.id,
    )
    if not async_create and agent_task_transport_unconfirmed(outcome):
        raise RepositoryHealthTransportUnconfirmed(
            "Agent repository probe did not return a terminal result."
        )
    outcome_result = outcome.result if isinstance(outcome.result, dict) else {}
    outcome_status = (
        "success"
        if async_create
        else str(getattr(getattr(outcome, "task", None), "status", ""))
    )
    if outcome_status != "success":
        if agent_result_has_repository_conflict(outcome_result):
            raise RepositoryAlreadyExistsError(REPOSITORY_ALREADY_EXISTS_MESSAGE)
        message = agent_repository_failure_message(
            outcome_result,
            last_error=str(
                getattr(getattr(outcome, "task", None), "last_error", "") or ""
            ),
        )
        result = outcome_result
        error_code = str(result.get("error_code") or "").strip()
        raise NASRepositoryError(
            message or "NAS repository initialization failed.",
            error_code=error_code or "REPOSITORY_CREATE_FAILED",
        )
    if outcome_result.get("ownership_verified") is not True:
        from apps.storage.services.internal.repository_location import (
            repository_has_legacy_location,
        )

        if (
            not supports_ownership
            and health_only
            and repository_has_legacy_location(
                repository,
                owner_node_id=node.id,
                repository_subdir=nas_proxy_repository_subdir(repository),
            )
        ):
            logger.info(
                "%s legacy compatibility repository_id=%s node_id=%s org_id=%s",
                log_scope,
                repository.id,
                node.id,
                repository.organization_id,
            )
            return outcome
        if supports_ownership:
            raise NASRepositoryError(
                "Agent declared repository ownership support but did not return an ownership result.",
                error_code="AGENT_PROTOCOL_INVALID",
            )
        raise NASRepositoryError(
            f'Agent "{node.name}" must be upgraded to verify repository ownership.',
            error_code="AGENT_UPGRADE_REQUIRED",
        )
    if not health_only:
        sync_proxy_mount_path_from_repo_status(repository, outcome_result)
    from apps.storage.services.internal.repository_location import (
        mark_repository_location_ownership_verified,
    )

    mark_repository_location_ownership_verified(
        repository,
        owner_node_id=node.id,
        repository_subdir=nas_proxy_repository_subdir(repository),
    )
    logger.info(
        "%s ok repository_id=%s node_id=%s org_id=%s",
        log_scope,
        repository.id,
        node.id,
        repository.organization_id,
    )
    return outcome
