"""Initialize or probe standalone-disk repositories on a Proxy node.

Mirrors :mod:`apps.storage.services.internal.nas_repository` but targets a
user-supplied directory on the Proxy node. The Agent-managed kopia engine
uses a strict create-only task for initialization and a separate connect-only
task for later probes.

See ``apps/agent/internal/engine/managed_backup.go`` for the agent-side
implementation of the ``repo.status`` task for ``proxy_fs`` repositories.
"""

from __future__ import annotations

import logging
import posixpath
from typing import Any

from django.core.exceptions import ValidationError

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.capabilities import (
    REPOSITORY_OWNERSHIP_CAPABILITY,
    node_supports_capability,
)
from apps.node.services.internal.agent_log import (
    log_agent_dispatch,
    log_agent_exception,
    log_agent_outcome,
)
from apps.node.services.interface import run_agent_task_sync
from apps.storage.repositories.models import Repository, RepositoryLocationClaim
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_MESSAGE,
    RepositoryAlreadyExistsError,
    agent_repository_failure_message,
    agent_result_has_repository_conflict,
)
from apps.storage.services.internal.repository_secrets import resolve_repository_secrets

logger = logging.getLogger(__name__)

PROXY_FS_LAYOUT_MANAGED_SUBDIR_V1 = "managed_subdir_v1"
PROXY_FS_MANAGED_DIRECTORY_PREFIX = "hfl-repo-"


class ProxyFSRepositoryError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "REPOSITORY_CREATE_FAILED"):
        super().__init__(message)
        self.error_code = error_code


