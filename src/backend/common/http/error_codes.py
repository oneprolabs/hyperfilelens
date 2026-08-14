"""
Internal API error codes (framework-level).

`code` remains HTTP status for transport compatibility, while `error_code` is
stable for client logic and analytics.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_noop


# Success
SUCCESS = "SUCCESS"
UNKNOWN = "UNKNOWN"

# App framework
AUTH_UNAUTHORIZED = "AUTH_401_UNAUTHORIZED"
AUTH_FORBIDDEN = "AUTH_403_FORBIDDEN"

# Token error codes (specific reasons for 401)
TOKEN_EXPIRED = "TOKEN_EXPIRED"
REFRESH_EXPIRED = "REFRESH_EXPIRED"
TOKEN_BLACKLISTED = "TOKEN_BLACKLISTED"
OTHER_DEVICE_LOGIN = "OTHER_DEVICE_LOGIN"
PASSWORD_CHANGED = "PASSWORD_CHANGED"
ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
TOKEN_REUSED = "TOKEN_REUSED"
INVALID_TOKEN = "INVALID_TOKEN"
VAL_VALIDATION = "VAL_400_VALIDATION"
SRV_INTERNAL = "SRV_500_INTERNAL"
NOT_FOUND = "API_404_NOT_FOUND"

# i18n messages (kept in django.po; keys used by exception handler)
I18N_UNAUTHORIZED = gettext_noop("Unauthorized")
I18N_FORBIDDEN = gettext_noop("Forbidden")
I18N_VALIDATION = gettext_noop("Validation failed")
I18N_NOT_FOUND = gettext_noop("Not found")
I18N_SERVER_ERROR = gettext_noop("Server error")
I18N_NOT_ACCEPTABLE = gettext_noop("Not acceptable")
I18N_ERROR = gettext_noop("Error")


def map_http_status_to_error_code(status_code: int) -> str:
    if status_code == 400:
        return VAL_VALIDATION
    if status_code == 401:
        return AUTH_UNAUTHORIZED
    if status_code == 403:
        return AUTH_FORBIDDEN
    if status_code == 404:
        return NOT_FOUND
    if status_code == 406:
        return "API_406_NOT_ACCEPTABLE"
    if 500 <= status_code <= 599:
        return SRV_INTERNAL
    return UNKNOWN


def ensure_request_id_in_data(data: Any, request_id: str) -> Any:
    if not request_id:
        return data
    if isinstance(data, dict):
        if "request_id" in data or "requestId" in data:
            return data
        out = dict(data)
        out["request_id"] = request_id
        return out
    return {"value": data, "request_id": request_id}
