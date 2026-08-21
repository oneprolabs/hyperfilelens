from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any
from uuid import uuid4

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.node.models import Node, NodeTask
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
from apps.node.services.interface import (
    AgentTaskSyncResult,
    run_agent_task_async,
    run_agent_task_sync,
)
from apps.source.constants import ResourceType
from apps.storage.repositories.models import (
    Repository,
    RepositoryLocationClaim,
    RepositoryUsageShard,
)
from apps.storage.services.internal.nas_repository import (
    NASRepositoryError,
    check_proxy_nas_repository,
    nas_agent_repository_subdir,
    nas_proxy_repository_subdir,
    nas_repository_payload,
    validate_proxy_for_repository,
)
from apps.storage.services.internal.proxy_fs_repository import (
    ProxyFSRepositoryError,
    check_proxy_fs_repository,
    proxy_fs_repository_payload,
    validate_proxy_for_proxy_fs,
)
from apps.storage.services.internal.repository_initializer import check_s3_repository
from apps.storage.services.internal.repository_errors import (
    RepositoryHealthTransportUnconfirmed,
    agent_task_transport_unconfirmed,
)
from apps.storage.services.internal.repository_execution_lock import (
    repository_execution_lock,
)
from apps.storage.services.internal.repository_location import (
    invalidate_repository_location_ownership,
    mark_repository_location_ownership_verified,
    repository_has_legacy_location,
)
from apps.storage.services.internal.repository_ownership import (
    RepositoryOwnershipError,
)
from apps.storage.services.internal.repository_usage import (
    RepositoryUsageProbeResult,
    apply_repository_usage_probe,
    repository_observation_revision,
    repository_usage_probe_from_agent_result,
)


logger = logging.getLogger(__name__)

REPOSITORY_HEALTH_PROBE_CORRELATION_TYPE = "storage.repository_health"


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


def project_repository_health_from_agent_result(
    *, node_task: NodeTask, allow_failure: bool = True
) -> bool:
    """Project a current successful ``repo.status`` result back to repository health."""

    persisted = node_task.payload if isinstance(node_task.payload, dict) else {}
    if persisted.get("automatic_health_probe") is True:
        return _project_automatic_repository_health(
            node_task=node_task,
            allow_failure=allow_failure,
        )
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


def dispatch_automatic_repository_observation(
    *,
    repository: Repository,
    retry_attempt: int = 0,
    include_usage: bool = False,
) -> list[NodeTask] | None:
    """Serialize and dispatch one repository observation generation."""

    if repository.repo_type not in {Repository.Type.NAS, Repository.Type.PROXY_FS}:
        return None
    with repository_execution_lock(
        operation="repository-observation-dispatch",
        operation_id=repository.id,
    ) as acquired:
        if not acquired:
            return list(
                NodeTask.objects.filter(
                    organization_id=repository.organization_id,
                    correlation_type=REPOSITORY_HEALTH_PROBE_CORRELATION_TYPE,
                    correlation_id=str(repository.id),
                    status__in=[NodeTask.Status.PENDING, NodeTask.Status.RUNNING],
                    payload__automatic_health_probe=True,
                    payload__include_usage=include_usage,
                ).order_by("created_at", "id")
            )
        current = Repository.objects.filter(
            pk=repository.id,
            organization_id=repository.organization_id,
            status=Repository.Status.CREATED,
        ).first()
        if current is None:
            return []
        return _dispatch_automatic_repository_observation_locked(
            repository=current,
            retry_attempt=retry_attempt,
            include_usage=include_usage,
        )


