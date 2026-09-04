#!/usr/bin/env python3
"""Apply deployment-specific public URL and runtime feature settings to HFL .env."""

import argparse
import os
import pathlib
import re
import stat
import tempfile
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import SplitResult, urlsplit, urlunsplit


RUNTIME_KEYS = {
    "HFL_EMAIL_SIGNUP_ENABLED",
    "HFL_EMAIL_CODE_LOGIN_ENABLED",
    "HFL_GOOGLE_OAUTH_ENABLED",
    "HFL_GA_MEASUREMENT_ID",
    "HFL_INSECURE_TLS",
    "HFL_PLATFORM_GATEWAY_AUTO_DEPLOY",
    "HFL_DEPLOY_TARGET",
    "SENTRY_ENABLED",
    "SENTRY_BACKEND_DSN",
    "SENTRY_FRONTEND_DSN",
    "SENTRY_ENVIRONMENT",
    "SENTRY_TRACES_SAMPLE_RATE",
    "TURNSTILE_ENABLED",
    "TURNSTILE_SITE_KEY",
    "TURNSTILE_SECRET_KEY",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_SECURITY",
    "EMAIL_FROM",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
}
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
GOOGLE_CLIENT_ID_PATTERN = re.compile(
    r"^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$"
)
GA4_MEASUREMENT_ID_PATTERN = re.compile(r"^G-[A-Z0-9]+$")
SENTRY_ENVIRONMENT_PATTERN = re.compile(
    r"^hfl-(test|community|preprod|production)$"
)


def warn(message: str) -> None:
    print(f"[runtime-config] WARNING: {message}")


def info(message: str) -> None:
    print(f"[runtime-config] INFO: {message}")


def require_regular_file(path: pathlib.Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise SystemExit(f"{label} does not exist: {path}") from exc
    if not stat.S_ISREG(mode) or path.is_symlink():
        raise SystemExit(f"{label} must be a regular file, not a symlink: {path}")


def parse_public_origin(value: str) -> Optional[SplitResult]:
    try:
        candidate = urlsplit(value)
        candidate.port
    except ValueError:
        return None
    if (
        candidate.scheme not in {"http", "https"}
        or not candidate.hostname
        or candidate.username
        or candidate.password
        or candidate.query
        or candidate.fragment
        or candidate.path not in {"", "/"}
        or re.search(r"\s", candidate.netloc)
    ):
        return None
    return candidate


def host_for_url(value: str) -> str:
    if ":" in value and not value.startswith("["):
        return f"[{value}]"
    return value


def comma_values(value: str, exclude_wildcard: bool = False) -> List[str]:
    items = []
    for item in value.split(","):
        item = item.strip()
        if item and not (exclude_wildcard and item == "*") and item not in items:
            items.append(item)
    return items


def append_unique(items: List[str], *values: str) -> None:
    for value in values:
        if value and value not in items:
            items.append(value)


def read_runtime_values(path: Optional[pathlib.Path]) -> Dict[str, str]:
    if path is None:
        return {}
    require_regular_file(path, "runtime environment file")
    os.chmod(str(path), 0o600)
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if not separator or key not in RUNTIME_KEYS or re.search(r"[\r\n]", value):
            warn(f"ignoring invalid staged runtime key {key!r}")
            continue
        values[key] = value
    return values


def smtp_runtime_updates(values: Dict[str, str]) -> Dict[str, str]:
    """Validate staged SMTP inputs and return Docker Compose environment updates."""
    names = (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_SECURITY",
        "EMAIL_FROM",
    )
    present = [name for name in names if values.get(name, "") != ""]
    if not present:
        warn("SMTP deployment configuration is empty; preserving installed email settings")
        return {}
    missing = [name for name in names if values.get(name, "") == ""]
    if missing:
        warn(
            "partial SMTP deployment configuration; preserving installed email "
            "settings (missing: " + ", ".join(missing) + ")"
        )
        return {}

    host = values["SMTP_HOST"].strip()
    username = values["SMTP_USERNAME"].strip()
    password = values["SMTP_PASSWORD"]
    security = values["SMTP_SECURITY"].strip().lower()
    from_email = values["EMAIL_FROM"].strip()
    if not host or re.search(r"\s", host):
        warn("invalid SMTP host; preserving installed email settings")
        return {}
    if not username or re.search(r"[\r\n]", username):
        warn("invalid SMTP username; preserving installed email settings")
        return {}
    if not password or re.search(r"[\x00\r\n]", password):
        warn("invalid SMTP password; preserving installed email settings")
        return {}
    if not from_email or re.search(r"[\r\n]", from_email):
        warn("invalid sender identity; preserving installed email settings")
        return {}
    try:
        port = int(values["SMTP_PORT"])
    except ValueError:
        warn("invalid SMTP port; preserving installed email settings")
        return {}
    if port < 1 or port > 65535:
        warn("SMTP port is outside 1-65535; preserving installed email settings")
        return {}
    if security not in {"ssl", "starttls", "none"}:
        warn("invalid SMTP security mode; preserving installed email settings")
        return {}

    return {
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_HOST": host,
        "EMAIL_PORT": str(port),
        "EMAIL_HOST_USER": username,
        "EMAIL_HOST_PASSWORD": password,
        "EMAIL_USE_TLS": "true" if security == "starttls" else "false",
        "EMAIL_USE_SSL": "true" if security == "ssl" else "false",
        "DEFAULT_FROM_EMAIL": from_email,
    }


def google_runtime_updates(values: Dict[str, str]) -> Dict[str, str]:
    """Apply Google OAuth as one optional group, preserving installed values on errors."""
    enabled = values.get("HFL_GOOGLE_OAUTH_ENABLED", "").strip().lower()
    client_id = values.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = values.get("GOOGLE_CLIENT_SECRET", "")
    if enabled not in {"true", "false"}:
        warn("invalid Google OAuth enabled value; preserving installed Google settings")
        return {}
    if enabled == "false":
        return {"HFL_GOOGLE_OAUTH_ENABLED": "false"}
    if not GOOGLE_CLIENT_ID_PATTERN.fullmatch(client_id):
        warn("invalid Google OAuth client ID; preserving installed Google settings")
        return {}
    if not client_secret or re.search(r"[\x00\r\n]", client_secret):
        warn("invalid Google OAuth client secret; preserving installed Google settings")
        return {}
    return {
        "HFL_GOOGLE_OAUTH_ENABLED": "true",
        "GOOGLE_CLIENT_ID": client_id,
        "GOOGLE_CLIENT_SECRET": client_secret,
    }


def analytics_runtime_update(values: Dict[str, str]) -> Tuple[bool, str]:
    """Return whether SaaS analytics was staged and its validated ID."""
    if "HFL_GA_MEASUREMENT_ID" not in values:
        return False, ""
    measurement_id = values.get("HFL_GA_MEASUREMENT_ID", "").strip()
    if not measurement_id:
        return True, ""
    if not GA4_MEASUREMENT_ID_PATTERN.fullmatch(measurement_id):
        warn("invalid GA4 measurement ID; analytics is disabled")
        return True, ""
    return True, measurement_id


def valid_sentry_dsn(value: str) -> bool:
    """Return whether a staged value is a structurally valid Sentry DSN."""
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    project_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username
        and not parsed.query
        and not parsed.fragment
        and project_id.isdigit()
        and not re.search(r"[\x00\r\n\s]", value)
    )


