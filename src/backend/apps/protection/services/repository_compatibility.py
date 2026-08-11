from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.protection import conf as protection_conf
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_access import explicit_repository_server_host


def validate_backup_repository_compatible(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    repository_id: int,
) -> Repository:
    repository = (
        Repository.objects.filter(
            organization_id=organization_id,
            id=repository_id,
            status=Repository.Status.CREATED,
        )
        .first()
    )
    if repository is None:
        raise ValidationError({"repository_id": "Repository not found."})

    if source_type == "agent" and _is_direct_nas_repository(repository):
        _validate_direct_nas_agent_platform(
            organization_id=organization_id,
            source_ref_id=source_ref_id,
        )
    elif source_type == "nas" and repository.repo_type == Repository.Type.PROXY_FS:
        _validate_proxy_bound_repository(
            organization_id=organization_id,
            source_ref_id=source_ref_id,
            repository=repository,
        )
    elif (
        source_type == "nas"
        and
        repository.repo_type == Repository.Type.NAS
        and repository.bind_node_type == Repository.BindNodeType.PROXY
    ):
        _validate_proxy_bound_repository(
            organization_id=organization_id,
            source_ref_id=source_ref_id,
            repository=repository,
        )

    return repository


def _is_direct_nas_repository(repository: Repository) -> bool:
    return (
        repository.repo_type == Repository.Type.NAS
        and not (
            repository.bind_node_type == Repository.BindNodeType.PROXY
            and repository.bind_node_id
        )
    )


def _validate_direct_nas_agent_platform(
    *,
    organization_id: int,
    source_ref_id: int,
) -> None:
    node = Node.objects.filter(
        organization_id=organization_id,
        id=source_ref_id,
        role=NodeRole.AGENT,
        is_deleted=False,
    ).first()
    if node is None:
        raise ValidationError({"source_ref_id": "Backup source not found."})
    if _direct_nas_agent_platform(node) == "linux":
        return
    raise ValidationError({
        "repository_id": (
            "Direct NAS repositories are compatible only with Linux Host sources. "
            "Bind the NAS repository to a Proxy node for Windows, macOS, or "
            "unidentified Host platforms."
        )
    })


def _direct_nas_agent_platform(node: Node) -> str | None:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    merged = {
        **metadata,
        **(inventory if isinstance(inventory, dict) else {}),
    }
    raw = str(
        merged.get("os") or merged.get("platform") or node.os_name or ""
    ).strip().lower()
    if "darwin" in raw or "mac" in raw:
        return "macos"
    if "windows" in raw or raw in {"win32", "win64"} or raw.startswith("win "):
        return "windows"
    if "linux" in raw:
        return "linux"
    return None


def _validate_proxy_bound_repository(
    *,
    organization_id: int,
    source_ref_id: int,
    repository: Repository,
) -> None:
    if not repository.bind_node_id:
        raise ValidationError({"repository_id": "Repository is not bound to a proxy node."})

    source_proxy_id = _nas_source_proxy_id(
        organization_id=organization_id,
        source_ref_id=source_ref_id,
    )
    if int(repository.bind_node_id) == source_proxy_id:
        return

    if not protection_conf.PROTECTION_PROXY_REPOSITORY_SERVER_ENABLED:
        raise ValidationError({
            "repository_id": (
                "Cross-proxy repository access is disabled. Bind the NAS source and repository "
                "to the same proxy node."
            )
        })

    repository_node = Node.objects.filter(
        organization_id=organization_id,
        id=repository.bind_node_id,
        role=NodeRole.PROXY,
        is_deleted=False,
    ).first()
    if repository_node is None:
        raise ValidationError({"repository_id": "Repository bound proxy node not found."})
    if repository_node.availability != Node.Availability.ONLINE:
        raise ValidationError({"repository_id": "Repository bound proxy node is offline."})

    host, _source = explicit_repository_server_host(
        repository=repository,
        node=repository_node,
    )
    if not host:
        raise ValidationError({
            "repository_id": (
                "Cross-proxy repository access requires an explicit Repository Server address."
            )
        })


def _nas_source_proxy_id(*, organization_id: int, source_ref_id: int) -> int:
    resource = (
        SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id=source_ref_id,
            is_deleted=False,
        )
        .select_related("bound_node")
        .first()
    )
    if resource is None:
        raise ValidationError({"source_ref_id": "NAS source not found."})
    if resource.bound_node_id is None:
        raise ValidationError({"source_ref_id": "NAS source is not bound to a proxy node."})
    return int(resource.bound_node_id)