def _dispatch_automatic_repository_observation_locked(
    *, repository: Repository, retry_attempt: int = 0, include_usage: bool
) -> list[NodeTask]:
    """Dispatch durable repository health/usage observations without waiting.

    An empty list means the Agent-owned scope was handled but currently has no
    routable execution node. Direct NAS locations fan out one observation per
    distinct execution node and aggregate from durable shard rows on result.
    """

    existing = list(
        NodeTask.objects.filter(
            organization_id=repository.organization_id,
            kind="repo.status",
            correlation_type=REPOSITORY_HEALTH_PROBE_CORRELATION_TYPE,
            correlation_id=str(repository.id),
            status__in=[NodeTask.Status.PENDING, NodeTask.Status.RUNNING],
            payload__automatic_health_probe=True,
            payload__include_usage=include_usage,
        ).order_by("created_at", "id")
    )
    if existing:
        return existing

    revision = repository_observation_revision(repository)
    group_id = str(uuid4())
    retry_attempt = max(0, int(retry_attempt))

    if repository.repo_type == Repository.Type.PROXY_FS or (
        repository.repo_type == Repository.Type.NAS
        and repository.bind_node_type == Repository.BindNodeType.PROXY
        and repository.bind_node_id
    ):
        if repository.repo_type == Repository.Type.NAS:
            node = validate_proxy_for_repository(repository)
            repository_subdir = nas_proxy_repository_subdir(repository)
            repository_payload = nas_repository_payload(
                repository=repository,
                subdir=repository_subdir,
                node_id=node.id,
            )
            legacy_location = repository_has_legacy_location(
                repository,
                owner_node_id=node.id,
                repository_subdir=repository_subdir,
            )
        else:
            node = validate_proxy_for_proxy_fs(repository)
            repository_subdir = ""
            repository_payload = proxy_fs_repository_payload(repository)
            legacy_location = repository_has_legacy_location(repository)
        legacy_adoption = RepositoryLocationClaim.objects.filter(
            repository=repository,
            scope=RepositoryLocationClaim.Scope.REPOSITORY,
            state=RepositoryLocationClaim.State.OWNED,
            ownership_verified_at__isnull=True,
            legacy_adoption_required=True,
        ).exists()
        return [
            _dispatch_repository_observation_task(
                repository=repository,
                node=node,
                repository_payload=repository_payload,
                repository_subdir=repository_subdir,
                revision=revision,
                group_id=group_id,
                expected_node_ids=[int(node.id)],
                retry_attempt=retry_attempt,
                allow_ownership_adoption=legacy_adoption,
                legacy_compatibility_allowed=(
                    not node_supports_capability(
                        node,
                        REPOSITORY_OWNERSHIP_CAPABILITY,
                    )
                    and legacy_location
                ),
                direct_nas=False,
                transport_unknown=False,
                include_usage=include_usage,
            )
        ]

    if repository.repo_type != Repository.Type.NAS or (
        repository.bind_node_type or repository.bind_node_id
    ):
        raise ValidationError("NAS repository proxy binding is incomplete.")

    has_associations, has_claimed_locations, nodes = _unbound_nas_execution_nodes(
        repository
    )
    if not has_associations or not has_claimed_locations:
        Repository.objects.filter(pk=repository.id).update(
            health=Repository.Health.UNVERIFIED,
            health_failures=0,
        )
        return []

    online_nodes = [node for node in nodes if node.availability == Node.Availability.ONLINE]
    expected_node_ids = [int(node.id) for node in online_nodes]

    from apps.storage.services.internal.repository_usage import (
        _direct_nas_agent_config_groups,
        _mark_direct_nas_inactive_shards,
        _upsert_direct_nas_agent_shard,
    )

    config_groups = _direct_nas_agent_config_groups(repository)
    checked_at = timezone.now()
    owned_keys = {
        (int(node_id), str(root_path))
        for node_id, root_path in RepositoryLocationClaim.objects.filter(
            repository=repository,
            scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
            state=RepositoryLocationClaim.State.OWNED,
            owner_node_id__isnull=False,
        ).values_list("owner_node_id", "root_path")
    }
    active_keys = {
        (int(node_id), nas_agent_repository_subdir(int(node_id)))
        for node_id in config_groups
        if (int(node_id), nas_agent_repository_subdir(int(node_id))) in owned_keys
    }
    if include_usage:
        _mark_direct_nas_inactive_shards(
            repository=repository,
            active_keys=active_keys,
            checked_at=checked_at,
        )
    online_node_ids = {int(node.id) for node in online_nodes}
    for node_id, source_config_ids in config_groups.items():
        repository_subdir = nas_agent_repository_subdir(node_id)
        if not include_usage:
            continue
        if (node_id, repository_subdir) not in owned_keys:
            _upsert_direct_nas_agent_shard(
                repository=repository,
                node_id=node_id,
                repository_subdir=repository_subdir,
                source_config_ids=source_config_ids,
                checked_at=checked_at,
                status=RepositoryUsageShard.Status.SKIPPED,
                last_error="Repository location ownership requires verification.",
                is_active=False,
            )
        elif node_id not in online_node_ids:
            _upsert_direct_nas_agent_shard(
                repository=repository,
                node_id=node_id,
                repository_subdir=repository_subdir,
                source_config_ids=source_config_ids,
                checked_at=checked_at,
                status=RepositoryUsageShard.Status.SKIPPED,
                last_error="Repository execution node is not online.",
            )
    if not expected_node_ids:
        return []
    tasks: list[NodeTask] = []
    for node in online_nodes:
        repository_subdir = nas_agent_repository_subdir(node.id)
        claim = RepositoryLocationClaim.objects.filter(
            repository=repository,
            scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
            owner_node_id=node.id,
            root_path=repository_subdir,
        ).first()
        if claim is None:
            continue
        legacy_adoption = (
            claim.state == RepositoryLocationClaim.State.OWNED
            and claim.ownership_verified_at is None
            and claim.legacy_adoption_required
        )
        tasks.append(
            _dispatch_repository_observation_task(
                repository=repository,
                node=node,
                repository_payload=nas_repository_payload(
                    repository=repository,
                    subdir=repository_subdir,
                    node_id=node.id,
                ),
                repository_subdir=repository_subdir,
                revision=revision,
                group_id=group_id,
                expected_node_ids=expected_node_ids,
                retry_attempt=retry_attempt,
                allow_ownership_adoption=legacy_adoption,
                legacy_compatibility_allowed=(
                    not node_supports_capability(
                        node,
                        REPOSITORY_OWNERSHIP_CAPABILITY,
                    )
                    and repository_has_legacy_location(
                        repository,
                        owner_node_id=node.id,
                        repository_subdir=repository_subdir,
                    )
                ),
                direct_nas=True,
                transport_unknown=len(online_nodes) < len(nodes),
                include_usage=include_usage,
                usage_active=(
                    include_usage
                    and claim.state == RepositoryLocationClaim.State.OWNED
                ),
            )
        )
    return tasks


