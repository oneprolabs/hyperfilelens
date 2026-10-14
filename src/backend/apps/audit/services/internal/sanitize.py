"""Sanitize request payloads for audit storage."""

from __future__ import annotations

SENSITIVE_FIELDS = {
    "password",
    "password_confirm",
    "current_password",
    "confirm_password",
    "temporary_password",
    "currentpassword",
    "confirmpassword",
    "temporarypassword",
    "newpassword",
    "oldpassword",
    "new_password",
    "old_password",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "secret",
    "authorization",
    "credential",
    "smtp_password",
}


def sanitize_request_body(data):
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if key.lower() in SENSITIVE_FIELDS:
                out[key] = "******"
            elif isinstance(value, (dict, list)):
                out[key] = sanitize_request_body(value)
            else:
                out[key] = value
        return out
    if isinstance(data, list):
        return [
            sanitize_request_body(item) if isinstance(item, (dict, list)) else item
            for item in data
        ]
    return data
