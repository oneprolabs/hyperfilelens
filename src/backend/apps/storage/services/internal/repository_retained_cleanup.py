"""Narrow, non-destructive deletion of failed managed repository registrations."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.storage.repositories.models import Repository, RepositoryLocationClaim, RepositoryTask
from apps.storage.services.internal.repository_location import ACTIVE_CLAIM_STATES
from apps.storage.services.internal.repository_provider import (
    MANAGED_CREDENTIAL_MODE, managed_repository_provider,
)
from apps.task.models import Task


def retains_unverified_data(task: Task) -> bool:
    return (task.request_payload or {}).get("retain_unverified_data") is True


def can_retain_unverified_data(repository: Repository) -> bool:
    """Require failed initialization provenance, not merely an Offline resource."""
    if repository.repo_type != Repository.Type.S3 or repository.removed_at is not None:
        return False
    if (repository.config or {}).get("credential_mode") != MANAGED_CREDENTIAL_MODE:
        return False
    try:
        provider = managed_repository_provider(repository)
    except ValidationError:
        # Removing EE must not turn the ordinary, blocked preflight into a
        # server error or grant a metadata-only fallback without its adapter.
        return False
    eligible = getattr(provider, "can_retain_failed_initialization", None)
    if not callable(eligible) or not eligible(repository):
        return False
    claims = repository.location_claims.filter(state__in=ACTIVE_CLAIM_STATES)
    if (
        not claims.exists()
        or claims.exclude(state=RepositoryLocationClaim.State.RESIDUAL).exists()
        or claims.filter(ownership_verified_at__isnull=False).exists()
    ):
        return False
    if repository.status == Repository.Status.CREATE_FAILED:
        return True
    if repository.status not in {Repository.Status.REMOVING, Repository.Status.REMOVE_FAILED}:
        return False
    # A worker/retry may continue only the same explicitly accepted mode and
    # Claim generation. Never reinterpret a failed destructive delete.
    previous = RepositoryTask.objects.filter(
        repository=repository,
        operation_type=RepositoryTask.OperationType.CLEANUP_REPOSITORY,
        triggered_by_task=None,
    ).select_related("task").order_by("-pk").first()
    if previous is None or previous.force or not retains_unverified_data(previous.task):
        return False
    plan = (previous.task.request_payload or {}).get("cleanup_plan") or {}
    return plan.get("location_claim_ids") == list(
        claims.order_by("id").values_list("id", flat=True)
    )
