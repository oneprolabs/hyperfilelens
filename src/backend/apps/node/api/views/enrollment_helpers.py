"""Shared helpers for enrollment install and artifact download flows."""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

from apps.iam.models import Organization
from apps.node.models import NodeToken
from apps.node.services.internal.enrollment_auth import (
    ArtifactDownloadAuthorization,
    active_node_credential,
    resolve_enrollment_authorization,
)


def enrollment_health(_request):
    from django.http import JsonResponse

    return JsonResponse({"app": "enrollment", "status": "ok"})


def agent_control_plane_ws_url(api_base: str) -> str:
    """Derive ws/wss URL for Agent WSS from HTTP API base."""
    base = (api_base or "").strip().rstrip("/")
    if not base:
        return ""
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        return ""
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws/node/agent/"


def get_valid_enrollment_token(
    *,
    org: Organization,
    token: str,
    role: str,
) -> NodeToken | None:
    """Return the token authorizing an enrollment token or active session."""
    authorization = resolve_enrollment_authorization(
        org=org,
        secret=token,
        role=role,
    )
    return authorization.token if authorization is not None else None


def token_usable_for_artifact_download(
    *,
    org: Organization,
    token: str,
    role: str,
) -> bool:
    """
    True when token may download signed agent artifacts.

    Active tokens are always allowed. Legacy one-time tokens that were deactivated
    after first use remain downloadable so existing install links can finish.
    Registered nodes may also download with their long-lived NodeCredential.
    """
    return (
        resolve_artifact_download_authorization(
            org=org,
            token=token,
            role=role,
        )
        is not None
    )


def _resolve_enrollment_artifact_authorization(
    *,
    org: Organization,
    token: str,
    role: str,
) -> ArtifactDownloadAuthorization | None:
    """Enrollment/session/legacy-only resolution (no NodeCredential)."""
    authorization = resolve_enrollment_authorization(
        org=org,
        secret=token,
        role=role,
    )
    if authorization is not None:
        return ArtifactDownloadAuthorization(
            token=authorization.token,
            session=authorization.session,
        )
    if not token:
        return None
    for row in NodeToken.objects.filter(
        organization=org,
        role=role,
        enrollment_mode=NodeToken.EnrollmentMode.LEGACY,
    ).only(
        "token",
        "used_at",
    ):
        if secrets.compare_digest(row.token, token) and row.used_at is not None:
            return ArtifactDownloadAuthorization(token=row)
    return None


def resolve_artifact_download_authorization(
    *,
    org: Organization,
    token: str,
    role: str,
) -> ArtifactDownloadAuthorization | None:
    """Resolve enrollment sessions/tokens, used legacy links, or node credentials."""
    authorization = _resolve_enrollment_artifact_authorization(
        org=org,
        token=token,
        role=role,
    )
    if authorization is not None:
        return authorization
    if not token:
        return None
    credential = active_node_credential(org=org, secret=token, role=role)
    if credential is not None:
        return ArtifactDownloadAuthorization(credential=credential)
    return None


def token_usable_for_bootstrap(
    *,
    org: Organization,
    token: str,
    role: str,
) -> bool:
    """
    True when bootstrap stub may be served (active token or legacy used link).

    Used links must still return a shell script so ``curl | bash`` can run ``hfl-enroll``
    and report idempotent success when the agent is already enrolled locally.
    NodeCredential is intentionally excluded: bootstrap is install-link scoped.
    """
    return (
        resolve_bootstrap_enrollment_token(
            org=org,
            token=token,
            role=role,
        )
        is not None
    )


def resolve_bootstrap_enrollment_token(
    *,
    org: Organization,
    token: str,
    role: str,
) -> NodeToken | None:
    """Resolve the token that authoritatively defines bootstrap settings."""
    authorization = _resolve_enrollment_artifact_authorization(
        org=org,
        token=token,
        role=role,
    )
    return authorization.token if authorization is not None else None