def dispatch_automatic_repository_health_probe(
    *, repository: Repository, retry_attempt: int = 0
) -> NodeTask | None:
    """Compatibility wrapper returning the first durable observation task."""

    tasks = dispatch_automatic_repository_observation(
        repository=repository,
        retry_attempt=retry_attempt,
        include_usage=False,
    )
    return tasks[0] if tasks else None


def _dispatch_repository_observation_task(
    *,
    repository: Repository,
    node: Node,
    repository_payload: dict[str, Any],
    repository_subdir: str,
    revision: str,
    group_id: str,
    expected_node_ids: list[int],
    retry_attempt: int,
    allow_ownership_adoption: bool,
    legacy_compatibility_allowed: bool,
    direct_nas: bool,
    transport_unknown: bool,
    include_usage: bool,
    usage_active: bool = True,
) -> NodeTask:
    handle = run_agent_task_async(
        organization_id=repository.organization_id,
        node_id=node.id,
        kind="repo.status",
        payload={
            "repository": repository_payload,
            "health_only": not include_usage,
            "allow_ownership_adoption": allow_ownership_adoption,
        },
        persisted_payload={
            "automatic_health_probe": True,
            "repository_id": int(repository.id),
            "repository_revision": revision,
            "retry_attempt": retry_attempt,
            "repository_subdir": repository_subdir,
            "legacy_compatibility_allowed": legacy_compatibility_allowed,
            "direct_nas": direct_nas,
            "transport_unknown": transport_unknown,
            "include_usage": include_usage,
            "failure_affects_health": not include_usage,
            "usage_active": usage_active,
            "observation_group_id": group_id,
            "expected_node_ids": expected_node_ids,
        },
        correlation_type=REPOSITORY_HEALTH_PROBE_CORRELATION_TYPE,
        correlation_id=str(repository.id),
    )
    handle.task.refresh_from_db()
    return handle.task


