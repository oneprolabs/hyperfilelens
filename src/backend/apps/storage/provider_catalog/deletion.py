"""Reviewed, atomic deletion of unused custom Provider Catalog entries."""

from __future__ import annotations

import re
from typing import Any

from django.db import transaction

from apps.storage.conf import provider_catalog_limits
from apps.storage.provider_catalog.catalog import default_provider_records
from apps.storage.provider_catalog.errors import ProviderCatalogValidationError
from apps.storage.provider_catalog.locks import lock_provider_ids
from apps.storage.provider_catalog.models import (
    StorageProviderOverride,
    StorageProviderValidationRun,
)
from apps.storage.provider_catalog.operations import _conflict, _expires_at, _sign, _unsign
from apps.storage.repositories.models import Repository

DELETE_TOKEN_SALT = "storage.provider-catalog.delete-review.v1"


def _selection(provider_ids: list[str]) -> list[str]:
    if (
        not isinstance(provider_ids, list)
        or not provider_ids
        or len(provider_ids) > provider_catalog_limits()["max_providers"]
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,49}", item) is None
            for item in provider_ids
        )
        or len(set(provider_ids)) != len(provider_ids)
    ):
        raise ProviderCatalogValidationError(
            "Select a non-empty, unique list of Provider IDs."
        )
    return sorted(provider_ids)


def _records(provider_ids: list[str]) -> dict[str, StorageProviderOverride]:
    defaults = default_provider_records()
    if any(provider_id in defaults for provider_id in provider_ids):
        _conflict(
            "Built-in Providers cannot be deleted. Use Edit to disable them, "
            "or Reset to Defaults to restore their configuration.",
            code="PROVIDER_CATALOG_DELETE_DEFAULT",
        )
    return {
        record.provider_id: record
        for record in StorageProviderOverride.objects.select_for_update()
        .filter(provider_id__in=provider_ids)
        .order_by("provider_id")
    }


def _assert_unused(provider_ids: list[str]) -> None:
    if (
        Repository.objects.filter(
            repo_type=Repository.Type.S3, s3_platform__in=provider_ids
        )
        .exclude(status=Repository.Status.REMOVED)
        .exists()
    ):
        _conflict(
            "One or more selected Providers are still used by repositories. "
            "Remove those repositories before deleting the Providers.",
            code="PROVIDER_CATALOG_DELETE_IN_USE",
        )
    safe_statuses = {
        StorageProviderValidationRun.Status.PASSED,
        StorageProviderValidationRun.Status.VALIDATION_FAILED,
        StorageProviderValidationRun.Status.CANCELLED,
        StorageProviderValidationRun.Status.EXPIRED,
    }
    runs = (
        StorageProviderValidationRun.objects.select_for_update()
        .filter(provider_id__in=provider_ids)
        .order_by("provider_id")
    )
    for run in runs:
        if (
            run.status not in safe_statuses
            or run.region_validations.filter(
                status__in=["running", "cleanup_failed"]
            ).exists()
            or run.region_validations.filter(bucket_name__isnull=False)
            .exclude(bucket_name="")
            .exists()
        ):
            _conflict(
                "Finish connection checks and clean up temporary cloud resources "
                "before deleting the selected Providers.",
                code="PROVIDER_CATALOG_DELETE_VALIDATION_ACTIVE",
            )


def delete_review(*, provider_ids: list[str], user_id: int) -> dict[str, Any]:
    provider_ids = _selection(provider_ids)
    with transaction.atomic():
        lock_provider_ids(provider_ids)
        records = _records(provider_ids)
        if sorted(records) != provider_ids:
            _conflict(
                "A selected Provider no longer exists. Refresh the Provider list.",
                code="PROVIDER_CATALOG_NOT_FOUND",
            )
        _assert_unused(provider_ids)
        expires_at = _expires_at()
        payload = {
            "kind": "delete",
            "user_id": user_id,
            "provider_ids": provider_ids,
            "providers": [
                {
                    "provider_id": provider_id,
                    "checksum": records[provider_id].checksum,
                    "updated_at": records[provider_id].updated_at.isoformat(),
                }
                for provider_id in provider_ids
            ],
            "expires_at": expires_at,
        }
        return {
            "provider_ids": provider_ids,
            "providers": [
                {
                    "id": provider_id,
                    "display_name": records[provider_id].config["display_name"],
                    "region_count": len(records[provider_id].config["regions"]),
                }
                for provider_id in provider_ids
            ],
            "expires_at": expires_at,
            "delete_token": _sign(payload, salt=DELETE_TOKEN_SALT),
        }


def delete_confirm(
    *, provider_ids: list[str], user_id: int, delete_token: str
) -> dict[str, Any]:
    provider_ids = _selection(provider_ids)
    token = _unsign(
        delete_token, salt=DELETE_TOKEN_SALT, kind="delete", user_id=user_id
    )
    baselines = {item["provider_id"]: item for item in token.get("providers") or []}
    if token.get("provider_ids") != provider_ids or sorted(baselines) != provider_ids:
        _conflict(
            "Deletion selection changed after Review.",
            code="PROVIDER_CATALOG_INPUT_CHANGED",
        )
    with transaction.atomic():
        lock_provider_ids(provider_ids)
        records = _records(provider_ids)
        if not records:
            return {"deleted": False, "idempotent": True, "provider_ids": provider_ids}
        if sorted(records) != provider_ids or any(
            records[item].checksum != baselines[item]["checksum"]
            or records[item].updated_at.isoformat() != baselines[item]["updated_at"]
            for item in records
        ):
            _conflict(
                "A selected Provider changed after Review. Review the deletion again.",
                code="PROVIDER_CATALOG_BASELINE_CHANGED",
            )
        _assert_unused(provider_ids)
        StorageProviderOverride.objects.filter(provider_id__in=provider_ids).delete()
    return {"deleted": True, "idempotent": False, "provider_ids": provider_ids}
