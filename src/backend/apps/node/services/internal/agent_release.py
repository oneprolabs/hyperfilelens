"""Published agent release artifacts under media/agent-releases/."""

from __future__ import annotations

import json
import os
import re
import tarfile
import zipfile
from pathlib import Path

from django.conf import settings

from apps.node.models.base import NodeRole

AGENT_RELEASES_URL_PREFIX = "/media/agent-releases"
UBUNTU_BUNDLED_LINUX_ROLES = frozenset({"proxy", "gateway"})
SUPPORTED_UBUNTU_BUNDLE_RELEASES = frozenset({"20.04", "22.04", "24.04"})
UPGRADEABLE_AGENT_ROLES = frozenset(
    {
        NodeRole.AGENT,
        NodeRole.PROXY,
        NodeRole.GATEWAY,
    },
)

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
_MAIN_BUILD_RE = re.compile(r"^main-[0-9a-f]{7}$")


def agent_releases_root() -> Path:
    custom = os.getenv("AGENT_RELEASES_DIR", "").strip()
    if custom:
        return Path(custom)
    return Path(settings.MEDIA_ROOT) / "agent-releases"


def parse_semver(value: str) -> tuple[int, int, int] | None:
    text = str(value or "").strip().lstrip("vV")
    match = _SEMVER_RE.match(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def semver_sort_key(name: str) -> tuple[int, ...]:
    parsed = parse_semver(name)
    if parsed is None:
        return (0, 0, 0)
    return parsed


def semver_compare(left: str, right: str) -> int:
    """Return -1 if left < right, 0 if equal, 1 if left > right."""
    left_parsed = parse_semver(left)
    right_parsed = parse_semver(right)
    if left_parsed is None or right_parsed is None:
        return 0
    if left_parsed < right_parsed:
        return -1
    if left_parsed > right_parsed:
        return 1
    return 0


def is_main_build(value: str) -> bool:
    return bool(_MAIN_BUILD_RE.fullmatch(str(value or "").strip().lower()))


def is_agent_artifact_id(value: str) -> bool:
    return parse_semver(value) is not None or is_main_build(value)


def agent_release_sort_key(name: str) -> tuple[int, int, int, int, str]:
    """Sort Main artifacts before SemVer fallbacks; pinned versions still win."""
    value = str(name or "").strip().lower()
    if is_main_build(value):
        return (2, 0, 0, 0, value)
    parsed = parse_semver(value)
    if parsed is None:
        return (0, 0, 0, 0, value)
    return (1, *parsed, value)


def agent_version_compare(current: str, target: str) -> int:
    """Compare an installed Agent identity with the deployment-pinned target."""
    current = str(current or "").strip().lower()
    target = str(target or "").strip().lower()
    if is_main_build(target):
        return 0 if current == target else -1
    if is_main_build(current) and parse_semver(target) is not None:
        return -1
    return semver_compare(current, target)


def normalize_ubuntu_bundle_release(os_version: str | None) -> str | None:
    value = str(os_version or "").strip()
    for release in SUPPORTED_UBUNTU_BUNDLE_RELEASES:
        if value == release or value.startswith(f"{release}."):
            return release
    return None


def ubuntu_bundle_release(
    role: str | None,
    platform: str,
    os_version: str | None = None,
) -> str | None:
    if platform != "linux" or (role or "") not in UBUNTU_BUNDLED_LINUX_ROLES:
        return None
    # Older enrollment helpers did not report the OS version and historically
    # received the Ubuntu 24.04 bundle. Preserve that behavior during upgrades.
    if not str(os_version or "").strip():
        return "24.04"
    return normalize_ubuntu_bundle_release(os_version)


def dist_filename(
    version: str,
    platform: str,
    arch: str,
    *,
    ubuntu_release: str | None = None,
) -> str:
    ubuntu_release = normalize_ubuntu_bundle_release(ubuntu_release)
    if ubuntu_release and platform == "linux":
        suffix = "ubuntu" + ubuntu_release.replace(".", "")
        return f"hfl-agent-{version}-linux-{arch}-{suffix}.tar.gz"
    ext = "zip" if platform == "windows" else "tar.gz"
    return f"hfl-agent-{version}-{platform}-{arch}.{ext}"


def version_has_dist(
    root: Path,
    version: str,
    platform: str,
    arch: str,
    *,
    role: str | None = None,
    os_version: str | None = None,
) -> bool:
    ubuntu_release = ubuntu_bundle_release(role, platform, os_version)
    filename = dist_filename(version, platform, arch, ubuntu_release=ubuntu_release)
    return (root / version / filename).is_file()


def agent_release_commit(
    version: str,
    platform: str,
    arch: str,
    *,
    role: str | None = None,
    os_version: str | None = None,
) -> str:
    """Read the immutable build commit embedded in a published Agent bundle."""

    ubuntu_release = ubuntu_bundle_release(role, platform, os_version)
    archive = agent_releases_root() / version / dist_filename(
        version,
        platform,
        arch,
        ubuntu_release=ubuntu_release,
    )
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                names = [
                    name
                    for name in bundle.namelist()
                    if name.endswith("/MANIFEST.json")
                ]
                if len(names) != 1:
                    return ""
                if bundle.getinfo(names[0]).file_size > 1024 * 1024:
                    return ""
                raw = bundle.read(names[0])
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                member = next(
                    (
                        item
                        for item in bundle
                        if item.isfile() and item.name.endswith("/MANIFEST.json")
                    ),
                    None,
                )
                if member is None or member.size > 1024 * 1024:
                    return ""
                extracted = bundle.extractfile(member)
                if extracted is None:
                    return ""
                raw = extracted.read()
        manifest = json.loads(raw)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, json.JSONDecodeError):
        return ""
    if not isinstance(manifest, dict):
        return ""
    if str(manifest.get("agent_version") or "").strip() != version:
        return ""
    commit = str(manifest.get("agent_commit") or "").strip().lower()
    return commit if re.fullmatch(r"[0-9a-f]{40}", commit) else ""


