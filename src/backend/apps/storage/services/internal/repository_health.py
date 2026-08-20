from __future__ import annotations

import logging
from enum import StrEnum

from django.apps import apps
from django.core.exceptions import ValidationError

from apps.node.models import Node
from apps.node.services.capabilities import (
    REPOSITORY_OWNERSHIP_CAPABILITY,
    node_supports_capability,
)
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_log import (
    log_agent_dispatch,
    log_agent_exception,
    log_agent_outcome,
)
from apps.node.services.interface import run_agent_task_sync
from apps.source.constants import ResourceType
from apps.storage.repositories.models import Repository, RepositoryLocationClaim
from apps.storage.services.internal.nas_repository import (
    NASRepositoryError,
    check_proxy_nas_repository,
    nas_agent_repository_subdir,
    nas_repository_payload,
)
from apps.storage.services.internal.proxy_fs_repository import (
    ProxyFSRepositoryError,
    check_proxy_fs_repository,
)
from apps.storage.services.internal.repository_initializer import check_s3_repository
from apps.storage.services.internal.repository_errors import (
    RepositoryHealthTransportUnconfirmed,
    agent_task_transport_unconfirmed,
)
from apps.storage.services.internal.repository_location import (
    invalidate_repository_location_ownership,
    mark_repository_location_ownership_verified,
    repository_has_legacy_location,
)
from apps.storage.services.internal.repository_ownership import (
    RepositoryOwnershipError,
)


logger = logging.getLogger(__name__)


class _AgentProbeState(StrEnum):
    ONLINE = "online"
    CONFIRMED_FAILURE = "confirmed_failure"
    TRANSPORT_UNKNOWN = "transport_unknown"


