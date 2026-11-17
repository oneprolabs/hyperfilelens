"""Fail-closed resolution of extension-owned repository credentials."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from common.extension_spi import get_repository_provider

MANAGED_CREDENTIAL_MODE = "repository_temporary_identity_v1"


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