def _project_automatic_repository_health(
    *, node_task: NodeTask, allow_failure: bool
) -> bool:
    if node_task.kind != "repo.status" or node_task.status not in {
        NodeTask.Status.SUCCESS,
        NodeTask.Status.FAILED,
        NodeTask.Status.TIMEOUT,
        NodeTask.Status.CANCELED,
    }:
        return False
    persisted = node_task.payload if isinstance(node_task.payload, dict) else {}
    repository_id = int(persisted.get("repository_id") or 0)
    repository = Repository.objects.filter(
        pk=repository_id,
        organization_id=node_task.organization_id,
        status=Repository.Status.CREATED,
    ).first()
    if repository is None:
        return False
    expected_revision = str(persisted.get("repository_revision") or "")
    if expected_revision:
        if repository_observation_revision(repository) != expected_revision:
            return False
    else:
        expected_updated_at = parse_datetime(
            str(persisted.get("repository_updated_at") or "")
        )
        if expected_updated_at is None or repository.updated_at != expected_updated_at:
            return False
    if persisted.get("direct_nas") is True:
        _has_associations, _has_claimed_locations, current_nodes = (
            _unbound_nas_execution_nodes(repository)
        )
        if node_task.node_id not in {node.id for node in current_nodes}:
            return False

    outcome = AgentTaskSyncResult(
        task=node_task,
        stream_message=None,
        timed_out=node_task.status == NodeTask.Status.TIMEOUT,
    )
    if node_task.status == NodeTask.Status.SUCCESS:
        ownership_verified = (
            isinstance(node_task.result, dict)
            and node_task.result.get("ownership_verified") is True
        )
        legacy_allowed = persisted.get("legacy_compatibility_allowed") is True
        if not ownership_verified and not legacy_allowed:
            if not allow_failure:
                return False
            return _project_repository_observation_failure(
                repository=repository,
                node_task=node_task,
                persisted=persisted,
                fail_immediately=True,
            )
        if ownership_verified:
            if persisted.get("direct_nas") is True:
                mark_repository_location_ownership_verified(
                    repository,
                    owner_node_id=node_task.node_id,
                    repository_subdir=str(persisted.get("repository_subdir") or ""),
                )
            elif repository.repo_type == Repository.Type.NAS:
                mark_repository_location_ownership_verified(
                    repository,
                    owner_node_id=node_task.node_id,
                    repository_subdir=str(
                        persisted.get("repository_subdir") or ""
                    ),
                )
            else:
                mark_repository_location_ownership_verified(repository)
        return _project_repository_observation_success(
            repository=repository,
            node_task=node_task,
            persisted=persisted,
        )

    if (
        not allow_failure
        or node_task.accepted_at is None
        or agent_task_transport_unconfirmed(outcome)
    ):
        return False
    result = node_task.result if isinstance(node_task.result, dict) else {}
    ownership_invalid = (
        str(result.get("error_code") or "").strip()
        == "REPOSITORY_OWNERSHIP_INVALID"
    )
    if ownership_invalid:
        if persisted.get("direct_nas") is True:
            invalidate_repository_location_ownership(
                repository,
                owner_node_id=node_task.node_id,
                repository_subdir=str(persisted.get("repository_subdir") or ""),
            )
        else:
            invalidate_repository_location_ownership(repository)
    return _project_repository_observation_failure(
        repository=repository,
        node_task=node_task,
        persisted=persisted,
        fail_immediately=ownership_invalid,
    )


