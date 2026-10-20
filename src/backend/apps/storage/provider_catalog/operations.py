"""Provider Catalog review/apply, export, and reset operations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.storage.conf import (
    provider_catalog_limits,
    provider_catalog_review_token_ttl_seconds,
)
from apps.storage.provider_catalog.catalog import (
    default_provider_records,
    effective_provider_records,
)
from apps.storage.provider_catalog.diff import provider_diff
from apps.storage.provider_catalog.errors import (
    ProviderCatalogConflictError,
    ProviderCatalogValidationError,
)
from apps.storage.provider_catalog.models import StorageProviderOverride
from apps.storage.provider_catalog.locks import lock_provider_ids
from apps.storage.provider_catalog.schema import (
    CURRENT_SCHEMA_VERSION,
    canonical_json_bytes,
    catalog_checksum,
    parse_catalog,
    provider_checksum,
)
from apps.storage.provider_catalog.credentials import delete_validation_credentials
from apps.storage.provider_catalog.models import StorageProviderValidationRun
from apps.storage.provider_catalog.validation import (
    import_validation_evidence,
    write_validation_run_audit,
)
from apps.task.models import Task


IMPORT_TOKEN_SALT = "storage.provider-catalog.import-review.v1"
RESET_TOKEN_SALT = "storage.provider-catalog.reset-review.v1"


def _conflict(message: str, *, code: str = "PROVIDER_CATALOG_CONFLICT") -> None:
    exc = ProviderCatalogConflictError(message)
    exc.code = code
    raise exc


def _expires_at(*, deadline: datetime | None = None) -> str:
    expires = timezone.now() + timedelta(
        seconds=provider_catalog_review_token_ttl_seconds()
    )
    if deadline is not None:
        expires = min(expires, deadline)
    return expires.isoformat()


def _sign(payload: dict[str, Any], *, salt: str) -> str:
    return signing.dumps(payload, salt=salt, compress=True)


def _unsign(token: str, *, salt: str, kind: str, user_id: int) -> dict[str, Any]:
    try:
        payload = signing.loads(
            token,
            salt=salt,
            max_age=provider_catalog_review_token_ttl_seconds(),
        )
    except signing.SignatureExpired:
        _conflict("Review token has expired.", code="PROVIDER_CATALOG_REVIEW_EXPIRED")
    except signing.BadSignature:
        _conflict("Review token is invalid.", code="PROVIDER_CATALOG_REVIEW_INVALID")
    if not isinstance(payload, dict) or payload.get("kind") != kind:
        _conflict("Review token is invalid.", code="PROVIDER_CATALOG_REVIEW_INVALID")
    if payload.get("user_id") != user_id:
        _conflict(
            "Review token belongs to a different operator.",
            code="PROVIDER_CATALOG_REVIEW_USER_MISMATCH",
        )
    try:
        expired = timezone.now() >= datetime.fromisoformat(payload["expires_at"])
    except (KeyError, TypeError, ValueError):
        _conflict("Review token is invalid.", code="PROVIDER_CATALOG_REVIEW_INVALID")
    if expired:
        _conflict("Review token has expired.", code="PROVIDER_CATALOG_REVIEW_EXPIRED")
    return payload


def _override_records(provider_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    return {
        item["provider_id"]: item
        for item in StorageProviderOverride.objects.filter(
            provider_id__in=provider_ids
        ).values("provider_id", "schema_version", "checksum")
    }


def _states(
    provider_ids: list[str],
) -> dict[str, dict[str, Any]]:
    defaults = default_provider_records()
    effective = effective_provider_records()
    overrides = _override_records(provider_ids)
    return {
        provider_id: {
            "default_checksum": (
                defaults[provider_id]["checksum"] if provider_id in defaults else None
            ),
            "override_checksum": (
                overrides[provider_id]["checksum"] if provider_id in overrides else None
            ),
            "effective_checksum": (
                effective[provider_id]["checksum"] if provider_id in effective else None
            ),
            "override_exists": provider_id in overrides,
        }
        for provider_id in provider_ids
    }


def _candidate_records(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    version = catalog["schema_version"]
    return {
        provider["id"]: {
            "config": provider,
            "checksum": provider_checksum(provider, schema_version=version),
        }
        for provider in catalog["providers"]
    }


def _persistence_action(
    *,
    candidate_checksum: str,
    default_checksum: str | None,
) -> str:
    return (
        "delete_override"
        if default_checksum is not None and candidate_checksum == default_checksum
        else "upsert_override"
    )


def _build_import_preview(raw: str | bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = parse_catalog(raw)
    candidates = _candidate_records(catalog)
    provider_ids = sorted(candidates)
    effective = effective_provider_records()
    states = _states(provider_ids)
    provider_previews: list[dict[str, Any]] = []
    high_risk_ids: list[str] = []
    unchanged_targets: list[str] = []
    for provider_id in provider_ids:
        candidate = candidates[provider_id]
        current = effective.get(provider_id)
        diff = provider_diff(
            provider_id=provider_id,
            before=current["config"] if current else None,
            after=candidate["config"],
        )
        if diff["change_type"] == "unchanged":
            unchanged_targets.append(provider_id)
        high_risk_ids.extend(item["id"] for item in diff["high_risk_changes"])
        provider_previews.append(
            {
                **diff,
                "current_checksum": states[provider_id]["effective_checksum"],
                "candidate_checksum": candidate["checksum"],
                "default_checksum": states[provider_id]["default_checksum"],
                "override_checksum": states[provider_id]["override_checksum"],
                "persistence_action": _persistence_action(
                    candidate_checksum=candidate["checksum"],
                    default_checksum=states[provider_id]["default_checksum"],
                ),
            }
        )
    preview = {
        "schema_version": catalog["schema_version"],
        "input_checksum": catalog_checksum(catalog),
        "target_provider_ids": provider_ids,
        "unchanged_target_provider_ids": unchanged_targets,
        "skipped_provider_ids": sorted(set(effective) - set(provider_ids)),
        "providers": provider_previews,
        "high_risk_confirmation_ids": sorted(set(high_risk_ids)),
    }
    return catalog, preview


def import_diff(raw: str | bytes) -> dict[str, Any]:
    _catalog, preview = _build_import_preview(raw)
    return preview


def import_review(raw: str | bytes, *, user_id: int) -> dict[str, Any]:
    catalog, preview = _build_import_preview(raw)
    states = _states(preview["target_provider_ids"])
    candidate_checksums = {
        item["provider_id"]: item["candidate_checksum"] for item in preview["providers"]
    }
    evidence = [
        import_validation_evidence(
            provider_id=provider_id,
            candidate_checksum=candidate_checksums[provider_id],
            requested_by_id=user_id,
        )
        for provider_id in preview["target_provider_ids"]
    ]
    validation_risks = [
        f"validation:{'partial' if item['status'] == 'passed_partial' else item['status']}:{item['provider_id']}"
        for item in evidence
        if item["status"] != "passed_complete"
        and item["status"] not in {"running", "cleanup_required"}
    ]
    required_risks = sorted(
        set(preview["high_risk_confirmation_ids"] + validation_risks)
    )
    evidence_deadlines = [
        datetime.fromisoformat(item["expires_at"])
        for item in evidence
        if item.get("status") in {"passed_complete", "passed_partial"}
        and item.get("expires_at")
    ]
    expires_at = _expires_at(
        deadline=min(evidence_deadlines) if evidence_deadlines else None
    )
    token_providers = []
    for item in preview["providers"]:
        provider_id = item["provider_id"]
        token_providers.append(
            {
                "provider_id": provider_id,
                **states[provider_id],
                "candidate_checksum": item["candidate_checksum"],
                "persistence_action": item["persistence_action"],
                "run_id": next(
                    (
                        evidence_item["run_id"]
                        if evidence_item["status"]
                        in {"passed_complete", "passed_partial"}
                        else None
                    )
                    for evidence_item in evidence
                    if evidence_item["provider_id"] == provider_id
                ),
                "validation_status": next(
                    evidence_item["status"]
                    for evidence_item in evidence
                    if evidence_item["provider_id"] == provider_id
                ),
            }
        )
    payload = {
        "kind": "import",
        "user_id": user_id,
        "schema_version": catalog["schema_version"],
        "input_checksum": preview["input_checksum"],
        "provider_ids": preview["target_provider_ids"],
        "providers": token_providers,
        "required_risk_confirmation_ids": required_risks,
        "expires_at": expires_at,
    }
    return {
        **preview,
        "validation_evidence": evidence,
        "required_risk_confirmation_ids": required_risks,
        "expires_at": expires_at,
        "review_token": _sign(payload, salt=IMPORT_TOKEN_SALT),
    }


def _validate_risk_confirmations(required: list[str], submitted: list[str]) -> None:
    if len(submitted) != len(set(submitted)) or set(submitted) != set(required):
        raise ProviderCatalogValidationError(
            "All and only the Review risk confirmations must be submitted.",
            issues=[
                {
                    "path": "$.risk_confirmations",
                    "code": "risk_confirmation_mismatch",
                    "message": "Risk confirmations do not match the Review.",
                }
            ],
        )


def _baseline_matches(current: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return all(
        current[field] == baseline.get(field)
        for field in ("default_checksum", "override_checksum", "effective_checksum")
    )


def _desired_result_reached(
    *,
    current: dict[str, Any],
    candidate_checksum: str,
    action: str,
) -> bool:
    if current["effective_checksum"] != candidate_checksum:
        return False
    if action == "delete_override":
        return not current["override_exists"]
    return (
        current["override_exists"]
        and current["override_checksum"] == candidate_checksum
    )


def import_apply(
    raw: str | bytes,
    *,
    user_id: int,
    input_checksum: str,
    review_token: str,
    risk_confirmations: list[str],
) -> dict[str, Any]:
    catalog = parse_catalog(raw)
    candidates = _candidate_records(catalog)
    normalized_input_checksum = catalog_checksum(catalog)
    token = _unsign(
        review_token,
        salt=IMPORT_TOKEN_SALT,
        kind="import",
        user_id=user_id,
    )
    provider_ids = sorted(candidates)
    if (
        input_checksum != normalized_input_checksum
        or token.get("input_checksum") != normalized_input_checksum
        or token.get("schema_version") != catalog["schema_version"]
        or token.get("provider_ids") != provider_ids
    ):
        _conflict(
            "Imported configuration changed after Review.",
            code="PROVIDER_CATALOG_INPUT_CHANGED",
        )
    _validate_risk_confirmations(
        token.get("required_risk_confirmation_ids") or [],
        risk_confirmations,
    )
    token_items = {item["provider_id"]: item for item in token.get("providers") or []}
    if sorted(token_items) != provider_ids:
        _conflict(
            "Review token scope is invalid.", code="PROVIDER_CATALOG_REVIEW_INVALID"
        )

    with transaction.atomic():
        lock_provider_ids(provider_ids)
        locked_runs = {
            str(run.id): run
            for run in StorageProviderValidationRun.objects.select_for_update()
            .filter(provider_id__in=provider_ids)
            .prefetch_related("region_validations")
            .order_by("provider_id")
        }
        current_states = _states(provider_ids)
        if all(
            _desired_result_reached(
                current=current_states[provider_id],
                candidate_checksum=candidates[provider_id]["checksum"],
                action=token_items[provider_id]["persistence_action"],
            )
            for provider_id in provider_ids
        ):
            return {
                "applied": False,
                "idempotent": True,
                "input_checksum": normalized_input_checksum,
                "provider_ids": provider_ids,
                "changes": [],
            }
        for provider_id in provider_ids:
            baseline = token_items[provider_id]
            current = current_states[provider_id]
            candidate = candidates[provider_id]
            current_evidence = import_validation_evidence(
                provider_id=provider_id,
                candidate_checksum=candidate["checksum"],
                requested_by_id=user_id,
            )
            if current_evidence["status"] in {"running", "cleanup_required"}:
                _conflict(
                    f"Provider {provider_id!r} validation is not safe to apply.",
                    code="PROVIDER_VALIDATION_BLOCKS_APPLY",
                )
            evidence_changed = baseline.get(
                "validation_status"
            ) != current_evidence.get("status")
            if baseline.get("validation_status") in {
                "passed_complete",
                "passed_partial",
            }:
                evidence_changed = evidence_changed or any(
                    baseline.get(field) != current_evidence.get(current_field)
                    for field, current_field in (
                        ("run_id", "run_id"),
                    )
                )
            if evidence_changed:
                _conflict(
                    f"Provider {provider_id!r} validation evidence changed after Review.",
                    code="PROVIDER_VALIDATION_EVIDENCE_CHANGED",
                )
            if not _baseline_matches(current, baseline):
                _conflict(
                    f"Provider {provider_id!r} changed after Review.",
                    code="PROVIDER_CATALOG_BASELINE_CHANGED",
                )
            action = _persistence_action(
                candidate_checksum=candidate["checksum"],
                default_checksum=current["default_checksum"],
            )
            if action != baseline.get("persistence_action") or candidate[
                "checksum"
            ] != baseline.get("candidate_checksum"):
                _conflict(
                    f"Provider {provider_id!r} persistence action changed after Review.",
                    code="PROVIDER_CATALOG_ACTION_CHANGED",
                )

        changes: list[dict[str, Any]] = []
        consumed_run_ids: list[str] = []
        for provider_id in provider_ids:
            candidate = candidates[provider_id]
            action = token_items[provider_id]["persistence_action"]
            before_checksum = current_states[provider_id]["effective_checksum"]
            if action == "delete_override":
                StorageProviderOverride.objects.filter(provider_id=provider_id).delete()
            else:
                StorageProviderOverride.objects.update_or_create(
                    provider_id=provider_id,
                    defaults={
                        "schema_version": catalog["schema_version"],
                        "config": candidate["config"],
                        "checksum": candidate["checksum"],
                        "updated_by_id": user_id,
                    },
                )
            changes.append(
                {
                    "provider_id": provider_id,
                    "action": action,
                    "before_checksum": before_checksum,
                    "after_checksum": candidate["checksum"],
                }
            )
            token_run_id = token_items[provider_id].get("run_id")
            if (
                token_items[provider_id].get("validation_status")
                in {"passed_complete", "passed_partial"}
                and token_run_id
            ):
                run = locked_runs.get(str(token_run_id))
                if (
                    run is None
                    or run.provider_id != provider_id
                    or run.status
                    != StorageProviderValidationRun.Status.PASSED
                    or run.candidate_checksum != candidate["checksum"]
                ):
                    _conflict(
                        f"Provider {provider_id!r} validation evidence is unavailable.",
                        code="PROVIDER_VALIDATION_EVIDENCE_CHANGED",
                    )
                Task.objects.filter(pk=run.task_id).update(
                    status=Task.Status.SUCCESS,
                    progress=100,
                    result_payload={
                        "run_id": str(run.id),
                        "provider_id": provider_id,
                        "result": "applied",
                    },
                    finished_at=timezone.now(),
                    updated_at=timezone.now(),
                )
                write_validation_run_audit(run, result="applied")
                consumed_run_ids.append(str(run.id))
                run.delete()
        if consumed_run_ids:
            transaction.on_commit(
                lambda run_ids=tuple(consumed_run_ids): [
                    delete_validation_credentials(run_id) for run_id in run_ids
                ]
            )
    return {
        "applied": True,
        "idempotent": False,
        "input_checksum": normalized_input_checksum,
        "provider_ids": provider_ids,
        "changes": changes,
        "consumed_validation_run_ids": consumed_run_ids,
    }


def export_catalog(
    provider_ids: list[str] | None = None,
) -> tuple[dict[str, Any], bytes]:
    effective = effective_provider_records()
    selected = (
        list(effective)
        if provider_ids is None
        else list(dict.fromkeys(provider_ids))
    )
    unknown = sorted(set(selected) - set(effective))
    if not selected:
        raise ProviderCatalogValidationError(
            "At least one Provider must be selected for export."
        )
    if unknown:
        raise ProviderCatalogValidationError(
            "Export contains unsupported Provider IDs.",
            issues=[
                {
                    "path": "$.provider_ids",
                    "code": "unknown_provider",
                    "message": f"Unknown Provider IDs: {', '.join(unknown)}.",
                }
            ],
        )
    catalog = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "providers": [effective[provider_id]["config"] for provider_id in selected],
    }
    return catalog, canonical_json_bytes(catalog) + b"\n"


@transaction.atomic
def reset_review(
    *,
    user_id: int,
    provider_id: str | None = None,
    provider_ids: list[str] | None = None,
) -> dict[str, Any]:
    defaults = default_provider_records()
    if provider_ids is not None:
        if (
            provider_id is not None
            or not isinstance(provider_ids, list)
            or not provider_ids
            or any(not isinstance(item, str) or not item for item in provider_ids)
            or len(provider_ids) > provider_catalog_limits()["max_providers"]
            or len(set(provider_ids)) != len(provider_ids)
        ):
            raise ProviderCatalogValidationError(
                "Select a non-empty, unique list of Provider IDs."
            )
        provider_ids = sorted(provider_ids)
        if any(item not in defaults for item in provider_ids):
            _conflict(
                "Every selected Provider must have a release default.",
                code="PROVIDER_CATALOG_RESET_DEFAULT_MISSING",
            )
        scope = "selected"
    elif provider_id is not None:
        if provider_id not in defaults:
            _conflict(
                "Provider cannot be reset because it is absent from the release default.",
                code="PROVIDER_CATALOG_RESET_DEFAULT_MISSING",
            )
        provider_ids = [provider_id]
        scope = "provider"
    else:
        provider_ids = sorted(defaults)
        scope = "all"
    # Keep the displayed diff and signed baseline from the same locked snapshot.
    lock_provider_ids(provider_ids)
    effective = effective_provider_records()
    states = _states(provider_ids)
    if scope == "selected" and any(
        not states[item]["override_exists"] for item in provider_ids
    ):
        _conflict(
            "Every selected Provider must have a custom configuration to restore.",
            code="PROVIDER_CATALOG_RESET_OVERRIDE_MISSING",
        )
    diffs = [
        {
            **provider_diff(
                provider_id=item,
                before=effective[item]["config"],
                after=defaults[item]["config"],
            ),
            "default_checksum": states[item]["default_checksum"],
            "override_checksum": states[item]["override_checksum"],
            "effective_checksum": states[item]["effective_checksum"],
            "persistence_action": "delete_override",
        }
        for item in provider_ids
    ]
    expires_at = _expires_at()
    payload = {
        "kind": "reset",
        "user_id": user_id,
        "scope": scope,
        "provider_ids": provider_ids,
        "providers": [{"provider_id": item, **states[item]} for item in provider_ids],
        "expires_at": expires_at,
    }
    return {
        "scope": scope,
        "provider_ids": provider_ids,
        "providers": diffs,
        "expires_at": expires_at,
        "reset_token": _sign(payload, salt=RESET_TOKEN_SALT),
    }


def reset_confirm(*, user_id: int, reset_token: str) -> dict[str, Any]:
    token = _unsign(
        reset_token,
        salt=RESET_TOKEN_SALT,
        kind="reset",
        user_id=user_id,
    )
    provider_ids = token.get("provider_ids")
    if (
        token.get("scope") not in {"all", "provider", "selected"}
        or not isinstance(provider_ids, list)
        or not provider_ids
        or provider_ids != sorted(set(provider_ids))
    ):
        _conflict(
            "Reset token scope is invalid.", code="PROVIDER_CATALOG_REVIEW_INVALID"
        )
    token_items = {item["provider_id"]: item for item in token.get("providers") or []}
    if sorted(token_items) != provider_ids:
        _conflict(
            "Reset token scope is invalid.", code="PROVIDER_CATALOG_REVIEW_INVALID"
        )

    with transaction.atomic():
        lock_provider_ids(provider_ids)
        defaults = default_provider_records()
        if token.get("scope") == "all" and sorted(defaults) != provider_ids:
            _conflict(
                "The release default Provider scope changed after Review.",
                code="PROVIDER_CATALOG_RESET_SCOPE_CHANGED",
            )
        if token.get("scope") in {"provider", "selected"} and any(
            provider_id not in defaults for provider_id in provider_ids
        ):
            _conflict(
                "Provider is no longer present in the release default.",
                code="PROVIDER_CATALOG_RESET_DEFAULT_MISSING",
            )
        current_states = _states(provider_ids)
        for provider_id in provider_ids:
            if current_states[provider_id]["default_checksum"] != token_items[
                provider_id
            ].get("default_checksum"):
                _conflict(
                    f"Provider {provider_id!r} default changed after Review.",
                    code="PROVIDER_CATALOG_BASELINE_CHANGED",
                )
        if all(not current_states[item]["override_exists"] for item in provider_ids):
            return {
                "reset": False,
                "idempotent": True,
                "scope": token["scope"],
                "provider_ids": provider_ids,
                "deleted_provider_ids": [],
            }
        for provider_id in provider_ids:
            if not _baseline_matches(
                current_states[provider_id], token_items[provider_id]
            ):
                _conflict(
                    f"Provider {provider_id!r} changed after Review.",
                    code="PROVIDER_CATALOG_BASELINE_CHANGED",
                )
        deleted = sorted(
            StorageProviderOverride.objects.filter(
                provider_id__in=provider_ids
            ).values_list("provider_id", flat=True)
        )
        StorageProviderOverride.objects.filter(provider_id__in=provider_ids).delete()
    return {
        "reset": bool(deleted),
        "idempotent": False,
        "scope": token["scope"],
        "provider_ids": provider_ids,
        "deleted_provider_ids": deleted,
    }