def normalize_proxy_fs_base_dir(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or "\x00" in raw or not raw.startswith("/"):
        raise ValidationError(
            "Proxy filesystem base directory must be an absolute path."
        )
    normalized = posixpath.normpath(raw)
    if normalized == "/":
        raise ValidationError(
            "Proxy filesystem base directory cannot be the filesystem root."
        )
    return normalized


def configure_managed_proxy_fs_path(repository: Repository) -> bool:
    """Persist the HFL-owned child path for a newly created proxy_fs row."""
    if repository.repo_type != Repository.Type.PROXY_FS:
        return False
    config = dict(repository.config or {})
    base_dir = normalize_proxy_fs_base_dir(
        config.get("proxy_node_base_dir") or config.get("proxy_node_dir")
    )
    repository_dir = posixpath.join(
        base_dir,
        f"{PROXY_FS_MANAGED_DIRECTORY_PREFIX}{int(repository.id)}",
    )
    config.update(
        {
            "proxy_node_base_dir": base_dir,
            "proxy_node_dir": repository_dir,
            "proxy_fs_layout": PROXY_FS_LAYOUT_MANAGED_SUBDIR_V1,
        }
    )
    if repository.config == config:
        return False
    repository.config = config
    return True


def proxy_fs_uses_managed_subdir(repository: Repository) -> bool:
    config = repository.config if isinstance(repository.config, dict) else {}
    return (
        repository.repo_type == Repository.Type.PROXY_FS
        and config.get("proxy_fs_layout") == PROXY_FS_LAYOUT_MANAGED_SUBDIR_V1
    )


def validate_proxy_for_proxy_fs(repository: Repository) -> Node:
    """Resolve the bound Proxy node; raise if the binding is invalid/offline."""
    if repository.repo_type != Repository.Type.PROXY_FS:
        raise ValidationError("Repository is not a proxy_fs repository.")
    if (
        repository.bind_node_type != Repository.BindNodeType.PROXY
        or not repository.bind_node_id
    ):
        raise ValidationError(
            "Proxy filesystem repository is not bound to a proxy node."
        )
    node = Node.objects.filter(
        id=repository.bind_node_id,
        organization_id=repository.organization_id,
        role=NodeRole.PROXY,
        is_deleted=False,
    ).first()
    if node is None:
        raise ValidationError("Bound proxy node not found.")
    if node.availability != Node.Availability.ONLINE:
        raise ValidationError(f'Bound proxy node "{node.name}" is not online.')
    return node


def proxy_fs_repository_payload(repository: Repository) -> dict[str, Any]:
    """Build the agent-side ``repository`` payload for a proxy_fs repository."""
    from apps.storage.services.internal.repository_ownership import (
        ownership_payload_for_node,
    )

    config = repository.config if isinstance(repository.config, dict) else {}
    secrets_payload = resolve_repository_secrets(repository)
    payload = {
        "id": repository.id,
        "type": Repository.Type.PROXY_FS,
        "path": str(config.get("proxy_node_dir") or "").strip(),
        "kopia_password": str(secrets_payload.get("kopia_password") or "").strip(),
        "ownership": ownership_payload_for_node(repository),
    }
    if proxy_fs_uses_managed_subdir(repository):
        payload.update(
            {
                "base_path": str(config.get("proxy_node_base_dir") or "").strip(),
                "layout": PROXY_FS_LAYOUT_MANAGED_SUBDIR_V1,
            }
        )
    return payload


def initialize_proxy_fs_repository(repository: Repository):
    """Initialize a new proxy_fs repository on the bound Proxy node."""

    return _run_proxy_fs_repository_task(
        repository,
        kind="repo.initialize",
        log_scope="storage proxy_fs repo init",
    )


def check_proxy_fs_repository(
    repository: Repository,
    *,
    health_only: bool = False,
    adopt_legacy_ownership: bool = True,
):
    return _run_proxy_fs_repository_task(
        repository,
        kind="repo.status",
        log_scope="storage proxy_fs repo check",
        health_only=health_only,
        adopt_legacy_ownership=adopt_legacy_ownership,
    )


def _run_proxy_fs_repository_task(
    repository: Repository,
    *,
    kind: str,
    log_scope: str,
    health_only: bool = False,
    adopt_legacy_ownership: bool = True,
):
    """Run a strict initialize or connect-only probe on the bound Proxy."""

    node = validate_proxy_for_proxy_fs(repository)
    supports_ownership = node_supports_capability(
        node,
        REPOSITORY_OWNERSHIP_CAPABILITY,
    )
    if kind == "repo.initialize" and not supports_ownership:
        raise ProxyFSRepositoryError(
            f'Agent "{node.name}" must be upgraded before creating this repository.',
            error_code="AGENT_UPGRADE_REQUIRED",
        )
    payload = {
        "repository": proxy_fs_repository_payload(repository),
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
        raise ProxyFSRepositoryError(str(exc)) from exc
    log_agent_outcome(
        log_scope,
        outcome=outcome,
        node_id=node.id,
        kind=kind,
        correlation_type="storage_repository",
        correlation_id=str(repository.id),
        repository_id=repository.id,
    )
    if outcome.task.status != "success":
        if agent_result_has_repository_conflict(outcome.result):
            raise RepositoryAlreadyExistsError(REPOSITORY_ALREADY_EXISTS_MESSAGE)
        message = agent_repository_failure_message(
            outcome.result,
            last_error=str(getattr(outcome.task, "last_error", "") or ""),
        )
        result = outcome.result if isinstance(outcome.result, dict) else {}
        raise ProxyFSRepositoryError(
            message or "Proxy filesystem repository initialization failed.",
            error_code=str(result.get("error_code") or "REPOSITORY_CREATE_FAILED"),
        )
    if not (
        isinstance(outcome.result, dict)
        and outcome.result.get("ownership_verified") is True
    ):
        from apps.storage.services.internal.repository_location import (
            repository_has_legacy_location,
        )

        if (
            not supports_ownership
            and health_only
            and repository_has_legacy_location(repository)
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
            raise ProxyFSRepositoryError(
                "Agent declared repository ownership support but did not return an ownership result.",
                error_code="AGENT_PROTOCOL_INVALID",
            )
        raise ProxyFSRepositoryError(
            f'Agent "{node.name}" must be upgraded to verify repository ownership.',
            error_code="AGENT_UPGRADE_REQUIRED",
        )
    from apps.storage.services.internal.repository_location import (
        mark_repository_location_ownership_verified,
    )

    mark_repository_location_ownership_verified(repository)
    logger.info(
        "%s ok repository_id=%s node_id=%s org_id=%s",
        log_scope,
        repository.id,
        node.id,
        repository.organization_id,
    )
    return outcome
