"""HFL authorization adapter for SourceLens shared Q&A snapshots."""

from __future__ import annotations

import logging
import uuid as uuid_lib
from typing import Any

from django.core import signing
from django.db import transaction

from apps.lens_bridge.models import LensRunSubmission, LensSessionLink
from apps.lens_bridge.services import sl_client


SHARE_ACCESS_SIGNING_SALT = "lens_bridge.copilot_shared_qa"
logger = logging.getLogger(__name__)


class CopilotShareNotFoundError(Exception):
    """The requested share does not belong to this HFL Chat."""


def _list_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [row for row in payload["results"] if isinstance(row, dict)]
    return []


def _canonical_uuid(value: Any) -> str:
    return str(uuid_lib.UUID(str(value)))


def _share_entries(link: LensSessionLink) -> list[dict[str, str]]:
    state = link.share_state_json if isinstance(link.share_state_json, dict) else {}
    rows = state.get("shares") if isinstance(state, dict) else []
    if not isinstance(rows, list):
        return []
    entries: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            entries.append(
                {
                    "uuid": _canonical_uuid(row.get("uuid")),
                    "run_uuid": _canonical_uuid(row.get("run_uuid")),
                    "token": str(row.get("token") or ""),
                }
            )
        except (TypeError, ValueError, AttributeError):
            continue
    return [row for row in entries if row["token"]]


def _require_shareable_link(link: LensSessionLink) -> None:
    """Reject share writes once the HFL Chat has started cleanup."""

    if (
        link.status != LensSessionLink.Status.ACTIVE
        or link.lifecycle_status != LensSessionLink.LifecycleStatus.READY
        or link.cleanup_intent != LensSessionLink.CleanupIntent.NONE
    ):
        raise CopilotShareNotFoundError()


@transaction.atomic
def _record_share(link: LensSessionLink, share: dict[str, Any]) -> None:
    share_uuid = _canonical_uuid(share.get("uuid"))
    run_uuid = _canonical_uuid(share.get("run_uuid"))
    token = str(share.get("token") or "")
    if not token:
        raise sl_client.LensBridgeError(
            "SourceLens returned a shared Q&A without a token."
        )
    locked = LensSessionLink.objects.select_for_update().get(pk=link.pk)
    _require_shareable_link(locked)
    entries = [row for row in _share_entries(locked) if row["uuid"] != share_uuid]
    entries.append({"uuid": share_uuid, "run_uuid": run_uuid, "token": token})
    locked.share_state_json = {"version": 1, "shares": entries}
    locked.save(update_fields=["share_state_json", "updated_at"])
    link.share_state_json = locked.share_state_json


@transaction.atomic
def _retain_share(link: LensSessionLink, share: dict[str, Any]) -> None:
    """Make one SourceLens share the only active HFL share identity."""

    normalized = _normalized_share(share)
    locked = LensSessionLink.objects.select_for_update().get(pk=link.pk)
    _require_shareable_link(locked)
    locked.share_state_json = {
        "version": 1,
        "shares": [
            {
                "uuid": normalized["uuid"],
                "run_uuid": normalized["run_uuid"],
                "token": normalized["token"],
            }
        ],
    }
    locked.save(update_fields=["share_state_json", "updated_at"])
    link.share_state_json = locked.share_state_json


@transaction.atomic
def _forget_share(link: LensSessionLink, share_uuid: str) -> None:
    _discard_share_entries(link, {_canonical_uuid(share_uuid)})


@transaction.atomic
def _discard_share_entries(
    link: LensSessionLink,
    share_uuids: set[str],
) -> None:
    """Remove only the specified identities without overwriting newer state."""

    locked = LensSessionLink.objects.select_for_update().get(pk=link.pk)
    entries = [
        row for row in _share_entries(locked) if row["uuid"] not in share_uuids
    ]
    locked.share_state_json = {"version": 1, "shares": entries}
    locked.save(update_fields=["share_state_json", "updated_at"])
    link.share_state_json = locked.share_state_json


def _session_messages(link: LensSessionLink) -> list[dict[str, Any]]:
    if not link.sl_session_uuid:
        return []
    payload = sl_client.request_json(
        "GET",
        f"/api/lens/sessions/{link.sl_session_uuid}/messages/",
        hfl_user=link.hfl_user,
    )
    return _list_rows(payload)


def _session_run_uuids(
    link: LensSessionLink,
    *,
    messages: list[dict[str, Any]] | None = None,
) -> set[str]:
    run_uuids = {
        str(value)
        for value in LensRunSubmission.objects.filter(
            session_link=link,
            sl_run_uuid__isnull=False,
        ).values_list("sl_run_uuid", flat=True)
    }
    run_uuids.update(row["run_uuid"] for row in _share_entries(link))
    for row in messages or []:
        value = row.get("run")
        if not value:
            continue
        try:
            run_uuids.add(_canonical_uuid(value))
        except (TypeError, ValueError, AttributeError):
            continue
    return run_uuids


