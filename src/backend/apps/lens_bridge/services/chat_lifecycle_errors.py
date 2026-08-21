"""Map internal Chat provisioning failures to safe product-facing errors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatLifecycleError:
    code: str
    message: str
    retryable: bool


def classify_chat_lifecycle_error(raw_error: str | None) -> ChatLifecycleError:
    """Return a stable, tenant-safe error without exposing upstream details."""

    raw = str(raw_error or "").strip()
    normalized = raw.upper()
    if "MODEL_NOT_VISION_CAPABLE" in normalized:
        return ChatLifecycleError(
            code="INSIGHT.CHAT_MODEL_NOT_VISION_CAPABLE",
            message=(
                "The configured AI model is not compatible with this Chat. "
                "Ask an administrator to check the multimodal model, then try again."
            ),
            retryable=False,
        )
    if "REMOTE CREATE OUTCOME IS UNKNOWN" in normalized:
        return ChatLifecycleError(
            code="INSIGHT.CHAT_ASSISTANT_CREATE_UNKNOWN",
            message=(
                "The AI service did not confirm Chat creation. "
                "Temporary resources are being cleaned up before you retry."
            ),
            retryable=True,
        )
    if "REPOSITORY_READER_UPGRADE_REQUIRED" in normalized or (
        "REPOSITORY READER" in normalized and "UPGRADE" in normalized
    ):
        return ChatLifecycleError(
            code="INSIGHT.REPOSITORY_READER_UPGRADE_REQUIRED",
            message=(
                "Upgrade the Repository Reader Agent before browsing or using "
                "this backup in Chat."
            ),
            retryable=False,
        )
    if "REPOSITORY_READER_UNAVAILABLE" in normalized or (
        "REPOSITORY READER" in normalized
        and any(marker in normalized for marker in ("UNAVAILABLE", "OFFLINE"))
    ):
        return ChatLifecycleError(
            code="INSIGHT.REPOSITORY_READER_UNAVAILABLE",
            message=(
                "The Repository Reader is unavailable. Check its Proxy binding "
                "and Agent status, then try again."
            ),
            retryable=True,
        )
    if "REPOSITORY_UNAVAILABLE" in normalized or "SNAPSHOT REPOSITORY" in normalized:
        return ChatLifecycleError(
            code="INSIGHT.REPOSITORY_UNAVAILABLE",
            message="The snapshot repository is unavailable. Check the repository and try again.",
            retryable=True,
        )
    if "SNAPSHOT_BROWSE_TIMEOUT" in normalized or (
        "SNAPSHOT" in normalized
        and "BROWS" in normalized
        and ("TIMEOUT" in normalized or "TIMED OUT" in normalized)
    ):
        return ChatLifecycleError(
            code="INSIGHT.SNAPSHOT_BROWSE_TIMEOUT",
            message="Snapshot browsing timed out. Check the Reader and try again.",
            retryable=True,
        )
    if "GATEWAY" in normalized and any(
        marker in normalized for marker in ("OFFLINE", "UNAVAILABLE", "NOT READY")
    ):
        return ChatLifecycleError(
            code="INSIGHT.DATA_GATEWAY_UNAVAILABLE",
            message=(
                "The selected Data Gateway is unavailable. Bring its Agent and "
                "LensNode online, then try again."
            ),
            retryable=True,
        )
    if (
        "SNAPSHOT_UNAVAILABLE" in normalized
        or "SNAPSHOT_NOT_FOUND" in normalized
        or (
            "SNAPSHOT" in normalized
            and any(
                marker in normalized
                for marker in ("NOT FOUND", "DOES NOT EXIST", "MISSING")
            )
        )
    ):
        return ChatLifecycleError(
            code="INSIGHT.SNAPSHOT_UNAVAILABLE",
            message="The selected snapshot is no longer available. Choose another snapshot.",
            retryable=False,
        )
    return ChatLifecycleError(
        code="INSIGHT.CHAT_PREPARATION_FAILED",
        message="Chat preparation failed. Try again or contact your administrator.",
        retryable=True,
    )
