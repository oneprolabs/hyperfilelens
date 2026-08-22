"""Map internal Chat provisioning failures to safe product-facing errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.errors import AppError


_QUOTA_CODE = "SUBSCRIPTION.QUOTA_EXCEEDED"
_QUOTA_USAGE_UNAVAILABLE_CODE = "SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE"
_GATEWAY_CAPACITY_META = {
    "quota_type": "gateway.public_capacity_bytes",
    "scope": "gateway",
}
_SAFE_QUOTA_META_KEYS = {
    "quota_type",
    "scope",
    "limit",
    "used",
    "requested",
    "unknown_size",
}
_PRODUCT_VISIBLE_ORGANIZATION_QUOTAS = {
    "gateway_select_max_files",
    "gateway_select_max_bytes",
    "max_public_gateway_capacity_bytes",
}


@dataclass(frozen=True)
class ChatLifecycleError:
    code: str
    message: str
    retryable: bool
    meta: dict[str, Any]

    def as_state(self) -> dict[str, Any]:
        """Return the durable, tenant-safe representation."""

        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "meta": self.meta,
        }


def _safe_quota_meta(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe = {key: value[key] for key in _SAFE_QUOTA_META_KEYS if key in value}
    organization_meter_is_visible = (
        str(safe.get("scope") or "organization").lower() == "organization"
        and str(safe.get("quota_type") or "")
        in _PRODUCT_VISIBLE_ORGANIZATION_QUOTAS
    )
    if not organization_meter_is_visible:
        for key in ("limit", "used", "requested", "unknown_size"):
            safe.pop(key, None)
    return safe


def _quota_error(meta: Any = None) -> ChatLifecycleError:
    safe_meta = _safe_quota_meta(meta)
    scope = str(safe_meta.get("scope") or "organization").lower()
    if scope == "gateway":
        message = (
            "The shared Data Gateway currently has insufficient capacity. "
            "Try again later or contact your platform administrator."
        )
    elif scope == "instance":
        message = (
            "Shared instance capacity is full. Contact your platform administrator "
            "to raise the deployment grant or free capacity."
        )
    else:
        message = (
            "A quota required for this Chat is full. Contact your platform "
            "administrator to raise the limit."
        )
    return ChatLifecycleError(
        code=_QUOTA_CODE,
        message=message,
        retryable=True,
        meta=safe_meta,
    )


def _quota_usage_unavailable(meta: Any = None) -> ChatLifecycleError:
    safe_meta = _safe_quota_meta(meta)
    scope = str(safe_meta.get("scope") or "organization").lower()
    message = (
        "Organization capacity is temporarily unavailable. Try again shortly."
        if scope == "organization"
        else (
            "Shared capacity is temporarily unavailable. Try again later or "
            "contact your platform administrator."
        )
    )
    return ChatLifecycleError(
        code=_QUOTA_USAGE_UNAVAILABLE_CODE,
        message=message,
        retryable=True,
        meta=safe_meta,
    )


def lifecycle_error_state_from_exception(exc: Exception) -> dict[str, Any]:
    """Capture one safe structured error before async cleanup changes context."""

    if isinstance(exc, AppError):
        if exc.code == _QUOTA_CODE:
            return _quota_error(exc.meta).as_state()
        if exc.code == _QUOTA_USAGE_UNAVAILABLE_CODE:
            return _quota_usage_unavailable(exc.meta).as_state()
    return classify_chat_lifecycle_error(str(exc)).as_state()


def classify_chat_lifecycle_error(
    raw_error: str | None,
    state: Any = None,
) -> ChatLifecycleError:
    """Return a stable, tenant-safe error without exposing upstream details."""

    raw = str(raw_error or "").strip()
    normalized = raw.upper()
    structured = state if isinstance(state, dict) else {}
    if structured.get("code") == _QUOTA_CODE:
        return _quota_error(structured.get("meta"))
    if structured.get("code") == _QUOTA_USAGE_UNAVAILABLE_CODE:
        return _quota_usage_unavailable(structured.get("meta"))
    if (
        "PUBLIC DATA GATEWAY CAPACITY IS FULL" in normalized
        or "PUBLIC DATA GATEWAY IS AT CAPACITY" in normalized
    ):
        # Compatibility for failures persisted before structured lifecycle errors.
        return _quota_error(_GATEWAY_CAPACITY_META)
    if "MODEL_NOT_VISION_CAPABLE" in normalized:
        return ChatLifecycleError(
            code="INSIGHT.CHAT_MODEL_NOT_VISION_CAPABLE",
            message=(
                "The configured AI model is not compatible with this Chat. "
                "Ask an administrator to check the multimodal model, then try again."
            ),
            retryable=False,
            meta={},
        )
    if (
        "INVALID INSIGHT SCOPE SUMMARY" in normalized
        or "INVALID INSIGHT SCOPE TYPE" in normalized
    ):
        return ChatLifecycleError(
            code="INSIGHT.SCOPE_SUMMARY_INVALID",
            message=(
                "The Repository Reader returned an invalid selected-data summary. "
                "Upgrade the Reader and select the file or folder again."
            ),
            retryable=False,
            meta={},
        )
    if "REMOTE CREATE OUTCOME IS UNKNOWN" in normalized:
        return ChatLifecycleError(
            code="INSIGHT.CHAT_ASSISTANT_CREATE_UNKNOWN",
            message=(
                "The AI service did not confirm Chat creation. "
                "Temporary resources are being cleaned up before you retry."
            ),
            retryable=True,
            meta={},
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
            meta={},
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
            meta={},
        )
    if "REPOSITORY_UNAVAILABLE" in normalized or "SNAPSHOT REPOSITORY" in normalized:
        return ChatLifecycleError(
            code="INSIGHT.REPOSITORY_UNAVAILABLE",
            message="The snapshot repository is unavailable. Check the repository and try again.",
            retryable=True,
            meta={},
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
            meta={},
        )
    if "SNAPSHOT_PATH_NOT_FOUND" in normalized or (
        "SELECTED FILE OR FOLDER" in normalized
        and "NO LONGER AVAILABLE" in normalized
        and "SNAPSHOT" in normalized
    ):
        return ChatLifecycleError(
            code="INSIGHT.SNAPSHOT_PATH_NOT_FOUND",
            message=(
                "The selected file or folder is no longer available in this "
                "snapshot. Select it again or choose another path."
            ),
            retryable=False,
            meta={},
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
            meta={},
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
            meta={},
        )
    return ChatLifecycleError(
        code="INSIGHT.CHAT_PREPARATION_FAILED",
        message="Chat preparation failed. Try again or contact your administrator.",
        retryable=True,
        meta={},
    )
