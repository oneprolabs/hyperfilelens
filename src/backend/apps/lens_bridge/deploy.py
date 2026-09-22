"""Resolve SourceLens bridge credentials and deployment URLs."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from project.settings.env import env_int, env_str

# Public HTTPS path used by bundled SourceLens Data Gateways.
LENS_GATEWAY_PUBLIC_PATH = "/sourcelens"


def lens_base_url() -> str:
    return env_str("LENS_BASE_URL", "").rstrip("/")


def sourcelens_mode() -> str:
    mode = env_str("SOURCELENS_MODE", "bundled").strip().lower()
    return mode if mode in {"bundled", "external"} else "bundled"


def sourcelens_console_url() -> str:
    """Return the browser-facing SourceLens administration console URL."""
    explicit = env_str("SOURCELENS_CONSOLE_URL", "").rstrip("/")
    if explicit:
        return explicit

    if sourcelens_mode() == "external":
        return lens_base_url()

    frontend = env_str("FRONTEND_URL", "").rstrip("/")
    parsed = urlsplit(frontend)
    if not parsed.scheme or not parsed.hostname:
        return ""

    hostname = parsed.hostname
    if ":" in hostname:
        hostname = f"[{hostname}]"
    port = env_int("SOURCELENS_CONSOLE_PORT", 11445)
    if port < 1 or port > 65535:
        port = 11445
    return urlunsplit((parsed.scheme, f"{hostname}:{port}", "", "", ""))


def sourcelens_version() -> str:
    """Return the deployment-declared SourceLens release, when available."""
    explicit = env_str("SOURCELENS_VERSION", "")
    if explicit:
        return explicit
    if sourcelens_mode() == "bundled":
        return env_str("SOURCELENS_GIT_REF", "")
    return ""


def lens_gateway_base_url() -> str:
    """SourceLens URL reachable from enrolled gateway hosts (native OS, not Docker DNS)."""
    use_deployment_origin_fallback = True
    if sourcelens_mode() == "bundled":
        from apps.configuration.constants import NOT_FOUND
        from apps.configuration.selectors.interface import has_runtime_config
        from apps.configuration.selectors.internal.resolver import resolve_global_value
        from apps.instance_settings.conf import CONFIG_KEY_EXTERNAL_ACCESS_URL
        from apps.instance_settings.services.external_access import (
            configured_external_access_url,
        )

        # Match effective_external_access_url(): valid Runtime wins; explicit empty
        # blocks Deployment; policy-rejected non-empty values still fall through.
        # Dedicated LENS_GATEWAY_BASE_URL still outranks Deployment below.
        if has_runtime_config(CONFIG_KEY_EXTERNAL_ACCESS_URL):
            configured = configured_external_access_url()
            if configured:
                return f"{configured}{LENS_GATEWAY_PUBLIC_PATH}"
            raw = resolve_global_value(config_key=CONFIG_KEY_EXTERNAL_ACCESS_URL)
            raw_text = raw if isinstance(raw, str) else ""
            if raw is not NOT_FOUND and not raw_text.strip():
                use_deployment_origin_fallback = False

    explicit = env_str("LENS_GATEWAY_BASE_URL", "").rstrip("/")
    if explicit:
        return explicit

    base = lens_base_url()
    if sourcelens_mode() == "external":
        return base

    if use_deployment_origin_fallback:
        from apps.instance_settings.services.external_access import (
            deployment_external_access_url,
        )

        # HFL_EXTERNAL_ACCESS_URL → FRONTEND_URL (same Deployment chain as Console).
        origin = deployment_external_access_url().rstrip("/")
        if origin:
            return f"{origin}{LENS_GATEWAY_PUBLIC_PATH}"

    if not base:
        return ""
    # host.docker.internal resolves inside Docker only; gateway install runs on the host OS.
    if "host.docker.internal" in base:
        return base.replace("host.docker.internal", "127.0.0.1")
    return base


def lens_gateway_base_path() -> str:
    """Return the control-plane-relative SourceLens path for bundled gateways.

    New Agents resolve this path against the ``HFL_API_BASE`` they already used
    to enroll.  The absolute URL remains in the response for compatibility with
    older Agents and for external SourceLens deployments.
    """
    if sourcelens_mode() == "bundled":
        return LENS_GATEWAY_PUBLIC_PATH
    return ""


def local_platform_lens_gateway_base_url() -> str:
    """Return the bundled SourceLens URL for the installer-managed local Gateway."""
    if sourcelens_mode() != "bundled":
        return lens_gateway_base_url()
    port = env_int("HFL_TENANT_PORT", 11443)
    if port < 1 or port > 65535:
        port = 11443
    return f"https://127.0.0.1:{port}{LENS_GATEWAY_PUBLIC_PATH}"


def lens_bridge_email() -> str:
    """SL service account login email."""
    return env_str("LENS_BRIDGE_EMAIL", "")


def lens_bridge_legacy_username() -> str:
    """Legacy SL login used only while upgrading pre-email releases."""
    return env_str("LENS_BRIDGE_USERNAME", "")


def lens_bridge_password() -> str:
    return env_str("LENS_BRIDGE_PASSWORD", "")


def lens_bridge_configured() -> bool:
    return bool(lens_base_url() and lens_bridge_email() and lens_bridge_password())