def _list_my_shares(link: LensSessionLink) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = sl_client.request_json(
            "GET",
            "/api/lens/shares/",
            params={"page": page, "page_size": 500},
            hfl_user=link.hfl_user,
        )
        batch = _list_rows(payload)
        rows.extend(batch)
        if not batch or not isinstance(payload, dict) or not payload.get("next"):
            return rows
        page += 1


def _latest_shareable_turn(
    messages: list[dict[str, Any]],
) -> dict[str, str] | None:
    for index in range(len(messages) - 1, -1, -1):
        answer = messages[index]
        if answer.get("role") != "assistant":
            continue
        content = str(answer.get("content") or "").strip()
        run_uuid = answer.get("run")
        if not content or not run_uuid or not answer.get("completed_at"):
            continue
        try:
            canonical_run_uuid = _canonical_uuid(run_uuid)
        except (TypeError, ValueError, AttributeError):
            continue
        question = ""
        for candidate in reversed(messages[:index]):
            if candidate.get("role") == "user":
                question = str(candidate.get("content") or "").strip()
                break
        return {
            "run_uuid": canonical_run_uuid,
            "question": question,
            "answer": content,
        }
    return None


def _normalized_share(share: dict[str, Any]) -> dict[str, Any]:
    try:
        return {
            **share,
            "uuid": _canonical_uuid(share.get("uuid")),
            "run_uuid": _canonical_uuid(share.get("run_uuid")),
            "token": str(share.get("token") or ""),
        }
    except (TypeError, ValueError, AttributeError) as exc:
        raise sl_client.LensBridgeError(
            "SourceLens returned an invalid shared Q&A response."
        ) from exc


def _revoke_remote_share(link: LensSessionLink, share_uuid: str) -> None:
    """Revoke one SourceLens share, treating an absent row as converged."""

    try:
        sl_client.request_json(
            "DELETE",
            f"/api/lens/shares/{share_uuid}/",
            hfl_user=link.hfl_user,
        )
    except sl_client.LensBridgeError as exc:
        if exc.status_code != 404:
            raise


@transaction.atomic
def _make_single_active_share(
    link: LensSessionLink,
    share: dict[str, Any],
) -> dict[str, Any]:
    """Serialize replacement and retain one current Chat share identity."""

    normalized = _normalized_share(share)
    _record_share(link, normalized)
    stale_uuids = {
        row["uuid"]
        for row in _share_entries(link)
        if row["uuid"] != normalized["uuid"]
    }
    for share_uuid in sorted(stale_uuids):
        _revoke_remote_share(link, share_uuid)
    _retain_share(link, normalized)
    return normalized


def get_share_candidate(link: LensSessionLink) -> dict[str, Any]:
    messages = _session_messages(link)
    turn = _latest_shareable_turn(messages)
    if turn is None:
        return {"shareable": False, "share": None}
    session_run_uuids = _session_run_uuids(link, messages=messages)
    existing = None
    for row in _list_my_shares(link):
        try:
            normalized = _normalized_share(row)
        except sl_client.LensBridgeError:
            continue
        if normalized["run_uuid"] not in session_run_uuids:
            continue
        # Recover identities left between the SourceLens create and HFL record
        # steps, so the next share operation can converge them to one link.
        _record_share(link, normalized)
        if existing is None and normalized["run_uuid"] == turn["run_uuid"]:
            existing = normalized
    if existing is not None:
        existing = _make_single_active_share(link, existing)
    return {"shareable": True, **turn, "share": existing}


def create_share(link: LensSessionLink, *, title: str = "") -> dict[str, Any]:
    candidate = get_share_candidate(link)
    if not candidate.get("shareable"):
        raise CopilotShareNotFoundError()
    existing = candidate.get("share")
    if isinstance(existing, dict):
        return _make_single_active_share(link, existing)
    payload = sl_client.request_json(
        "POST",
        f"/api/lens/runs/{candidate['run_uuid']}/share/",
        json_body={"title": title.strip()[:200]},
        hfl_user=link.hfl_user,
    )
    if not isinstance(payload, dict):
        raise sl_client.LensBridgeError(
            "SourceLens returned an invalid shared Q&A response."
        )
    share = _normalized_share(payload)
    if share["run_uuid"] != candidate["run_uuid"]:
        raise sl_client.LensBridgeError(
            "SourceLens shared a different Q&A run than requested."
        )
    try:
        share = _make_single_active_share(link, share)
    except Exception:
        # SourceLens and HFL cannot share one database transaction. Compensate
        # the remote create so an untracked link cannot survive an HFL failure.
        try:
            sl_client.request_json(
                "DELETE",
                f"/api/lens/shares/{share['uuid']}/",
                hfl_user=link.hfl_user,
            )
        except sl_client.LensBridgeError as cleanup_error:
            logger.warning(
                "Unable to compensate unrecorded SourceLens share %s: %s",
                share["uuid"],
                cleanup_error,
            )
        try:
            _forget_share(link, share["uuid"])
        except Exception as state_error:
            logger.warning(
                "Unable to forget compensated SourceLens share %s: %s",
                share["uuid"],
                state_error,
            )
        raise
    return share


