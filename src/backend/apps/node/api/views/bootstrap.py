"""Serve enrollment bootstrap stubs (deploy/bootstrap → token-filled on download)."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.models import Organization
from apps.node.api import permissions as node_permissions
from apps.node.api.views.bootstrap_templates import (
    BOOTSTRAP_GATEWAY_LINUX,
    BOOTSTRAP_LINUX,
    BOOTSTRAP_MACOS,
    BOOTSTRAP_WINDOWS,
    render_bootstrap_script,
)
from apps.node.api.views.enrollment_helpers import (
    agent_control_plane_ws_url,
    resolve_bootstrap_enrollment_token,
    token_usable_for_bootstrap,
)
from apps.node.models import Node, NodeToken
from common.deploy.site import enrollment_tls_verify, tenant_public_url


def _strict_api_base_valid(api_base: str) -> bool:
    """Return whether strict enrollment targets the configured HTTPS origin."""
    if not enrollment_tls_verify():
        return True
    canonical = tenant_public_url()
    return bool(
        api_base and api_base == canonical and urlsplit(api_base).scheme == "https"
    )


def _bootstrap_error_response(script_type: str, message: str) -> HttpResponse:
    """Return an executable stub so ``curl | bash`` never runs JSON error bodies."""
    fail_msg = message if message.endswith((".", "?", "!")) else f"{message}."
    if script_type in ("windows", "windows_ps1", "ps1"):
        body = (
            "# HyperFileLens enrollment bootstrap error\n"
            "$ts = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')\n"
            f'Write-Host "[$ts] [FAIL ] {fail_msg}" -ForegroundColor Red\n'
            'Write-Host "[$ts] [INFO ] Open the console to generate a new enrollment link." -ForegroundColor Yellow\n'
            "exit 1\n"
        )
        content_type = "text/plain; charset=utf-8"
        filename = BOOTSTRAP_WINDOWS
    else:
        body = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "hfl_now() { date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ; }\n"
            f'echo "[$(hfl_now)] [FAIL ] {fail_msg}" >&2\n'
            'echo "[$(hfl_now)] [INFO ] Open the console to generate a new enrollment link." >&2\n'
            'echo "[$(hfl_now)] [INFO ] If the agent is already installed, check the node in the console." >&2\n'
            "exit 1\n"
        )
        content_type = "text/x-shellscript; charset=utf-8"
        filename = (
            BOOTSTRAP_LINUX if script_type in ("linux", "sh") else BOOTSTRAP_MACOS
        )
    resp = HttpResponse(body, content_type=content_type, status=200)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _parse_enrollment_query(
    request: Request,
    script_type: str,
) -> tuple[str, str, str, str, str] | HttpResponse | Response:
    org_key = str(request.query_params.get("org") or "").strip()
    role = str(request.query_params.get("role") or "agent").strip()
    token = str(
        request.query_params.get("token") or request.query_params.get("id") or "",
    ).strip()
    api_base = str(request.query_params.get("api_base") or "").strip().rstrip("/")

    if not org_key or role not in dict(Node.Role.choices) or not token:
        if script_type in (
            "linux",
            "sh",
            "darwin",
            "macos",
            "windows",
            "windows_ps1",
            "ps1",
        ):
            return _bootstrap_error_response(
                script_type,
                "org, role, and token are required in the enrollment link",
            )
        return Response({"error": "org/role/token required"}, status=400)

    if not _strict_api_base_valid(api_base):
        return _bootstrap_error_response(
            script_type,
            "api_base must match the configured HTTPS tenant origin",
        )

    org = Organization.objects.filter(key=org_key, is_active=True).first()
    if org is None:
        if script_type in (
            "linux",
            "sh",
            "darwin",
            "macos",
            "windows",
            "windows_ps1",
            "ps1",
        ):
            return _bootstrap_error_response(script_type, "organization not found")
        return Response({"error": "organization not found"}, status=404)

    token_row = resolve_bootstrap_enrollment_token(
        org=org,
        token=token,
        role=role,
    )
    if token_row is None:
        if script_type in (
            "linux",
            "sh",
            "darwin",
            "macos",
            "windows",
            "windows_ps1",
            "ps1",
        ):
            return _bootstrap_error_response(
                script_type,
                "invalid or expired enrollment link",
            )
        return Response({"error": "invalid enrollment token"}, status=401)

    platform_by_script_type = {
        "linux": NodeToken.TargetPlatform.LINUX,
        "sh": NodeToken.TargetPlatform.LINUX,
        "darwin": NodeToken.TargetPlatform.MACOS,
        "macos": NodeToken.TargetPlatform.MACOS,
        "windows": NodeToken.TargetPlatform.WINDOWS,
        "windows_ps1": NodeToken.TargetPlatform.WINDOWS,
        "ps1": NodeToken.TargetPlatform.WINDOWS,
    }
    requested_platform = platform_by_script_type.get(script_type)
    if (
        token_row.installation_mode_policy == NodeToken.InstallationModePolicy.AUTO
        and requested_platform != token_row.target_platform
    ):
        return _bootstrap_error_response(
            script_type,
            "This enrollment link was issued for a different operating system",
        )

    if (
        token_row.installation_mode == Node.InstallationMode.USER_CONTINUOUS
        and token_row.installation_mode_policy == NodeToken.InstallationModePolicy.FIXED
        and script_type not in ("linux", "sh")
    ):
        return _bootstrap_error_response(
            script_type,
            "Linux user-continuous protection requires the Linux installer",
        )

    installation_mode = (
        "auto"
        if token_row.installation_mode_policy == NodeToken.InstallationModePolicy.AUTO
        else token_row.installation_mode
    )
    return org_key, role, token, api_base, installation_mode


def _template_values(
    org_key: str,
    role: str,
    token: str,
    api_base: str,
    installation_mode: str = "system",
) -> dict[str, str]:
    insecure_tls = "0" if enrollment_tls_verify() else "1"
    return {
        "HFL_ORG_KEY": org_key,
        "HFL_NODE_ROLE": role,
        "HFL_NODE_TOKEN": token,
        "HFL_INSTALLATION_MODE": installation_mode,
        "HFL_API_BASE": api_base,
        "HFL_WSS_URL": agent_control_plane_ws_url(api_base),
        "HFL_INSECURE_TLS": insecure_tls,
    }


def serve_enrollment_bootstrap(
    request: Request, script_type: str
) -> HttpResponse | Response:
    parsed = _parse_enrollment_query(request, script_type)
    if not isinstance(parsed, tuple):
        return parsed
    org_key, role, token, api_base, installation_mode = parsed
    values = _template_values(
        org_key,
        role,
        token,
        api_base,
        installation_mode,
    )

    try:
        if script_type in ("linux", "sh"):
            body = render_bootstrap_script(BOOTSTRAP_LINUX, values)
            resp = HttpResponse(body, content_type="text/x-shellscript; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="{BOOTSTRAP_LINUX}"'
            return resp

        if script_type in ("darwin", "macos"):
            body = render_bootstrap_script(BOOTSTRAP_MACOS, values)
            resp = HttpResponse(body, content_type="text/x-shellscript; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="{BOOTSTRAP_MACOS}"'
            return resp

        if script_type in ("windows", "windows_ps1", "ps1"):
            body = render_bootstrap_script(BOOTSTRAP_WINDOWS, values)
            resp = HttpResponse(body, content_type="text/plain; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="{BOOTSTRAP_WINDOWS}"'
            return resp
    except FileNotFoundError as exc:
        return Response({"error": str(exc)}, status=500)

    return Response(
        {"error": "unsupported type (use linux, macos, or windows)"}, status=400
    )


def _parse_gateway_bootstrap_query(
    request: Request,
) -> tuple[str, str, str] | HttpResponse | Response:
    org_key = str(request.query_params.get("org") or "").strip()
    token = str(
        request.query_params.get("token") or request.query_params.get("id") or "",
    ).strip()
    api_base = str(request.query_params.get("api_base") or "").strip().rstrip("/")
    role = Node.Role.GATEWAY

    if not org_key or not token:
        return _bootstrap_error_response(
            "linux",
            "org and token are required in the gateway enrollment link",
        )

    if not _strict_api_base_valid(api_base):
        return _bootstrap_error_response(
            "linux",
            "api_base must match the configured HTTPS tenant origin",
        )

    org = Organization.objects.filter(key=org_key, is_active=True).first()
    if org is None:
        return _bootstrap_error_response("linux", "organization not found")

    if not token_usable_for_bootstrap(org=org, token=token, role=role):
        return _bootstrap_error_response(
            "linux",
            "invalid or expired enrollment link",
        )

    return org_key, token, api_base


def serve_gateway_bootstrap(request: Request) -> HttpResponse | Response:
    parsed = _parse_gateway_bootstrap_query(request)
    if not isinstance(parsed, tuple):
        return parsed
    org_key, token, api_base = parsed
    values = _template_values(org_key, Node.Role.GATEWAY, token, api_base)

    try:
        body = render_bootstrap_script(BOOTSTRAP_GATEWAY_LINUX, values)
    except FileNotFoundError as exc:
        return Response({"error": str(exc)}, status=500)

    resp = HttpResponse(body, content_type="text/x-shellscript; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{BOOTSTRAP_GATEWAY_LINUX}"'
    return resp


class BootstrapGatewayView(APIView):
    """
    Download Data Gateway bootstrap stub (curl pipe / save and run).

    Query: org, token (or id), api_base
    """

    permission_classes = [node_permissions.AllowAny]

    def get(self, request):
        return serve_gateway_bootstrap(request)


class BootstrapView(APIView):
    """
    Download enrollment bootstrap stub (curl pipe / save and run).

    Query: type=linux|macos|windows, org, role, token (or id), api_base
    """

    permission_classes = [node_permissions.AllowAny]

    def get(self, request):
        script_type = str(request.query_params.get("type") or "").strip().lower()
        return serve_enrollment_bootstrap(request, script_type)