def _project_repository_observation_success(
    *, repository: Repository, node_task: NodeTask, persisted: dict[str, Any]
) -> bool:
    if persisted.get("include_usage") is not True:
        return bool(
            Repository.objects.filter(pk=repository.id).update(
                health=Repository.Health.ONLINE,
                health_failures=0,
            )
        )
    result = node_task.result if isinstance(node_task.result, dict) else {}
    probe = repository_usage_probe_from_agent_result(
        result,
        repository_subdir=str(persisted.get("repository_subdir") or ""),
    )
    checked_at = node_task.updated_at or timezone.now()
    if persisted.get("direct_nas") is True:
        from apps.storage.services.internal.repository_usage import (
            _direct_nas_agent_config_groups,
            _upsert_direct_nas_agent_shard,
        )

        current_source_config_ids = _direct_nas_agent_config_groups(repository).get(
            int(node_task.node_id),
            [],
        )

        already_applied = RepositoryUsageShard.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
            usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
            node_id=node_task.node_id,
            repository_subdir=str(persisted.get("repository_subdir") or ""),
            last_checked_at__gte=checked_at,
        ).exists()
        if persisted.get("usage_active") is True and not already_applied:
            _upsert_direct_nas_agent_shard(
                repository=repository,
                node_id=node_task.node_id,
                repository_subdir=str(persisted.get("repository_subdir") or ""),
                source_config_ids=current_source_config_ids,
                checked_at=checked_at,
                probe=probe,
            )
        if not already_applied:
            _aggregate_direct_nas_observation(
                repository=repository,
                checked_at=checked_at,
            )
        else:
            Repository.objects.filter(pk=repository.id).update(
                health=Repository.Health.ONLINE,
                health_failures=0,
            )
    else:
        with transaction.atomic():
            locked = Repository.objects.select_for_update().get(pk=repository.id)
            expected_revision = str(persisted.get("repository_revision") or "")
            if (
                expected_revision
                and repository_observation_revision(locked) != expected_revision
            ):
                return False
            already_applied = bool(
                locked.last_checked_at and locked.last_checked_at >= checked_at
            )
            if not already_applied:
                apply_repository_usage_probe(
                    locked,
                    probe,
                    checked_at=checked_at,
                    recorded_at=checked_at,
                )
            Repository.objects.filter(pk=locked.id).update(
                health=Repository.Health.ONLINE,
                health_failures=0,
            )
    return True


def _project_repository_observation_failure(
    *,
    repository: Repository,
    node_task: NodeTask,
    persisted: dict[str, Any],
    fail_immediately: bool,
) -> bool:
    include_usage = persisted.get("include_usage") is True
    failure_affects_health = persisted.get("failure_affects_health", True) is True
    if persisted.get("direct_nas") is not True:
        if include_usage:
            apply_repository_usage_probe(
                repository,
                RepositoryUsageProbeResult(
                    None,
                    None,
                    error=str(node_task.last_error or "Repository observation failed.")[:1000],
                ),
                checked_at=node_task.updated_at or timezone.now(),
                recorded_at=node_task.updated_at or timezone.now(),
            )
        if not failure_affects_health and not fail_immediately:
            return True
        return _record_automatic_repository_health_failure(
            repository=repository,
            retry_attempt=int(persisted.get("retry_attempt") or 0),
            fail_immediately=fail_immediately,
        )

    from apps.storage.services.internal.repository_usage import (
        _direct_nas_agent_config_groups,
        _upsert_direct_nas_agent_shard,
    )

    if include_usage and persisted.get("usage_active") is True:
        result = node_task.result if isinstance(node_task.result, dict) else {}
        error = str(
            node_task.last_error
            or result.get("error")
            or result.get("stderr")
            or "Repository observation failed."
        )[:1000]
        _upsert_direct_nas_agent_shard(
            repository=repository,
            node_id=node_task.node_id,
            repository_subdir=str(persisted.get("repository_subdir") or ""),
            source_config_ids=_direct_nas_agent_config_groups(repository).get(
                int(node_task.node_id),
                [],
            ),
            checked_at=node_task.updated_at or timezone.now(),
            status=RepositoryUsageShard.Status.FAILED,
            last_error=error,
            is_active=RepositoryLocationClaim.objects.filter(
                repository=repository,
                scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
                owner_node_id=node_task.node_id,
                root_path=str(persisted.get("repository_subdir") or ""),
                state=RepositoryLocationClaim.State.OWNED,
            ).exists(),
        )
    if not failure_affects_health and not fail_immediately:
        return True
    return _finalize_direct_nas_health_group(
        repository=repository,
        node_task=node_task,
        persisted=persisted,
        fail_immediately=fail_immediately,
    )


def _aggregate_direct_nas_observation(*, repository: Repository, checked_at) -> None:
    shards = list(
        RepositoryUsageShard.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
            usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
            is_active=True,
            last_success_checked_at__isnull=False,
        )
    )
    if shards:
        estimated = sum(max(0, int(shard.estimated_usage_bytes or 0)) for shard in shards)
        capacities = [
            int(shard.capacity_bytes or 0)
            for shard in shards
            if int(shard.capacity_bytes or 0) > 0
        ]
        repository.refresh_from_db()
        apply_repository_usage_probe(
            repository,
            RepositoryUsageProbeResult(
                estimated,
                max(capacities) if capacities else None,
                usage_error="" if shards else "Unable to read repository usage.",
                capacity_error=(
                    "" if capacities else "Unable to read filesystem capacity."
                ),
            ),
            checked_at=checked_at,
            recorded_at=checked_at,
        )
    Repository.objects.filter(pk=repository.id).update(
        health=Repository.Health.ONLINE,
        health_failures=0,
    )


