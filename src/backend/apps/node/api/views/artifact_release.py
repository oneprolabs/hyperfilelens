"""Agent binary release URLs and Nginx agent-releases auth."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse, urlsplit, urlunsplit

from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.models import Organization
from apps.iam.permissions_org import resolve_org_key
from apps.node.api import permissions as node_permissions
from apps.node.api.views.enrollment_helpers import (
    resolve_artifact_download_authorization,
    token_usable_for_artifact_download,
)
from apps.node.models import Node, NodeCredential, NodeInstallationSession, NodeToken
from apps.node.services.internal.enrollment_auth import (
    INSTALLATION_SESSION_IDLE_SECONDS,
)
from apps.node.services.internal.agent_release import (
    AGENT_RELEASES_URL_PREFIX,
    agent_releases_root,
    dist_filename,
    latest_published_agent_version,
    normalize_ubuntu_bundle_release,
    resolve_agent_version,
    ubuntu_bundle_release,
    version_has_dist,
)

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

_REDIS_ERRORS: tuple[type[BaseException], ...] = (OSError, TypeError, ValueError)
if redis is not None:
    _REDIS_ERRORS += (redis.exceptions.RedisError,)


@dataclass(frozen=True)
class AgentArtifact:
    platform: str
    arch: str
    version: str
    filename: str

    @property
    def artifact_path(self) -> str:
        return f"{AGENT_RELEASES_URL_PREFIX}/{self.version}/{self.filename}"

    @property
    def local_path(self) -> Path:
        return agent_releases_root() / self.version / self.filename


def _normalize_platform(value: str | None) -> str | None:
    if not value:
        return None
    platform = str(value).strip().lower()
    if platform in ("linux", "darwin", "windows"):
        return platform
    return None


def _normalize_arch(value: str | None) -> str | None:
    if not value:
        return None
    arch = str(value).strip().lower()
    if arch in ("x86_64", "amd64", "x64"):
        return "amd64"
    if arch in ("aarch64", "arm64"):
        return "arm64"
    return None


def _get_agent_artifact(
    role: str,
    *,
    platform: str | None = None,
    arch: str | None = None,
    os_version: str | None = None,
) -> AgentArtifact:
    plat = _normalize_platform(platform) or os.getenv("AGENT_PLATFORM", "linux")
    machine = _normalize_arch(arch) or os.getenv("AGENT_ARCH", "amd64")
    version = resolve_agent_version(plat, machine, role, os_version)
    ubuntu_release = ubuntu_bundle_release(role, plat, os_version)
    filename = os.getenv("AGENT_FILENAME") or dist_filename(
        version,
        plat,
        machine,
        ubuntu_release=ubuntu_release,
    )
    return AgentArtifact(
        platform=plat, arch=machine, version=version, filename=filename
    )


def _build_download_url(
    request,
    artifact: AgentArtifact,
    signed: str,
    *,
    api_base: str = "",
) -> str:
    """Prefer client ``api_base`` (console origin) over internal request host."""
    api_base = (
        str(api_base or request.query_params.get("api_base") or "")
        .strip()
        .rstrip("/")
    )
    if api_base:
        return f"{api_base}{artifact.artifact_path}?t={quote(signed, safe='')}"

    forwarded_proto = str(request.headers.get("X-Forwarded-Proto") or "").strip()
    forwarded_host = str(request.headers.get("X-Forwarded-Host") or "").strip()
    if forwarded_proto and forwarded_host:
        parts = urlsplit(request.build_absolute_uri(artifact.artifact_path))
        return urlunsplit(
            (
                forwarded_proto,
                forwarded_host,
                parts.path,
                f"t={quote(signed, safe='')}",
                "",
            ),
        )

    download_url = request.build_absolute_uri(artifact.artifact_path)
    sep = "&" if "?" in download_url else "?"
    return f"{download_url}{sep}t={quote(signed, safe='')}"


def issue_node_maintenance_release(
    *,
    request,
    node: Node,
    api_base: str = "",
) -> dict:
    """Issue a short-lived release URL for an existing, authorized node.

    The signed URL is bound to the existing node record instead of an enrollment
    token. This keeps maintenance downloads independent from install quotas and
    avoids exposing a reusable enrollment credential to the browser.
    """
    from apps.node.services.internal.agent_upgrade import (
        node_os_version,
        node_platform_arch,
    )
    from common.deploy.site import enrollment_tls_verify

    platform, arch = node_platform_arch(node)
    artifact = _get_agent_artifact(
        node.role,
        platform=platform,
        arch=arch,
        os_version=node_os_version(node),
    )
    if not artifact.local_path.is_file():
        raise FileNotFoundError("agent release artifact is unavailable")

    ttl = int(os.getenv("AGENT_RELEASE_URL_TTL_SECONDS", "600"))
    signed = _make_release_token(
        {
            "p": artifact.artifact_path,
            "org": node.organization.key,
            "role": node.role,
            "maintenance_node_id": node.id,
        },
        ttl_seconds=ttl,
    )
    try:
        download_size = artifact.local_path.stat().st_size
    except OSError:
        download_size = 0

    return {
        "version": artifact.version,
        "platform": artifact.platform,
        "arch": artifact.arch,
        "path": artifact.artifact_path,
        "download_url": _build_download_url(
            request,
            artifact,
            signed,
            api_base=api_base,
        ),
        "expires_in": ttl,
        "download_size": download_size,
        "required_space": max(500 * 1024 * 1024, download_size * 4),
        "tls_verify": enrollment_tls_verify(),
    }


def _make_release_token(payload: dict, ttl_seconds: int) -> str:
    return (
        signing.dumps(payload, salt="agent-releases", compress=True)
        + f".ttl{ttl_seconds}"
    )


def _load_release_token(token: str, max_age: int) -> dict | None:
    token = (token or "").split(".ttl", 1)[0]
    try:
        return signing.loads(token, salt="agent-releases", max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def _release_download_token(request) -> str:
    """Read signed ``t`` from query or from ``X-Original-URI`` (nginx auth_request)."""
    direct = str(request.query_params.get("t") or "").strip()
    if direct:
        return direct
    original_uri = str(request.headers.get("X-Original-URI", "") or "").strip()
    if "?" not in original_uri:
        return ""
    parsed = urlparse(original_uri)
    values = parse_qs(parsed.query).get("t") or []
    return str(values[0]).strip() if values else ""


def _release_file_exists(release_path: str) -> bool:
    prefix = f"{AGENT_RELEASES_URL_PREFIX}/"
    if not release_path.startswith(prefix):
        return False
    rel = release_path.removeprefix(prefix)
    return (agent_releases_root() / rel).is_file()


def _release_authorization_is_valid(
    *,
    org: Organization,
    role: str,
    token_id: int,
    session_id: int | None,
) -> bool:
    """Validate signed authorization IDs without embedding their secrets."""
    now = timezone.now()
    if session_id is not None:
        session = NodeInstallationSession.objects.filter(
            pk=session_id,
            organization=org,
            role=role,
            enrollment_token_id=token_id,
            status=NodeInstallationSession.Status.ACTIVE,
            idle_expires_at__gt=now,
            absolute_expires_at__gt=now,
        ).first()
        if session is None:
            return False
        renewed = min(
            now + timedelta(seconds=INSTALLATION_SESSION_IDLE_SECONDS),
            session.absolute_expires_at,
        )
        updated = NodeInstallationSession.objects.filter(
            pk=session.pk,
            status=NodeInstallationSession.Status.ACTIVE,
            idle_expires_at__gt=now,
            absolute_expires_at__gt=now,
        ).update(
            last_activity_at=now,
            idle_expires_at=renewed,
            updated_at=now,
        )
        return updated == 1

    token = NodeToken.objects.filter(
        pk=token_id,
        organization=org,
        role=role,
    ).first()
    if token is None:
        return False
    if token.enrollment_mode == NodeToken.EnrollmentMode.LEGACY:
        if token.used_at is not None:
            return True
    if not token.is_active or (token.expires_at and token.expires_at <= now):
        return False
    return True


def _release_credential_is_valid(
    *,
    org: Organization,
    role: str,
    credential_id: int,
    node_id: int,
) -> bool:
    """Validate a signed NodeCredential download without embedding the secret."""
    if credential_id <= 0 or node_id <= 0:
        return False
    return NodeCredential.objects.filter(
        pk=credential_id,
        organization=org,
        role=role,
        node_id=node_id,
        is_active=True,
    ).exists()


def _redis_client():
    if redis is None:
        return None
    url = (
        os.getenv("AGENT_RELEASES_REDIS_URL")
        or os.getenv("CACHE_REDIS_URL")
        or os.getenv(
            "CELERY_BROKER_URL",
            "redis://redis:6379/0",
        )
    )
    try:
        return redis.Redis.from_url(url, decode_responses=True)
    except OSError:
        return None


_SLOT_LUA = """
local key = KEYS[1]
local token = ARGV[1]
local maxn = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

