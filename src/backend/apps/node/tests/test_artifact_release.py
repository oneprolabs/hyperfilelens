"""Tests for agent release artifact filename resolution."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import SimpleTestCase
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIRequestFactory

from apps.node.api.views import artifact_release as release
from apps.node.services.internal import (
    agent_download_slots as download_slots,
    agent_release as release_service,
)


@pytest.mark.parametrize(
    ("role", "platform", "arch", "ubuntu_release"),
    [
        ("agent", "linux", "amd64", None),
        ("proxy", "linux", "amd64", "20.04"),
        ("gateway", "linux", "arm64", "24.04"),
        ("proxy", "darwin", "amd64", None),
        ("gateway", "windows", "amd64", None),
    ],
)
def test_dist_filename_by_role(role, platform, arch, ubuntu_release):
    filename = release._dist_filename(
        "1.0.0",
        platform,
        arch,
        ubuntu_release=ubuntu_release,
    )
    if ubuntu_release:
        suffix = "ubuntu" + ubuntu_release.replace(".", "")
        assert filename == f"hfl-agent-1.0.0-linux-{arch}-{suffix}.tar.gz"
    elif platform == "windows":
        assert filename == "hfl-agent-1.0.0-windows-amd64.zip"
    else:
        assert filename == f"hfl-agent-1.0.0-{platform}-{arch}.tar.gz"


def test_ubuntu_bundle_release_uses_reported_host_version():
    assert release._ubuntu_bundle_release("proxy", "linux", "20.04") == "20.04"
    assert release._ubuntu_bundle_release("gateway", "linux", "24.04.2") == "24.04"
    assert release._ubuntu_bundle_release("agent", "linux", "20.04") is None
    assert release._ubuntu_bundle_release("proxy", "darwin", "24.04") is None
    assert release._ubuntu_bundle_release("gateway", "linux", "22.04") == "22.04"


def test_ubuntu_bundle_release_preserves_legacy_2404_default():
    assert release._ubuntu_bundle_release("gateway", "linux") == "24.04"


@pytest.mark.parametrize(
    ("os_version", "suffix"),
    [
        ("20.04", "ubuntu2004"),
        ("22.04", "ubuntu2204"),
        ("24.04", "ubuntu2404"),
    ],
)
def test_get_agent_artifact_proxy_linux(tmp_path, monkeypatch, os_version, suffix):
    root = tmp_path / "agent-releases"
    version_dir = root / "1.0.0"
    version_dir.mkdir(parents=True)
    (version_dir / "hfl-agent-1.0.0-linux-amd64-ubuntu2404.tar.gz").write_bytes(b"x")
    (version_dir / "hfl-agent-1.0.0-linux-amd64-ubuntu2204.tar.gz").write_bytes(b"w")
    (version_dir / "hfl-agent-1.0.0-linux-amd64-ubuntu2004.tar.gz").write_bytes(b"z")
    (version_dir / "hfl-agent-1.0.0-linux-amd64.tar.gz").write_bytes(b"y")

    monkeypatch.setattr(release_service, "agent_releases_root", lambda: root)
    monkeypatch.delenv("AGENT_FILENAME", raising=False)
    monkeypatch.setenv("AGENT_VERSION", "1.0.0")

    artifact = release._get_agent_artifact(
        "proxy",
        platform="linux",
        arch="amd64",
        os_version=os_version,
    )
    assert artifact.filename == f"hfl-agent-1.0.0-linux-amd64-{suffix}.tar.gz"
    assert artifact.artifact_path == (
        f"/media/agent-releases/1.0.0/hfl-agent-1.0.0-linux-amd64-{suffix}.tar.gz"
    )


def test_get_agent_artifact_agent_linux(tmp_path, monkeypatch):
    root = tmp_path / "agent-releases"
    version_dir = root / "1.0.0"
    version_dir.mkdir(parents=True)
    (version_dir / "hfl-agent-1.0.0-linux-amd64-ubuntu2404.tar.gz").write_bytes(b"x")
    (version_dir / "hfl-agent-1.0.0-linux-amd64.tar.gz").write_bytes(b"y")

    monkeypatch.setattr(release_service, "agent_releases_root", lambda: root)
    monkeypatch.delenv("AGENT_FILENAME", raising=False)
    monkeypatch.setenv("AGENT_VERSION", "1.0.0")

    artifact = release._get_agent_artifact("agent", platform="linux", arch="amd64")
    assert artifact.filename == "hfl-agent-1.0.0-linux-amd64.tar.gz"


def test_try_acquire_slot_reuses_enrollment_id(monkeypatch):
    store: dict[str, set[str]] = {}

    class FakeRedis:
        def eval(self, script, numkeys, key, slot_id, maxn, ttl):  # noqa: ARG002
            members = store.setdefault(key, set())
            if slot_id in members:
                return [1, len(members)]
            members.add(slot_id)
            if len(members) > int(maxn):
                members.discard(slot_id)
                return [0, len(members)]
            return [1, len(members)]

    monkeypatch.setattr(download_slots, "_redis_client", lambda: FakeRedis())
    monkeypatch.setenv("AGENT_RELEASES_TENANT_MAX_CONCURRENT_DOWNLOADS", "1")

    ok1, _ = download_slots.try_acquire_slot("default", "enroll-token-a")
    ok2, _ = download_slots.try_acquire_slot("default", "enroll-token-a")
    ok3, count = download_slots.try_acquire_slot("default", "enroll-token-b")

    assert ok1 is True
    assert ok2 is True
    assert ok3 is False
    assert count == 1


def test_try_acquire_slot_fails_open_when_redis_is_unavailable(monkeypatch):
    class UnavailableRedis:
        def eval(self, *_args, **_kwargs):
            raise RedisConnectionError("review Redis is unavailable")

    monkeypatch.setattr(download_slots, "_redis_client", lambda: UnavailableRedis())

    allowed, count = download_slots.try_acquire_slot("default", "session:1")

    assert allowed is True
    assert count == 0


def test_release_session_slot_is_idempotent(monkeypatch):
    client = mock.Mock()
    monkeypatch.setattr(download_slots, "_redis_client", lambda: client)

    download_slots.release_session_slot("example", 3)
    download_slots.release_session_slot("example", 3)

    assert client.srem.call_args_list == [
        mock.call("hfl:agent-releases:slots:example", "session:3"),
        mock.call("hfl:agent-releases:slots:example", "session:3"),
    ]


def test_release_session_slot_fails_open(monkeypatch):
    client = mock.Mock()
    client.srem.side_effect = RedisConnectionError("Redis unavailable")
    monkeypatch.setattr(download_slots, "_redis_client", lambda: client)

    download_slots.release_session_slot("example", 3)


def test_slot_script_does_not_extend_expiry_on_rejection_or_reuse():
    script = download_slots._ACQUIRE_LUA
    assert script.index("SISMEMBER") < script.index("EXPIRE")
    assert script.index("count >= limit") < script.index("EXPIRE")
    assert script.count("redis.call('EXPIRE'") == 1
    assert "redis.call('TTL', key) == -1" in script


class AgentDownloadAuthorizationTests(SimpleTestCase):
    def test_capacity_denial_is_marked_for_nginx_without_exposing_credentials(self):
        artifact_path = "/media/agent-releases/1.0.0/agent.zip"
        request = APIRequestFactory().get(
            "/api/v1/node/enrollment/agent-releases/auth",
            HTTP_X_ORIGINAL_URI=f"{artifact_path}?t=signed-secret",
        )
        payload = {
            "p": artifact_path,
            "org": "example",
            "role": "agent",
            "token_id": 17,
        }
        organization = SimpleNamespace(key="example")
        with (
            mock.patch.object(release, "_release_download_token", return_value="signed-secret"),
            mock.patch.object(release, "_load_release_token", return_value=payload),
            mock.patch.object(
                release.Organization.objects,
                "filter",
                return_value=SimpleNamespace(first=lambda: organization),
            ),
            mock.patch.object(release, "_release_authorization_is_valid", return_value=True),
            mock.patch.object(release, "try_acquire_slot", return_value=(False, 20)),
        ):
            response = release.AgentReleasesAuthView.as_view()(request)

        self.assertEqual(response.status_code, 403)  # Nginx maps marked denials to 429.
        self.assertEqual(response["X-HFL-Download-Denial"], "authorization-capacity")
        self.assertEqual(
            response.data["error"],
            "too many active Agent download authorizations",
        )
        self.assertEqual(response["Retry-After"], "30")
        self.assertNotIn("signed-secret", str(response.data))

    def test_invalid_signature_is_not_marked_as_capacity(self):
        request = APIRequestFactory().get("/api/v1/node/enrollment/agent-releases/auth")
        with (
            mock.patch.object(release, "_release_download_token", return_value="bad"),
            mock.patch.object(release, "_load_release_token", return_value=None),
        ):
            response = release.AgentReleasesAuthView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("X-HFL-Download-Denial", response)


def test_session_download_auth_fails_when_conditional_renewal_loses_race():
    session = SimpleNamespace(
        pk=17,
        absolute_expires_at=timezone.now() + timedelta(hours=1),
    )
    lookup = mock.Mock()
    lookup.first.return_value = session
    renewal = mock.Mock()
    renewal.update.return_value = 0

    with mock.patch.object(
        release.NodeInstallationSession.objects,
        "filter",
        side_effect=[lookup, renewal],
    ):
        valid = release._release_authorization_is_valid(
            org=SimpleNamespace(pk=1),
            role="agent",
            token_id=3,
            session_id=17,
        )

    assert valid is False
    renewal.update.assert_called_once()
