from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.repository_server import (
    normalize_repository_server_host,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_secrets import build_repository_runtime_payload


EXPLICIT_REPOSITORY_SERVER_HOST_KEYS = (
    "proxy_repository_server_host",
    "repository_server_host",
    "advertised_host",
    "advertise_host",
)

@dataclass(frozen=True)
class RepositoryExecutionTarget:
    node: Node
    source_type: str = "agent"
    source_ref_id: int = 0


@dataclass(frozen=True)
class RepositoryAccess:
    node: Node
    repository_payload: dict[str, Any]
    mode: str


def resolve_repository_reader(
    *,
    repository: Repository,
    fallback_node: Node | None,
    source_type: str = "agent",
    source_ref_id: int | None = None,
    repository_endpoint_type: object = "external",
    repository_endpoint: object = None,
) -> RepositoryAccess:
    """Resolve the node that is allowed to read/write the repository.

    Proxy-bound NAS and proxy_fs repositories are central storage endpoints:
    their bound Proxy is the only node that may receive the full repository
    payload. Other repository types keep the existing local execution model.
    """

    if repository.repo_type == Repository.Type.NAS and repository.bind_node_type == Repository.BindNodeType.PROXY:
        node = _bound_proxy(repository=repository, message_prefix="NAS repository")
        return _access(
            repository=repository,
            node=node,
            source_type="proxy",
            source_ref_id=node.id,
            mode="bound_proxy",
            repository_endpoint_type=repository_endpoint_type,
            repository_endpoint=repository_endpoint,
        )

    if repository.repo_type == Repository.Type.PROXY_FS:
        node = _bound_proxy(repository=repository, message_prefix="Proxy filesystem repository")
        return _access(
            repository=repository,
            node=node,
            source_type="proxy",
            source_ref_id=node.id,
            mode="bound_proxy",
            repository_endpoint_type=repository_endpoint_type,
            repository_endpoint=repository_endpoint,
        )

    if fallback_node is None:
        raise ValidationError({"repository_id": "Repository access requires an execution node."})
    return _access(
        repository=repository,
        node=fallback_node,
        source_type=source_type,
        source_ref_id=source_ref_id if source_ref_id is not None else int(fallback_node.id),
        mode="fallback_node",
        repository_endpoint_type=repository_endpoint_type,
        repository_endpoint=repository_endpoint,
    )


def repository_payload_for_node(
    *,
    repository: Repository,
    node: Node,
    source_type: str = "agent",
    source_ref_id: int | None = None,
    repository_endpoint_type: object = "external",
    repository_endpoint: object = None,
) -> dict[str, Any]:
    """Build a repository payload for a specific execution node.

    This is intentionally strict for Proxy-bound repositories. Callers that
    want the correct reader should use :func:`resolve_repository_reader`.
    """

    target = RepositoryExecutionTarget(
        node=node,
        source_type=source_type,
        source_ref_id=source_ref_id if source_ref_id is not None else int(node.id),
    )
    return build_repository_runtime_payload(
        repository=repository,
        execution_target=target,
        repository_endpoint_type=repository_endpoint_type,
        repository_endpoint=repository_endpoint,
    )


def repository_uses_bound_proxy(repository: Repository) -> bool:
    return (
        repository.repo_type == Repository.Type.PROXY_FS
        or (
            repository.repo_type == Repository.Type.NAS
            and repository.bind_node_type == Repository.BindNodeType.PROXY
        )
    )


def explicit_repository_server_host(*, repository: Repository, node: Node) -> tuple[str, str]:
    """Resolve the source-reachable cross-node Repository Server host."""

    config = repository.config if isinstance(repository.config, dict) else {}
    for key in EXPLICIT_REPOSITORY_SERVER_HOST_KEYS:
        try:
            value = normalize_repository_server_host(config.get(key))
        except ValueError:
            return "", f"repository.config.{key}"
        if value:
            return value, f"repository.config.{key}"

    try:
        proxy_override = normalize_repository_server_host(
            getattr(node, "repository_server_address", "")
        )
    except ValueError:
        proxy_override = ""
    if proxy_override:
        return proxy_override, "node.repository_server_address"

    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory") if isinstance(metadata.get("inventory"), dict) else {}
    for source_name, source in (("metadata", metadata), ("metadata.inventory", inventory)):
        for key in EXPLICIT_REPOSITORY_SERVER_HOST_KEYS:
            try:
                value = normalize_repository_server_host(source.get(key))
            except ValueError:
                return "", f"node.{source_name}.{key}"
            if value:
                return value, f"node.{source_name}.{key}"

    for source_name, source in (("metadata", metadata), ("metadata.inventory", inventory)):
        for key in ("primary_ip_address", "primary_ip", "lan_ip_address", "lan_ip", "ip_address"):
            try:
                value = normalize_repository_server_host(source.get(key))
            except ValueError:
                value = ""
            if value:
                return value, f"node.{source_name}.{key}"
        for key in ("ip_addresses", "ipv4_addresses", "addresses"):
            values = source.get(key)
            if not isinstance(values, list):
                continue
            for raw in values:
                try:
                    value = normalize_repository_server_host(raw)
                except ValueError:
                    value = ""
                if value:
                    return value, f"node.{source_name}.{key}"

    try:
        node_ip = normalize_repository_server_host(getattr(node, "ip_address", ""))
    except ValueError:
        node_ip = ""
    if node_ip:
        return node_ip, "node.ip_address"
    return "", ""


def _access(
    *,
    repository: Repository,
    node: Node,
    source_type: str,
    source_ref_id: int,
    mode: str,
    repository_endpoint_type: object = "external",
    repository_endpoint: object = None,
) -> RepositoryAccess:
    return RepositoryAccess(
        node=node,
        repository_payload=repository_payload_for_node(
            repository=repository,
            node=node,
            source_type=source_type,
            source_ref_id=source_ref_id,
            repository_endpoint_type=repository_endpoint_type,
            repository_endpoint=repository_endpoint,
        ),
        mode=mode,
    )


def _bound_proxy(*, repository: Repository, message_prefix: str) -> Node:
    if repository.bind_node_type != Repository.BindNodeType.PROXY or not repository.bind_node_id:
        raise ValidationError({"repository_id": f"{message_prefix} is not bound to a proxy node."})
    node = Node.objects.filter(
        id=repository.bind_node_id,
        organization_id=repository.organization_id,
        role=NodeRole.PROXY,
        is_deleted=False,
    ).first()
    if node is None:
        raise ValidationError({"repository_id": f"{message_prefix} bound proxy node not found."})
    if node.availability != Node.Availability.ONLINE:
        raise ValidationError({"repository_id": f'{message_prefix} bound proxy node "{node.name}" is offline.'})
    return node


__all__ = [
    "RepositoryAccess",
    "RepositoryExecutionTarget",
    "normalize_repository_server_host",
    "repository_payload_for_node",
    "repository_uses_bound_proxy",
    "explicit_repository_server_host",
    "resolve_repository_reader",
]