def _dir_has_any_agent_archive(version_dir: Path) -> bool:
    if not version_dir.is_dir():
        return False
    for entry in version_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if name.startswith("hfl-agent-") and (
            name.endswith(".tar.gz") or name.endswith(".zip")
        ):
            return True
    return False


def list_published_agent_versions() -> list[str]:
    root = agent_releases_root()
    if not root.is_dir():
        return []
    versions = [
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and path.name not in ("dev",)
        and not path.name.startswith(".")
        and _dir_has_any_agent_archive(path)
    ]
    versions.sort(key=agent_release_sort_key, reverse=True)
    return versions


def latest_published_agent_version() -> str:
    """Console-facing agent release semver (newest under media/agent-releases/)."""
    preferred = os.getenv("AGENT_VERSION", "").strip()
    root = agent_releases_root()
    if preferred and _dir_has_any_agent_archive(root / preferred):
        return preferred
    published = list_published_agent_versions()
    if published:
        return published[0]
    return preferred or "0.0.0"


def latest_compatible_agent_version(
    platform: str,
    arch: str,
    role: str | None = None,
    os_version: str | None = None,
) -> str:
    """Newest published release that contains this node's exact bundle."""
    root = agent_releases_root()
    if not root.is_dir():
        return ""
    preferred = os.getenv("AGENT_VERSION", "").strip()
    if preferred and version_has_dist(
        root,
        preferred,
        platform,
        arch,
        role=role,
        os_version=os_version,
    ):
        return preferred
    candidates = [
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and path.name not in ("dev",)
        and not path.name.startswith(".")
        and version_has_dist(
            root,
            path.name,
            platform,
            arch,
            role=role,
            os_version=os_version,
        )
    ]
    candidates.sort(key=agent_release_sort_key, reverse=True)
    return candidates[0] if candidates else ""


def resolve_agent_version(
    platform: str,
    arch: str,
    role: str | None = None,
    os_version: str | None = None,
) -> str:
    """Pick newest release dir that contains a dist archive for platform/arch."""
    preferred = os.getenv("AGENT_VERSION", "").strip()
    compatible = latest_compatible_agent_version(
        platform,
        arch,
        role,
        os_version,
    )
    return compatible or preferred or latest_published_agent_version()
