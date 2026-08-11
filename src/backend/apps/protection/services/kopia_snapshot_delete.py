from __future__ import annotations

from typing import Any


_KOPIA_SNAPSHOT_ID_SENTINELS = {"none", "null"}


def normalize_kopia_snapshot_id(value: object) -> str:
    """Return a usable Kopia snapshot ID, or an empty string for legacy sentinels."""
    snapshot_id = str(value or "").strip()
    if snapshot_id.lower() in _KOPIA_SNAPSHOT_ID_SENTINELS:
        return ""
    return snapshot_id


def _item_delete_text(item: dict[str, Any]) -> str:
    parts: list[str] = [str(item.get("error_message") or "")]
    delete = item.get("delete")
    if isinstance(delete, dict):
        parts.append(str(delete.get("stderr") or ""))
        parts.append(str(delete.get("stderr_tail") or ""))
        parts.append(str(delete.get("stdout") or ""))
    return " ".join(part for part in parts if part).lower()


def kopia_snapshot_delete_already_absent(item: dict[str, Any]) -> bool:
    """True when Kopia reports the snapshot id is not present in the repository."""
    if str(item.get("status") or "").strip().lower() == "success":
        return False
    text = _item_delete_text(item)
    return "no snapshots matched" in text


def classify_kopia_snapshot_delete_results(
    item_results: list[dict[str, Any]],
) -> tuple[set[str], set[str], list[dict[str, Any]]]:
    """Return (deleted_ids, already_absent_ids, hard_failures)."""
    deleted: set[str] = set()
    already_absent: set[str] = set()
    hard_failures: list[dict[str, Any]] = []
    for item in item_results:
        if not isinstance(item, dict):
            continue
        snapshot_id = normalize_kopia_snapshot_id(item.get("kopia_snapshot_id"))
        if not snapshot_id:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == "success":
            deleted.add(snapshot_id)
            continue
        if kopia_snapshot_delete_already_absent(item):
            already_absent.add(snapshot_id)
            continue
        hard_failures.append(item)
    return deleted, already_absent, hard_failures
