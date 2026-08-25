"""Tests for published agent release version resolution."""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from apps.node.services.internal.agent_release import (
    agent_release_commit,
    agent_version_compare,
    is_main_build,
    latest_compatible_agent_version,
    latest_published_agent_version,
    resolve_agent_version,
    semver_compare,
)
from apps.node.services.internal import agent_release as release_service


@pytest.fixture
def releases_root(tmp_path, monkeypatch):
    media = tmp_path / "media" / "agent-releases"
    media.mkdir(parents=True)
    monkeypatch.setattr(release_service, "agent_releases_root", lambda: media)
    monkeypatch.delenv("AGENT_VERSION", raising=False)
    return media


def test_latest_published_agent_version_picks_highest_semver(releases_root):
    for version in ("1.0.0", "1.0.2", "1.0.10"):
        version_dir = releases_root / version
        version_dir.mkdir()
        (version_dir / f"hfl-agent-{version}-linux-amd64.tar.gz").write_bytes(b"x")
    assert latest_published_agent_version() == "1.0.10"


def test_latest_published_agent_version_honors_agent_version_env(releases_root, monkeypatch):
    for version in ("1.0.0", "1.0.2"):
        version_dir = releases_root / version
        version_dir.mkdir()
        (version_dir / f"hfl-agent-{version}-linux-amd64.tar.gz").write_bytes(b"x")
    monkeypatch.setenv("AGENT_VERSION", "1.0.0")
    assert latest_published_agent_version() == "1.0.0"


def test_latest_published_agent_version_honors_main_build_env(releases_root, monkeypatch):
    version = "main-123abcd"
    version_dir = releases_root / version
    version_dir.mkdir()
    (version_dir / f"hfl-agent-{version}-linux-amd64.tar.gz").write_bytes(b"x")
    monkeypatch.setenv("AGENT_VERSION", version)

    assert latest_published_agent_version() == version


@pytest.mark.parametrize(
    ("os_version", "suffix"),
    [
        ("20.04", "ubuntu2004"),
        ("22.04", "ubuntu2204"),
        ("24.04", "ubuntu2404"),
    ],
)
def test_resolve_agent_version_uses_matching_ubuntu_bundle(
    releases_root,
    os_version,
    suffix,
):
    version_dir = releases_root / "1.0.0"
    version_dir.mkdir()
    (version_dir / f"hfl-agent-1.0.0-linux-amd64-{suffix}.tar.gz").write_bytes(b"x")
    assert resolve_agent_version("linux", "amd64", "proxy", os_version) == "1.0.0"


def test_latest_compatible_agent_version_ignores_newer_incompatible_bundle(
    releases_root,
):
    older = releases_root / "1.0.0"
    older.mkdir()
    (older / "hfl-agent-1.0.0-linux-amd64-ubuntu2204.tar.gz").write_bytes(b"x")
    newer = releases_root / "1.1.0"
    newer.mkdir()
    (newer / "hfl-agent-1.1.0-windows-amd64.zip").write_bytes(b"x")

    assert (
        latest_compatible_agent_version("linux", "amd64", "proxy", "22.04")
        == "1.0.0"
    )


def test_latest_compatible_agent_version_is_empty_without_exact_bundle(
    releases_root,
):
    version_dir = releases_root / "1.1.0"
    version_dir.mkdir()
    (version_dir / "hfl-agent-1.1.0-linux-amd64-ubuntu2404.tar.gz").write_bytes(b"x")

    assert latest_compatible_agent_version("linux", "arm64", "proxy", "24.04") == ""


def test_semver_compare_orders_versions():
    assert semver_compare("1.0.0", "1.0.1") < 0
    assert semver_compare("1.0.1", "1.0.0") > 0
    assert semver_compare("1.0.0", "1.0.0") == 0


def test_main_build_identity_comparison_uses_exact_commit():
    assert is_main_build("main-123abcd")
    assert not is_main_build("main-latest")
    assert agent_version_compare("main-123abcd", "main-123abcd") == 0
    assert agent_version_compare("main-7654321", "main-123abcd") < 0
    assert agent_version_compare("1.0.0", "main-123abcd") < 0


def test_agent_release_commit_reads_bundle_manifest(releases_root):
    version = "1.0.0"
    commit = "a" * 40
    version_dir = releases_root / version
    version_dir.mkdir()
    archive = version_dir / f"hfl-agent-{version}-linux-amd64.tar.gz"
    raw = json.dumps(
        {"agent_version": version, "agent_commit": commit}
    ).encode()
    info = tarfile.TarInfo(f"hfl-agent-{version}-linux-amd64/MANIFEST.json")
    info.size = len(raw)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.addfile(info, io.BytesIO(raw))

    assert agent_release_commit(version, "linux", "amd64") == commit