if redis.call('SISMEMBER', key, token) == 1 then
  return {1, redis.call('SCARD', key)}
end

local added = redis.call('SADD', key, token)
local n = redis.call('SCARD', key)
redis.call('EXPIRE', key, ttl)

if n > maxn then
  if added == 1 then
    redis.call('SREM', key, token)
  end
  return {0, n}
end
return {1, n}
"""


def _try_acquire_slot(tenant_key: str, slot_id: str) -> tuple[bool, int]:
    maxn = int(os.getenv("AGENT_RELEASES_TENANT_MAX_CONCURRENT_DOWNLOADS", "20"))
    ttl = int(os.getenv("AGENT_RELEASES_SLOT_TTL_SECONDS", "3600"))
    client = _redis_client()
    if client is None:
        return True, 0
    key = f"hfl:agent-releases:slots:{tenant_key}"
    try:
        allowed, count = client.eval(_SLOT_LUA, 1, key, slot_id, str(maxn), str(ttl))
        return bool(int(allowed)), int(count)
    except _REDIS_ERRORS:
        return True, 0


class AgentLatestReleaseView(APIView):
    """Published agent semver for console upgrade UI."""

    permission_classes = [
        node_permissions.IsAuthenticated,
        node_permissions.IsOrgStaffReader,
    ]

    def get(self, request):
        return Response({"version": latest_published_agent_version()})


class AgentReleaseView(APIView):
    """Issue a short-lived signed download URL for the agent binary."""

    permission_classes = [node_permissions.AllowAny]

    def get(self, request):
        org_key = str(request.query_params.get("org") or "").strip()
        role = str(request.query_params.get("role") or "").strip()
        enroll_token = str(request.query_params.get("token") or "").strip()
        plat_raw = str(request.query_params.get("platform") or "").strip()
        arch_raw = str(request.query_params.get("arch") or "").strip()
        os_version = str(request.query_params.get("os_version") or "").strip()

        platform: str | None = None
        if plat_raw:
            platform = _normalize_platform(plat_raw)
            if platform is None:
                return Response({"error": "invalid platform"}, status=400)

        arch: str | None = None
        if arch_raw:
            arch = _normalize_arch(arch_raw)
            if arch is None:
                return Response({"error": "invalid arch"}, status=400)

        if (
            platform == "linux"
            and role in {Node.Role.PROXY, Node.Role.GATEWAY}
            and os_version
            and normalize_ubuntu_bundle_release(os_version) is None
        ):
            return Response(
                {"error": "gateway/proxy supports Ubuntu 20.04, 22.04, or 24.04"},
                status=400,
            )

        if not org_key or role not in dict(Node.Role.choices) or not enroll_token:
            return Response({"error": "org/role/token required"}, status=400)

        org = Organization.objects.filter(key=org_key, is_active=True).first()
        if org is None:
            return Response({"error": "organization not found"}, status=404)

        authorization = resolve_artifact_download_authorization(
            org=org,
            token=enroll_token,
            role=role,
        )
        if authorization is None:
            return Response({"error": "invalid enrollment token"}, status=401)

        artifact = _get_agent_artifact(
            role,
            platform=platform,
            arch=arch,
            os_version=os_version,
        )
        if not artifact.local_path.is_file():
            return Response(
                {"error": "agent release artifact is unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        ttl = int(os.getenv("AGENT_RELEASE_URL_TTL_SECONDS", "600"))
        signed_payload: dict = {
            "p": artifact.artifact_path,
            "org": org.key,
            "role": role,
        }
        if authorization.credential is not None:
            signed_payload["credential_id"] = authorization.credential.id
            signed_payload["node_id"] = authorization.credential.node_id
        else:
            assert authorization.token is not None
            signed_payload["token_id"] = authorization.token.id
            signed_payload["session_id"] = (
                authorization.session.id
                if authorization.session is not None
                else None
            )
        signed = _make_release_token(signed_payload, ttl_seconds=ttl)

        download_url = _build_download_url(request, artifact, signed)
        try:
            download_size = artifact.local_path.stat().st_size
        except OSError:
            download_size = 0
        required_space = max(500 * 1024 * 1024, download_size * 4)

        return Response(
            {
                "version": artifact.version,
                "platform": artifact.platform,
                "arch": artifact.arch,
                "path": artifact.artifact_path,
                "download_url": download_url,
                "expires_in": ttl,
                "download_size": download_size,
                "required_space": required_space,
            }
        )


class AgentReleasesAuthView(APIView):
    """Nginx ``auth_request`` hook for ``/media/agent-releases/*`` downloads."""

    permission_classes = [node_permissions.AllowAny]

    def get(self, request):
        original_uri = str(request.headers.get("X-Original-URI", "") or "").strip()
        token = _release_download_token(request)
        ttl = int(os.getenv("AGENT_RELEASE_URL_TTL_SECONDS", "600"))

        payload = _load_release_token(token, max_age=ttl)
        if not payload:
            return Response(
                {"error": "invalid token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        expected_path = str(payload.get("p") or "")
        org_key = str(payload.get("org") or "")
        legacy_enroll = str(payload.get("enroll") or "")
        role = str(payload.get("role") or "")
        releases_prefix = f"{AGENT_RELEASES_URL_PREFIX}/"
        if not expected_path.startswith(releases_prefix):
            return Response(
                {"error": "invalid path"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        req_path = original_uri.split("?", 1)[0] if original_uri else ""
        if req_path != expected_path:
            if req_path.startswith(releases_prefix):
                return Response(
                    {"error": "path mismatch"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            if not _release_file_exists(expected_path):
                return Response(
                    {"error": "release not found"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

        header_org = resolve_org_key(request)
        if header_org and header_org != org_key:
            return Response(
                {"error": "org mismatch"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        org = Organization.objects.filter(key=org_key, is_active=True).first()
        if org is None:
            return Response(
                {"error": "organization not found"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            token_id = int(payload.get("token_id") or 0)
            raw_session_id = payload.get("session_id")
            session_id = int(raw_session_id) if raw_session_id is not None else None
            credential_id = int(payload.get("credential_id") or 0)
            node_id = int(payload.get("node_id") or 0)
            maintenance_node_id = int(payload.get("maintenance_node_id") or 0)
        except (TypeError, ValueError):
            token_id = 0
            session_id = None
            credential_id = 0
            node_id = 0
            maintenance_node_id = 0

        if maintenance_node_id > 0:
            authorization_valid = Node.objects.filter(
                pk=maintenance_node_id,
                organization=org,
                role=role,
            ).exists()
            slot_id = f"maintenance:{maintenance_node_id}"
        elif credential_id > 0:
            authorization_valid = _release_credential_is_valid(
                org=org,
                role=role,
                credential_id=credential_id,
                node_id=node_id,
            )
            slot_id = f"credential:{credential_id}"
        elif token_id > 0:
            authorization_valid = _release_authorization_is_valid(
                org=org,
                role=role,
                token_id=token_id,
                session_id=session_id,
            )
            slot_id = (
                f"session:{session_id}"
                if session_id is not None
                else f"token:{token_id}"
            )
        else:
            # Compatibility for URLs issued by the immediately previous
            # deployment. Their maximum lifetime is only a few minutes.
            authorization_valid = token_usable_for_artifact_download(
                org=org,
                token=legacy_enroll,
                role=role,
            )
            slot_id = legacy_enroll or token

        if not authorization_valid:
            return Response(
                {"error": "invalid enrollment token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        ok, _count = _try_acquire_slot(org.key, slot_id)
        if not ok:
            # Nginx auth_request treats only 401/403 as expected denials; 429 becomes 500.
            return Response(
                {"error": "too many concurrent downloads"},
                status=status.HTTP_403_FORBIDDEN,
            )

        resp = Response(status=status.HTTP_204_NO_CONTENT)
        resp["X-Tenant-Key"] = org.key
        return resp


# Backward-compatible aliases for tests and internal imports.
_agent_releases_root = agent_releases_root
_resolve_agent_version = resolve_agent_version
_version_has_dist = version_has_dist
_dist_filename = dist_filename
_ubuntu_bundle_release = ubuntu_bundle_release