def valid_frontend_sentry_dsn(value: str) -> bool:
    """Return whether a DSN is safe to expose in browser runtime config."""
    if not valid_sentry_dsn(value):
        return False
    return urlsplit(value).password is None


def sentry_runtime_updates(
    values: Dict[str, str],
) -> Tuple[Dict[str, str], Set[str]]:
    """Validate managed Sentry settings without blocking deployment."""
    enabled = values.get("SENTRY_ENABLED", "").strip().lower()
    if enabled not in {"true", "false"}:
        warn("invalid Sentry enabled value; preserving installed Sentry settings")
        return {}, set()

    updates = {"SENTRY_ENABLED": enabled}
    removals: Set[str] = set()
    if enabled == "false":
        removals.update({"SENTRY_BACKEND_DSN", "SENTRY_FRONTEND_DSN"})
        return updates, removals

    environment = values.get("SENTRY_ENVIRONMENT", "").strip()
    if not SENTRY_ENVIRONMENT_PATTERN.fullmatch(environment):
        warn("invalid Sentry environment; Sentry is disabled")
        updates["SENTRY_ENABLED"] = "false"
        removals.update({"SENTRY_BACKEND_DSN", "SENTRY_FRONTEND_DSN"})
        return updates, removals
    updates["SENTRY_ENVIRONMENT"] = environment

    raw_rate = values.get("SENTRY_TRACES_SAMPLE_RATE", "").strip()
    try:
        rate = float(raw_rate)
    except ValueError:
        rate = -1.0
    if not 0.0 <= rate <= 1.0:
        warn("invalid Sentry trace sample rate; using 0")
        rate = 0.0
    updates["SENTRY_TRACES_SAMPLE_RATE"] = f"{rate:g}"

    valid_dsn_count = 0
    for name in ("SENTRY_BACKEND_DSN", "SENTRY_FRONTEND_DSN"):
        dsn = values.get(name, "").strip()
        validator = valid_frontend_sentry_dsn if name == "SENTRY_FRONTEND_DSN" else valid_sentry_dsn
        if validator(dsn):
            updates[name] = dsn
            valid_dsn_count += 1
        else:
            removals.add(name)
            warn(f"invalid or missing {name}; that Sentry surface is disabled")
    if valid_dsn_count == 0:
        updates["SENTRY_ENABLED"] = "false"
    return updates, removals


