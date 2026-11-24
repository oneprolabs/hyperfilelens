"""Fail-closed resolution of extension-owned repository credentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

from common.extension_spi import get_repository_provider

if TYPE_CHECKING:
    from apps.node.models import Node

MANAGED_CREDENTIAL_MODE = "repository_temporary_identity_v1"
MANAGED_REPOSITORY_AGENT_CAPABILITY = "repository_s3_temporary_credentials_v2"
MANAGED_REPOSITORY_AGENT_UPGRADE_MESSAGE = (
    "The execution Agent must be upgraded to use Managed Storage. "
    "Upgrade it from Source Hosts, then retry the operation."
)


class ManagedRepositoryAgentUpgradeRequired(ValidationError):
    """A deterministic incompatibility, not a retryable transport failure."""

    def __init__(self) -> None:
        super().__init__(
            MANAGED_REPOSITORY_AGENT_UPGRADE_MESSAGE,
            code="AGENT_UPGRADE_REQUIRED",
        )


def assert_managed_repository_agent_compatible(node: Node) -> None:
    """Require the current scoped-credential protocol before Agent execution."""
    from apps.node.services.capabilities import node_supports_capability

    if not node_supports_capability(node, MANAGED_REPOSITORY_AGENT_CAPABILITY):
        raise ManagedRepositoryAgentUpgradeRequired()


@dataclass(frozen=True)
class RepositoryTaskAuthority:
    """Server-only execution evidence; never deserialize this from a task payload."""

    actor_id: int
    organization_id: int
    repository_id: int
    purpose: str
    requirements: tuple[tuple[str, int, str], ...]
    snapshot_directory_id: int | None = None
    operation: str = "read"


def repository_task_authority(
    repository, *, actor_id: int | None, organization_id: int,
    purpose: str, requirements: tuple[tuple[str, int, str], ...],
    snapshot_directory_id: int | None = None, operation: str = "read",
):
    """Legacy repositories stay on their original path without extra metadata."""
    if managed_repository_provider(repository) is None:
        return None
    if (
        not actor_id or repository.organization_id != organization_id
        or operation not in {"read", "write"}
    ):
        raise ValidationError("Authenticated repository execution identity is required.")
    return RepositoryTaskAuthority(
        actor_id=actor_id, organization_id=organization_id, repository_id=repository.pk,
        purpose=purpose, requirements=requirements,
        snapshot_directory_id=snapshot_directory_id,
        operation=operation,
    )


def managed_repository_provider(repository):
    # The flag is routing metadata, never authority. The adapter must verify
    # the persisted association and canonical location on every entry.
    mode = (repository.config or {}).get("credential_mode")
    if mode and mode != MANAGED_CREDENTIAL_MODE:
        raise ValidationError("Unsupported Repository credential mode.")
    provider = get_repository_provider()
    if mode == MANAGED_CREDENTIAL_MODE:
        if provider is None or not provider.manages(repository):
            raise ValidationError("Managed Repository credentials are unavailable.")
        return provider
    return None
