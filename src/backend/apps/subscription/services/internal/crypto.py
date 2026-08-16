"""Activation code crypto.

Current HFL-ACT codes use an HMAC-style shared secret (compat with existing
issuers). Asymmetric verify is not wired yet — PEM placeholders below are for
the future EE license injection path (runtime public key + offline private key).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any

# Interim shared secret for HFL-ACT HMAC (not a public/private key pair).
LICENSE_SECRET_KEY = "HFL_LICENSE_SECRET_2024_DO_NOT_SHARE"


def load_license_public_key_pem() -> str | None:
    """Placeholder: PEM public key used to verify signed license grants.

    Empty until EE injection ships real key material via
    ``HFL_LICENSE_PUBLIC_KEY_PEM`` (or a file path setting).
    """
    try:
        from django.conf import settings

        pem = (getattr(settings, "HFL_LICENSE_PUBLIC_KEY_PEM", None) or "").strip()
        return pem or None
    except Exception:  # pragma: no cover
        return None


def load_license_private_key_pem() -> str | None:
    """Placeholder: PEM private key for offline license signing only.

    Must never be required at runtime in Community/EE control-plane images.
    Reserved for the closed issuer; loaded only when explicitly configured
    (``HFL_LICENSE_PRIVATE_KEY_PEM``), e.g. local DEV tooling.
    """
    try:
        from django.conf import settings

        pem = (getattr(settings, "HFL_LICENSE_PRIVATE_KEY_PEM", None) or "").strip()
        return pem or None
    except Exception:  # pragma: no cover
        return None


def asymmetric_license_verify_ready() -> bool:
    """True when a runtime public key is configured for future verify path."""
    return bool(load_license_public_key_pem())


def generate_activation_code(
    *,
    license_key: str,
    machine_code: str,
    limits: dict[str, int],
    features: list[str] | None = None,
    validity_days: int = 365,
) -> str:
    """Issue HFL-ACT code (HMAC today; private-key signer TBD)."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=validity_days)
    data = {
        "license_key": license_key,
        "machine_code": machine_code,
        "limits": limits,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    if features is not None:
        data["features"] = list(features)
    data_str = json.dumps(data, sort_keys=True)
    # TODO: when load_license_private_key_pem() is set, sign with asymmetric key.
    data["signature"] = hashlib.sha256((data_str + LICENSE_SECRET_KEY).encode()).hexdigest()
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    return f"HFL-ACT-{encoded}"


def verify_activation_code(activation_code: str) -> dict[str, Any]:
    """Verify HFL-ACT code (HMAC today; public-key verify TBD)."""
    if not activation_code.startswith("HFL-ACT-"):
        raise ValueError("Invalid activation code format")
    encoded = activation_code[8:]
    try:
        data = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid activation code payload") from exc
    if not isinstance(data, dict):
        raise ValueError("Activation code payload must be an object")
    stored_signature = data.pop("signature", None)
    if not isinstance(stored_signature, str) or not stored_signature:
        raise ValueError("Missing signature")
    data_str = json.dumps(data, sort_keys=True)
    # TODO: when load_license_public_key_pem() is set, verify asymmetric signature.
    # Until then HMAC remains the accepted development activation path. EE may
    # independently provide a built-in entitlement when no license is installed.
    expected = hashlib.sha256((data_str + LICENSE_SECRET_KEY).encode()).hexdigest()
    if not hmac.compare_digest(stored_signature, expected):
        raise ValueError("Invalid signature")
    for field in ("license_key", "machine_code", "issued_at"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError(f"Activation code {field} is required")
    if len(data["license_key"]) > 64 or len(data["machine_code"]) > 64:
        raise ValueError("Activation code identity exceeds the supported length")
    if not isinstance(data.get("limits"), dict):
        raise ValueError("License limits must be an object")

    issued_at = _signed_datetime(data["issued_at"], field="issued_at")
    if data.get("expires_at"):
        expires_at = _signed_datetime(data["expires_at"], field="expires_at")
        if expires_at <= issued_at:
            raise ValueError("Activation code expiry must be after issue time")
        if datetime.now(timezone.utc) > expires_at:
            raise ValueError("Activation code expired")
    data["signature"] = stored_signature
    return data


def _signed_datetime(value: object, *, field: str) -> datetime:
    """Parse an unambiguous timezone-aware timestamp from a signed payload."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Activation code {field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Activation code {field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Activation code {field} must include a timezone")
    return parsed