def turnstile_runtime_updates(values: Dict[str, str]) -> Dict[str, str]:
    """Apply Turnstile atomically without replacing usable installed credentials."""
    enabled = values.get("TURNSTILE_ENABLED", "").strip().lower()
    site_key = values.get("TURNSTILE_SITE_KEY", "").strip()
    secret_key = values.get("TURNSTILE_SECRET_KEY", "")
    if enabled not in {"true", "false"}:
        warn("invalid Turnstile enabled value; preserving installed settings")
        return {}

    credentials_present = bool(site_key or secret_key)
    credentials_valid = bool(
        KEY_PATTERN.fullmatch(site_key)
        and secret_key
        and KEY_PATTERN.fullmatch(secret_key)
    )
    if enabled == "false" and not credentials_present:
        return {"TURNSTILE_ENABLED": "false"}
    if not credentials_valid:
        warn("incomplete or malformed Turnstile credentials; preserving installed settings")
        return (
            {"TURNSTILE_ENABLED": "false"}
            if enabled == "false"
            else {}
        )
    return {
        "TURNSTILE_ENABLED": enabled,
        "TURNSTILE_SITE_KEY": site_key,
        "TURNSTILE_SECRET_KEY": secret_key,
    }


def compose_env_value(value: str) -> str:
    """Quote values that must remain literal through Docker Compose interpolation."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def atomic_write(path: pathlib.Path, lines: List[str]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = pathlib.Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        os.replace(str(temporary), str(path))
        os.chmod(str(path), 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_configuration(
    env_path: pathlib.Path,
    direct_host: str,
    public_url: str,
    admin_public_url: str,
    runtime_path: Optional[pathlib.Path],
) -> None:
    require_regular_file(env_path, "environment file")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    current = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator:
            current[key] = value

    updates: Dict[str, str] = {}
    removals: Set[str] = set()
    runtime_values = read_runtime_values(runtime_path)
    if runtime_path is not None:
        signup_enabled = runtime_values.get("HFL_EMAIL_SIGNUP_ENABLED", "").lower()
        if signup_enabled in {"true", "false"}:
            updates["HFL_EMAIL_SIGNUP_ENABLED"] = signup_enabled
        else:
            warn("invalid email sign-up value; preserving installed sign-up setting")
        email_code_login_enabled = runtime_values.get(
            "HFL_EMAIL_CODE_LOGIN_ENABLED", ""
        ).lower()
        if email_code_login_enabled in {"true", "false"}:
            updates["HFL_EMAIL_CODE_LOGIN_ENABLED"] = email_code_login_enabled
        else:
            warn(
                "invalid email-code login value; preserving installed email-code login setting"
            )
        insecure_tls = runtime_values.get("HFL_INSECURE_TLS", "")
        if insecure_tls not in {"0", "1"}:
            raise SystemExit("HFL_INSECURE_TLS must be 0 or 1")
        updates["HFL_INSECURE_TLS"] = insecure_tls
        updates.update(smtp_runtime_updates(runtime_values))
        updates.update(google_runtime_updates(runtime_values))
        sentry_updates, sentry_removals = sentry_runtime_updates(runtime_values)
        updates.update(sentry_updates)
        removals.update(sentry_removals)
        removals.update(
            {
                "SENTRY_DSN",
                "SENTRY_RELEASE",
                "SENTRY_PROFILES_SAMPLE_RATE",
                "SENTRY_SEND_DEFAULT_PII",
            }
        )
        analytics_staged, measurement_id = analytics_runtime_update(runtime_values)
        if analytics_staged:
            if measurement_id:
                updates["HFL_GA_MEASUREMENT_ID"] = measurement_id
            else:
                removals.add("HFL_GA_MEASUREMENT_ID")

        gateway_enabled = runtime_values.get(
            "HFL_PLATFORM_GATEWAY_AUTO_DEPLOY", ""
        ).lower()
        if gateway_enabled in {"true", "false"}:
            updates["HFL_PLATFORM_GATEWAY_AUTO_DEPLOY"] = gateway_enabled
        else:
            warn(
                "invalid platform Gateway auto-deploy value; preserving installed value"
            )

        updates.update(turnstile_runtime_updates(runtime_values))
        deploy_target = runtime_values.get("HFL_DEPLOY_TARGET", "").strip()
        if deploy_target in {"test", "community", "preprod", "prod"}:
            updates["HFL_DEPLOY_TARGET"] = deploy_target
            updates["HFL_DEPLOYMENT_MODE"] = "managed"
        else:
            warn("invalid deployment target; preserving installed deployment identity")

    direct_host = direct_host.strip()
    if direct_host and (
        re.search(r"\s", direct_host) or re.search(r"[/@?#]", direct_host)
    ):
        warn("invalid direct host; preserving installed direct-host configuration")
        direct_host = ""
    direct_allowed_host = direct_host.strip("[]")
    direct_url_host = host_for_url(direct_host)
    allowed_hosts = comma_values(
        current.get("DJANGO_ALLOWED_HOSTS", ""), exclude_wildcard=True
    )
    csrf_origins = comma_values(current.get("CSRF_TRUSTED_ORIGINS", ""))
    cors_origins = comma_values(current.get("CORS_ALLOWED_ORIGINS", ""))
    append_unique(allowed_hosts, "localhost", "127.0.0.1", direct_allowed_host)
    append_unique(
        csrf_origins,
        "https://localhost:11443",
        "https://127.0.0.1:11443",
        f"https://{direct_url_host}:11443" if direct_url_host else "",
    )
    append_unique(
        cors_origins,
        "https://localhost:11443",
        "https://127.0.0.1:11443",
        f"https://{direct_url_host}:11443" if direct_url_host else "",
    )
    public_url = public_url.strip()
    if public_url:
        parsed = parse_public_origin(public_url)
        if parsed:
            public_origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            append_unique(allowed_hosts, parsed.hostname or "")
            append_unique(csrf_origins, public_origin)
            append_unique(cors_origins, public_origin)
            updates.update(
                {
                    "FRONTEND_URL": public_origin,
                    "LENS_GATEWAY_BASE_URL": f"{public_origin}/sourcelens",
                }
            )
        else:
            warn(
                f"invalid public URL {public_url!r}; preserving installed URL configuration"
            )
    else:
        if os.environ.get("HFL_ONLINE_CHILD") == "1":
            info(
                "public URL was not specified; existing URL configuration remains unchanged"
            )
        else:
            warn("public URL is empty; preserving installed URL configuration")

    admin_public_url = admin_public_url.strip()
    if admin_public_url:
        parsed = parse_public_origin(admin_public_url)
        if parsed:
            admin_origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            append_unique(allowed_hosts, parsed.hostname or "")
            append_unique(csrf_origins, admin_origin)
            append_unique(cors_origins, admin_origin)
            updates["HFL_ADMIN_PUBLIC_URL"] = admin_origin
        else:
            warn(
                f"invalid Admin Console public URL {admin_public_url!r}; "
                "preserving installed Admin Console URL"
            )

    updates["DJANGO_ALLOWED_HOSTS"] = ",".join(allowed_hosts)
    updates["CSRF_TRUSTED_ORIGINS"] = ",".join(csrf_origins)
    updates["CORS_ALLOWED_ORIGINS"] = ",".join(cors_origins)

    updated = []
    seen = set()
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in removals:
            continue
        if key in updates:
            value = updates[key]
            if key in {
                "EMAIL_HOST_PASSWORD",
                "GOOGLE_CLIENT_SECRET",
                "SENTRY_BACKEND_DSN",
                "SENTRY_FRONTEND_DSN",
            }:
                value = compose_env_value(value)
            updated.append(f"{key}={value}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in updates.items():
        if key not in seen:
            if key in {
                "EMAIL_HOST_PASSWORD",
                "GOOGLE_CLIENT_SECRET",
                "SENTRY_BACKEND_DSN",
                "SENTRY_FRONTEND_DSN",
            }:
                value = compose_env_value(value)
            updated.append(f"{key}={value}")
    atomic_write(env_path, updated)
    print("[runtime-config] Applied deployment configuration before service startup")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=pathlib.Path)
    parser.add_argument("--direct-host", default="")
    parser.add_argument("--public-url", default="")
    parser.add_argument("--admin-public-url", default="")
    parser.add_argument("--runtime-env-file", type=pathlib.Path)
    arguments = parser.parse_args()
    apply_configuration(
        arguments.env_file,
        arguments.direct_host,
        arguments.public_url,
        arguments.admin_public_url,
        arguments.runtime_env_file,
    )


if __name__ == "__main__":
    main()