def _finalize_direct_nas_health_group(
    *,
    repository: Repository,
    node_task: NodeTask,
    persisted: dict[str, Any],
    fail_immediately: bool,
) -> bool:
    group_id = str(persisted.get("observation_group_id") or "")
    expected_node_ids = {
        int(value) for value in persisted.get("expected_node_ids") or []
    }
    with transaction.atomic():
        leader = (
            NodeTask.objects.select_for_update()
            .filter(
                organization_id=repository.organization_id,
                correlation_type=REPOSITORY_HEALTH_PROBE_CORRELATION_TYPE,
                correlation_id=str(repository.id),
                payload__observation_group_id=group_id,
            )
            .order_by("id")
            .first()
        )
        if leader is None:
            return False
        leader_payload = leader.payload if isinstance(leader.payload, dict) else {}
        if leader_payload.get("health_group_projected") is True:
            return True
        tasks = list(
            NodeTask.objects.filter(
                organization_id=repository.organization_id,
                correlation_type=REPOSITORY_HEALTH_PROBE_CORRELATION_TYPE,
                correlation_id=str(repository.id),
                payload__observation_group_id=group_id,
            )
        )
        if {int(task.node_id) for task in tasks} != expected_node_ids:
            return True
        terminal = {
            NodeTask.Status.SUCCESS,
            NodeTask.Status.FAILED,
            NodeTask.Status.TIMEOUT,
            NodeTask.Status.CANCELED,
        }
        if any(task.status not in terminal for task in tasks):
            return True
        if any(_observation_task_proves_online(task) for task in tasks):
            Repository.objects.filter(pk=repository.id).update(
                health=Repository.Health.ONLINE,
                health_failures=0,
            )
            _mark_health_group_projected(leader, leader_payload)
            return True
        if persisted.get("transport_unknown") is True or any(
            task.status != NodeTask.Status.SUCCESS
            and (
                task.accepted_at is None
                or agent_task_transport_unconfirmed(
                    AgentTaskSyncResult(
                        task=task,
                        stream_message=None,
                        timed_out=task.status == NodeTask.Status.TIMEOUT,
                    )
                )
            )
            for task in tasks
        ):
            _mark_health_group_projected(leader, leader_payload)
            return True
        projected = _record_automatic_repository_health_failure(
            repository=repository,
            retry_attempt=int(persisted.get("retry_attempt") or 0),
            fail_immediately=(
                fail_immediately
                or any(
                    str((task.result or {}).get("error_code") or "").strip()
                    == "REPOSITORY_OWNERSHIP_INVALID"
                    for task in tasks
                    if isinstance(task.result, dict)
                )
            ),
        )
        if projected:
            _mark_health_group_projected(leader, leader_payload)
        return projected


def _mark_health_group_projected(
    leader: NodeTask, payload: dict[str, Any]
) -> None:
    updated = dict(payload)
    updated["health_group_projected"] = True
    leader.payload = updated
    leader.save(update_fields=["payload"])


def _observation_task_proves_online(node_task: NodeTask) -> bool:
    if node_task.status != NodeTask.Status.SUCCESS:
        return False
    result = node_task.result if isinstance(node_task.result, dict) else {}
    persisted = node_task.payload if isinstance(node_task.payload, dict) else {}
    return bool(
        result.get("ownership_verified") is True
        or persisted.get("legacy_compatibility_allowed") is True
    )


def _record_automatic_repository_health_failure(
    *, repository: Repository, retry_attempt: int, fail_immediately: bool
) -> bool:
    from apps.storage.tasks import _record_repository_health_failure

    result = _record_repository_health_failure(
        repository=repository,
        retry_attempt=retry_attempt,
        fail_immediately=fail_immediately,
    )
    return not bool(result.get("stale"))


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
