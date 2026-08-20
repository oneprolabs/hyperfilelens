from __future__ import annotations

import re
from typing import Any


REPOSITORY_ALREADY_EXISTS_CODE = "STORAGE.REPOSITORY_ALREADY_EXISTS"
REPOSITORY_ALREADY_EXISTS_MESSAGE = (
    "A Kopia repository already exists at the selected location. "
    "Import is not supported in this version. Choose a different storage location."
)
AGENT_TASK_TRANSPORT_UNCONFIRMED_CODE = "AGENT_TASK_TRANSPORT_UNCONFIRMED"


class RepositoryAlreadyExistsError(RuntimeError):
    """Raised when strict repository initialization finds an existing repository."""


class RepositoryHealthTransportUnconfirmed(RuntimeError):
    """Raised when an Agent probe has no authoritative repository outcome."""

    error_code = AGENT_TASK_TRANSPORT_UNCONFIRMED_CODE


def agent_task_transport_unconfirmed(outcome: Any) -> bool:
    """Return whether an Agent outcome only proves delivery/wait uncertainty."""

    if getattr(outcome, "timed_out", False) is True:
        return True
    task = getattr(outcome, "task", None)
    status = str(getattr(task, "status", "") or "").strip().lower()
    if status in {"pending", "running", "timeout"}:
        return True
    if status in {"failed", "canceled", "cancelled"}:
        accepted_at = getattr(task, "accepted_at", None)
        result = getattr(outcome, "result", None)
        if accepted_at is None and not result:
            return True
    return False


def is_repository_health_transport_unconfirmed(exc: BaseException) -> bool:
    """Inspect an exception chain for an unconfirmed Agent health probe."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RepositoryHealthTransportUnconfirmed):
            return True
        if (
            str(getattr(current, "error_code", "") or "").strip()
            == AGENT_TASK_TRANSPORT_UNCONFIRMED_CODE
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


_REPOSITORY_CONFLICT_MARKERS = (
    "already exists",
    "already initialized",
    "repository exists",
    "found existing data in storage location",
)
_GENERIC_EXIT_MESSAGE = re.compile(
    r"^(?:exit\s+\d+\s*:\s*)?(?:exit status\s+\d+|exit\s+\d+)$",
    re.IGNORECASE,
)


def _agent_repository_command_messages(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    messages: list[str] = []
    for command_key in ("repository_create", "repository_connect", "repository_status"):
        command_result = result.get(command_key)
        if not isinstance(command_result, dict):
            continue
        for output_key in ("stderr_tail", "stderr", "stdout_tail", "stdout"):
            message = str(command_result.get(output_key) or "").strip()
            if message and message not in messages:
                messages.append(message)
    for output_key in ("error", "stderr", "detail"):
        message = str(result.get(output_key) or "").strip()
        if message and message not in messages:
            messages.append(message)
    return messages


def agent_result_has_repository_conflict(result: Any) -> bool:
    if (
        isinstance(result, dict)
        and str(result.get("error_code") or "").strip() == REPOSITORY_ALREADY_EXISTS_CODE
    ):
        return True
    if not isinstance(result, dict) or not isinstance(result.get("repository_create"), dict):
        return False
    create_result = result["repository_create"]
    output = "\n".join(
        str(create_result.get(key) or "").strip()
        for key in ("stderr_tail", "stderr", "stdout_tail", "stdout")
    ).lower()
    return any(marker in output for marker in _REPOSITORY_CONFLICT_MARKERS)


def agent_repository_failure_message(result: Any, *, last_error: str = "") -> str:
    """Prefer captured repository command output over a generic process exit."""

    fallback = str(last_error or "").strip()
    if fallback and not _GENERIC_EXIT_MESSAGE.fullmatch(fallback):
        return fallback
    messages = _agent_repository_command_messages(result)
    if messages:
        return messages[0]
    return fallback