def is_repository_ownership_failure(exc: Exception) -> bool:
    """Return whether an exception chain proves physical ownership invalid."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RepositoryOwnershipError):
            return True
        if isinstance(current, (NASRepositoryError, ProxyFSRepositoryError)) and (
            getattr(current, "error_code", "") == "REPOSITORY_OWNERSHIP_INVALID"
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def project_repository_health_from_agent_result(*, node_task) -> bool:
    """Project a current successful ``repo.status`` result back to repository health."""

    if (
        str(getattr(node_task, "kind", "") or "") != "repo.status"
        or str(getattr(node_task, "correlation_type", "") or "")
        != "storage_repository"
        or str(getattr(node_task, "status", "") or "") != "success"
    ):
        return False
    correlation_id = str(getattr(node_task, "correlation_id", "") or "").strip()
    if not correlation_id.isdigit():
        return False
    repository = Repository.objects.filter(
        pk=int(correlation_id),
        organization_id=node_task.organization_id,
        status=Repository.Status.CREATED,
        updated_at__lte=node_task.created_at,
    ).first()
    if repository is None:
        return False

    current_scope = Repository.objects.filter(
        pk=repository.id,
        organization_id=node_task.organization_id,
        status=Repository.Status.CREATED,
        updated_at=repository.updated_at,
    )
    if repository.repo_type == Repository.Type.PROXY_FS or (
        repository.repo_type == Repository.Type.NAS
        and repository.bind_node_type == Repository.BindNodeType.PROXY
    ):
        if (
            repository.bind_node_type != Repository.BindNodeType.PROXY
            or repository.bind_node_id != node_task.node_id
        ):
            return False
        current_scope = current_scope.filter(
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=node_task.node_id,
        )
    elif (
        repository.repo_type == Repository.Type.NAS
        and not repository.bind_node_type
        and not repository.bind_node_id
    ):
        _has_associations, _has_claims, nodes = _unbound_nas_execution_nodes(
            repository
        )
        if node_task.node_id not in {node.id for node in nodes}:
            return False
        current_scope = current_scope.filter(
            bind_node_type__isnull=True,
            bind_node_id__isnull=True,
        )
    else:
        return False
    return bool(
        current_scope.update(
            health=Repository.Health.ONLINE,
            health_failures=0,
        )
    )


def probe_repository_health(repository: Repository) -> str:
    """Probe a repository without persisting health, timestamps, or usage."""
    if repository.repo_type == Repository.Type.S3:
        check_s3_repository(repository)
        return Repository.Health.ONLINE

    if repository.repo_type == Repository.Type.PROXY_FS:
        check_proxy_fs_repository(repository, health_only=True)
        return Repository.Health.ONLINE

    if repository.repo_type == Repository.Type.NAS:
        if (
            repository.bind_node_type == Repository.BindNodeType.PROXY
            and repository.bind_node_id
        ):
            check_proxy_nas_repository(repository, health_only=True)
            return Repository.Health.ONLINE
        if not repository.bind_node_type and not repository.bind_node_id:
            return probe_unbound_nas_repository_health(repository)
        raise ValidationError("NAS repository proxy binding is incomplete.")

    raise ValidationError(f"Repository type {repository.repo_type} is not supported.")


def probe_unbound_nas_repository_health(
    repository: Repository,
    *,
    adopt_legacy_ownership: bool = True,
) -> str:
    """Return Online when any associated execution node can access the NAS."""
    if (
        repository.repo_type != Repository.Type.NAS
        or repository.bind_node_type
        or repository.bind_node_id
    ):
        raise ValidationError("Repository is not an unbound NAS repository.")

    has_associations, has_claimed_locations, nodes = _unbound_nas_execution_nodes(
        repository
    )
    if not has_associations or not has_claimed_locations:
        return Repository.Health.UNVERIFIED

    transport_unknown = not nodes
    for node in nodes:
        if node.availability != Node.Availability.ONLINE:
            transport_unknown = True
            continue
        probe_state = _probe_unbound_nas_from_node(
            repository=repository,
            node=node,
            adopt_legacy_ownership=adopt_legacy_ownership,
        )
        if probe_state == _AgentProbeState.ONLINE:
            return Repository.Health.ONLINE
        if probe_state == _AgentProbeState.TRANSPORT_UNKNOWN:
            transport_unknown = True
    if transport_unknown:
        raise RepositoryHealthTransportUnconfirmed(
            "No associated execution node returned an authoritative repository result."
        )
    return Repository.Health.OFFLINE


def _unbound_nas_execution_nodes(
    repository: Repository,
) -> tuple[bool, bool, list[Node]]:
    backup_config_model = apps.get_model("protection", "BackupConfig")
    rows = list(
        backup_config_model.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
        )
        .order_by("id")
        .values_list("source_type", "source_ref_id")
    )
    if not rows:
        return False, False, []

    agent_ids = {
        int(source_ref_id)
        for source_type, source_ref_id in rows
        if source_type == "agent" and int(source_ref_id or 0) > 0
    }
    nas_source_ids = {
        int(source_ref_id)
        for source_type, source_ref_id in rows
        if source_type == "nas" and int(source_ref_id or 0) > 0
    }

    nodes: dict[int, Node] = {
        int(node.id): node
        for node in Node.objects.filter(
            organization_id=repository.organization_id,
            id__in=agent_ids,
            role=NodeRole.AGENT,
            is_deleted=False,
        )
    }
    if nas_source_ids:
        source_resource_model = apps.get_model("source", "SourceResource")
        proxy_ids = set(
            source_resource_model.objects.filter(
                organization_id=repository.organization_id,
                id__in=nas_source_ids,
                resource_type=ResourceType.NAS,
                is_deleted=False,
                bound_node_id__isnull=False,
            ).values_list("bound_node_id", flat=True)
        )
        nodes.update(
            {
                int(node.id): node
                for node in Node.objects.filter(
                    organization_id=repository.organization_id,
                    id__in=proxy_ids,
                    role=NodeRole.PROXY,
                    is_deleted=False,
                )
            }
        )
    execution_node_ids = agent_ids | proxy_ids if nas_source_ids else agent_ids
    claimed_node_ids = {
        int(node_id)
        for node_id, root_path in RepositoryLocationClaim.objects.filter(
            repository=repository,
            scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
            state__in=[
                RepositoryLocationClaim.State.INITIALIZING,
                RepositoryLocationClaim.State.OWNED,
                RepositoryLocationClaim.State.RESIDUAL,
            ],
            owner_node_id__in=execution_node_ids,
        ).values_list("owner_node_id", "root_path")
        if str(root_path) == nas_agent_repository_subdir(int(node_id))
    }
    return (
        True,
        bool(claimed_node_ids),
        [nodes[node_id] for node_id in sorted(nodes) if node_id in claimed_node_ids],
    )


def _probe_unbound_nas_from_node(
    *,
    repository: Repository,
    node: Node,
    adopt_legacy_ownership: bool = True,
) -> _AgentProbeState:
    log_scope = "storage direct nas health probe"
    payload = {
        "repository": nas_repository_payload(
            repository=repository,
            subdir=nas_agent_repository_subdir(node.id),
            node_id=node.id,
        ),
        "health_only": True,
        "allow_ownership_adoption": (
            adopt_legacy_ownership
            and RepositoryLocationClaim.objects.filter(
                repository=repository,
                scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
                owner_node_id=node.id,
                root_path=nas_agent_repository_subdir(node.id),
                state=RepositoryLocationClaim.State.OWNED,
                ownership_verified_at__isnull=True,
                legacy_adoption_required=True,
            ).exists()
        ),
    }
    log_agent_dispatch(
        log_scope,
        node_id=node.id,
        kind="repo.status",
        correlation_type="storage_repository",
        correlation_id=str(repository.id),
        repository_id=repository.id,
        org_id=repository.organization_id,
    )
    try:
        outcome = run_agent_task_sync(
            organization_id=repository.organization_id,
            node_id=node.id,
            kind="repo.status",
            payload=payload,
            correlation_type="storage_repository",
            correlation_id=str(repository.id),
            wait_timeout_seconds=180,
        )
    except Exception as exc:
        log_agent_exception(
            log_scope,
            node_id=node.id,
            kind="repo.status",
            exc=exc,
            correlation_type="storage_repository",
            correlation_id=str(repository.id),
            repository_id=repository.id,
        )
        logger.warning(
            "direct NAS health path failed repository_id=%s node_id=%s error_type=%s",
            repository.id,
            node.id,
            type(exc).__name__,
        )
        return _AgentProbeState.TRANSPORT_UNKNOWN

    log_agent_outcome(
        log_scope,
        outcome=outcome,
        node_id=node.id,
        kind="repo.status",
        correlation_type="storage_repository",
        correlation_id=str(repository.id),
        repository_id=repository.id,
    )
    if agent_task_transport_unconfirmed(outcome):
        return _AgentProbeState.TRANSPORT_UNKNOWN
    if outcome.task.status != "success":
        result = outcome.result if isinstance(outcome.result, dict) else {}
        if result.get("error_code") == "REPOSITORY_OWNERSHIP_INVALID":
            invalidate_repository_location_ownership(
                repository,
                owner_node_id=node.id,
                repository_subdir=nas_agent_repository_subdir(node.id),
            )
        return _AgentProbeState.CONFIRMED_FAILURE
    if not (
        isinstance(outcome.result, dict)
        and outcome.result.get("ownership_verified") is True
    ):
        if node_supports_capability(node, REPOSITORY_OWNERSHIP_CAPABILITY):
            logger.warning(
                "repository ownership capability returned no result repository_id=%s node_id=%s",
                repository.id,
                node.id,
            )
            return _AgentProbeState.CONFIRMED_FAILURE
        if repository_has_legacy_location(
            repository,
            owner_node_id=node.id,
            repository_subdir=nas_agent_repository_subdir(node.id),
        ):
            return _AgentProbeState.ONLINE
        return _AgentProbeState.CONFIRMED_FAILURE
    mark_repository_location_ownership_verified(
        repository,
        owner_node_id=node.id,
        repository_subdir=nas_agent_repository_subdir(node.id),
    )
    return _AgentProbeState.ONLINE