def _owned_session_share(
    link: LensSessionLink,
    share_uuid: str,
) -> dict[str, Any]:
    canonical = _canonical_uuid(share_uuid)
    messages = _session_messages(link)
    run_uuids = _session_run_uuids(link, messages=messages)
    for row in _list_my_shares(link):
        try:
            normalized = _normalized_share(row)
        except sl_client.LensBridgeError:
            continue
        if normalized["uuid"] == canonical and normalized["run_uuid"] in run_uuids:
            _record_share(link, normalized)
            return normalized
    raise CopilotShareNotFoundError()


def update_share_title(
    link: LensSessionLink,
    share_uuid: str,
    *,
    title: str,
) -> dict[str, Any]:
    share = _owned_session_share(link, share_uuid)
    payload = sl_client.request_json(
        "PATCH",
        f"/api/lens/shares/{share['uuid']}/",
        json_body={"title": title.strip()[:200]},
        hfl_user=link.hfl_user,
    )
    if not isinstance(payload, dict):
        raise sl_client.LensBridgeError(
            "SourceLens returned an invalid shared Q&A response."
        )
    updated = _normalized_share(payload)
    if (
        updated["uuid"] != share["uuid"]
        or updated["run_uuid"] != share["run_uuid"]
    ):
        raise sl_client.LensBridgeError(
            "SourceLens updated a different shared Q&A than requested."
        )
    return _make_single_active_share(link, updated)


def revoke_share(link: LensSessionLink, share_uuid: str) -> None:
    canonical = _canonical_uuid(share_uuid)
    current = LensSessionLink.objects.only("share_state_json").get(pk=link.pk)
    if not any(row["uuid"] == canonical for row in _share_entries(current)):
        raise CopilotShareNotFoundError()
    link.share_state_json = current.share_state_json
    revoke_session_shares(link)


def revoke_session_shares(link: LensSessionLink) -> int:
    """Revoke all Q&A links before HFL deletes the wider Chat resource graph."""

    current = LensSessionLink.objects.only("share_state_json").get(pk=link.pk)
    known = {row["uuid"]: row for row in _share_entries(current)}
    if not known:
        return 0

    revoked = 0
    for share_uuid in sorted(known):
        _revoke_remote_share(link, share_uuid)
        revoked += 1
    _discard_share_entries(link, set(known))
    return revoked


def make_share_access_token(link: LensSessionLink, share: dict[str, Any]) -> str:
    normalized = _normalized_share(share)
    return signing.dumps(
        {
            "session_id": link.id,
            "share_uuid": normalized["uuid"],
        },
        salt=SHARE_ACCESS_SIGNING_SALT,
        compress=True,
    )


def resolve_share_access_token(raw_token: str) -> dict[str, Any]:
    try:
        payload = signing.loads(raw_token, salt=SHARE_ACCESS_SIGNING_SALT)
    except signing.BadSignature as exc:
        raise CopilotShareNotFoundError() from exc
    if not isinstance(payload, dict):
        raise CopilotShareNotFoundError()
    try:
        return {
            "session_id": int(payload["session_id"]),
            "share_uuid": _canonical_uuid(payload["share_uuid"]),
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CopilotShareNotFoundError() from exc


def require_active_share_access(
    *,
    organization_id: int,
    raw_token: str,
) -> tuple[LensSessionLink, dict[str, Any]]:
    access = resolve_share_access_token(raw_token)
    link = LensSessionLink.objects.filter(
        pk=access["session_id"],
        organization_id=organization_id,
        status=LensSessionLink.Status.ACTIVE,
        lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        cleanup_intent=LensSessionLink.CleanupIntent.NONE,
    ).first()
    if link is None:
        raise CopilotShareNotFoundError()
    entries = _share_entries(link)
    if len(entries) != 1:
        # Replacement may temporarily retain multiple identities so cleanup
        # can be retried. Fail closed until reconciliation chooses one.
        raise CopilotShareNotFoundError()
    expected = next(
        (
            row
            for row in entries
            if row["uuid"] == access["share_uuid"]
        ),
        None,
    )
    if expected is None:
        raise CopilotShareNotFoundError()
    access["share_token"] = expected["token"]
    return link, access
