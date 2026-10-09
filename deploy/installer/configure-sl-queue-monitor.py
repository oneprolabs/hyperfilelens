#!/usr/bin/env python3
"""HFL-owned, best-effort bundled Redis attachment; no SL image/config changes."""

import argparse
import json
import os
import pathlib
import re
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Dict

BRIDGE = "hyperfilelens-bridge"
ALIAS = "hfl-sourcelens-redis"
AUTO_KEY = "HFL_SL_RUNTIME_AUTO_REDIS_URL"
RESERVED_NAMES = {
    "redis",
    "postgres",
    "postgresql",
    "nginx",
    "api",
    "worker",
    "scheduler",
    "web",
}


class UnsafeSetup(RuntimeError):
    """A definitive safety/configuration rejection, not a discovery outage."""


def runtime_config_path(root: pathlib.Path) -> pathlib.Path:
    return root / "data" / "runtime" / "sl-queue-monitor.json"


def write_runtime_url(root: pathlib.Path, url: str) -> None:
    """Publish hot configuration outside logs/media, with no plaintext output."""
    path = runtime_config_path(root)
    if path.parent.is_symlink():
        raise UnsafeSetup("Refusing a symlink runtime directory")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(str(path.parent), 0o700)
    atomic_write(path, json.dumps({"version": 1, "url": url}) + "\n")


def disable_automatic_monitor(root: pathlib.Path) -> None:
    # Publish a tombstone before disconnecting; old API env cannot revive it.
    failure = None
    for operation in (
        lambda: write_runtime_url(root, ""),
        lambda: write_auto_url(root / ".env", ""),
        lambda: detach(root),
    ):
        try:
            operation()
        except Exception as exc:
            failure = exc
    if failure:
        raise failure


def safe_endpoint(endpoint: Dict[str, Any]) -> bool:
    names = set((endpoint.get("Aliases") or []) + (endpoint.get("DNSNames") or []))
    return ALIAS in names and not names.intersection(RESERVED_NAMES)


def docker(*arguments: str) -> str:
    result = subprocess.run(
        ["docker"] + list(arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise RuntimeError("Docker queue-monitor operation failed")
    return result.stdout


def inspect(container: str) -> Dict[str, Any]:
    return json.loads(docker("inspect", container))[0]


def owned(item: Dict[str, Any], root: pathlib.Path, service: str) -> bool:
    labels = item.get("Config", {}).get("Labels", {}) or {}
    expected = str(root / "sourcelens")
    files = labels.get("com.docker.compose.project.config_files", "").split(",")
    return (
        labels.get("com.docker.compose.project")
        in ("hyperfilelens-sourcelens", "sourcelens")
        and labels.get("com.docker.compose.service") == service
        and (
            labels.get("com.docker.compose.project.working_dir") == expected
            or expected + "/docker-compose.yml" in files
        )
    )


def trusted_peer(item: Dict[str, Any], root: pathlib.Path) -> bool:
    labels = item.get("Config", {}).get("Labels", {}) or {}
    service = labels.get("com.docker.compose.service")
    if owned(item, root, "nginx"):
        return True
    return (
        labels.get("com.docker.compose.project") == "hyperfilelens"
        and service
        in ("api", "api-blue", "api-green", "worker", "scheduler", "migration", "nginx")
        and (
            labels.get("com.docker.compose.project.working_dir") == str(root)
            or str(root / "docker-compose.yml")
            in labels.get(
                "com.docker.compose.project.config_files",
                "",
            ).split(",")
        )
    )


def find_service(root: pathlib.Path, service: str) -> Dict[str, Any]:
    ids = set()
    for project in ("hyperfilelens-sourcelens", "sourcelens"):
        ids.update(
            docker(
                "ps",
                "-q",
                "--no-trunc",
                "--filter",
                "label=com.docker.compose.project=" + project,
                "--filter",
                "label=com.docker.compose.service=" + service,
            ).split()
        )
    if not ids or len(ids) > 16:
        raise RuntimeError("Owned SL service discovery is unavailable or ambiguous")
    items = json.loads(docker("inspect", *sorted(ids)))
    matches = [item for item in items if owned(item, root, service)]
    if len(matches) != 1:
        raise RuntimeError("Exactly one owned running SL service is required")
    return matches[0]


def atomic_write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("Refusing a symlink configuration file")
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def env_value(path: pathlib.Path, key: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("HFL environment must be a regular file")
    match = re.search(r"^" + re.escape(key) + r"=(.*)$", path.read_text(), re.M)
    return match.group(1).strip().strip("\"'").strip() if match else ""


def write_auto_url(path: pathlib.Path, url: str) -> None:
    text = path.read_text(encoding="utf-8")
    quoted = (
        '"' + url.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'
    )
    line = AUTO_KEY + "=" + quoted
    pattern = r"^" + AUTO_KEY + r"=.*$"
    text = (
        re.sub(pattern, lambda _: line, text, flags=re.M)
        if re.search(
            pattern,
            text,
            re.M,
        )
        else text.rstrip() + "\n" + line + "\n"
    )
    atomic_write(path, text)


def broker_url(root: pathlib.Path) -> str:
    api = find_service(root, "api")
    values = dict(
        value.split("=", 1) for value in api["Config"].get("Env", []) if "=" in value
    )
    parsed = urlsplit(values.get("CELERY_BROKER_URL") or values.get("REDIS_URL") or "")
    if (
        parsed.scheme not in ("redis", "rediss")
        or parsed.hostname != "redis"
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 6379)
        or not re.fullmatch(r"/\d+", parsed.path)
    ):
        raise UnsafeSetup("SL broker does not target its managed Redis service")
    credentials = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
    return urlunsplit(
        (parsed.scheme, credentials + ALIAS + ":6379", parsed.path, "", "")
    )


def detach(root: pathlib.Path) -> None:
    marker = root / "deploy" / "sl-queue-monitor.json"
    if not marker.exists():
        return
    if marker.is_symlink():
        raise RuntimeError("Refusing a symlink attachment marker")
    state = json.loads(marker.read_text())
    if state.get("created") and re.fullmatch(
        r"[a-f0-9]{64}", state.get("container", "")
    ):
        present = docker(
            "ps", "-aq", "--no-trunc", "--filter", "id=" + state["container"]
        ).split()
        item = inspect(state["container"]) if state["container"] in present else None
        if item is not None:
            if not owned(item, root, "redis"):
                raise RuntimeError("Attachment no longer belongs to this installation")
            endpoint = item.get("NetworkSettings", {}).get("Networks", {}).get(BRIDGE)
            if not endpoint:
                # A timed-out connect may still finish in the daemon. Retain
                # ownership intent so a later lifecycle call can reconcile it.
                return
            if endpoint and ALIAS in (endpoint.get("Aliases") or []):
                docker("network", "disconnect", BRIDGE, item["Id"])
    marker.unlink()


def configure(root: pathlib.Path, remove: bool = False) -> None:
    env = root / ".env"
    mode = env_value(env, "SOURCELENS_MODE") or "bundled"
    explicit = env_value(env, "HFL_SL_RUNTIME_REDIS_URL")
    if (
        remove
        or mode != "bundled"
        or (explicit and urlsplit(explicit).hostname != ALIAS)
    ):
        disable_automatic_monitor(root)
        return
    if not (root / "sourcelens" / "docker-compose.yml").is_file():
        disable_automatic_monitor(root)
        return
    redis = find_service(root, "redis")
    url = broker_url(root)
    networks = redis.get("NetworkSettings", {}).get("Networks", {})
    endpoint = networks.get(BRIDGE)
    marker = root / "deploy" / "sl-queue-monitor.json"
    created = False
    bridge = json.loads(docker("network", "inspect", BRIDGE))[0]
    for cid in bridge.get("Containers", {}):
        if cid == redis["Id"]:
            continue
        peer = inspect(cid)
        if not trusted_peer(peer, root):
            raise UnsafeSetup("Shared bridge contains an untrusted workload")
        aliases = (
            peer.get("NetworkSettings", {})
            .get("Networks", {})
            .get(BRIDGE, {})
            .get("Aliases")
            or []
        )
        if ALIAS in aliases:
            raise UnsafeSetup("Queue-monitor alias is already in use")
    if endpoint:
        if not safe_endpoint(endpoint):
            raise UnsafeSetup("Existing SL Redis bridge endpoint has unsafe aliases")
        if marker.is_symlink():
            raise RuntimeError("Refusing a symlink attachment marker")
        previous = json.loads(marker.read_text()) if marker.exists() else {}
        created = previous.get("container") == redis["Id"] and bool(
            previous.get("created")
        )
    requested_new_connection = not endpoint
    try:
        if requested_new_connection:
            # Roll back only this invocation's new attachment on failure.
            atomic_write(
                marker, json.dumps({"container": redis["Id"], "created": True}) + "\n"
            )
            docker("network", "connect", "--alias", ALIAS, BRIDGE, redis["Id"])
            created = True
        endpoint = inspect(redis["Id"])["NetworkSettings"]["Networks"][BRIDGE]
        if not safe_endpoint(endpoint):
            raise UnsafeSetup("Queue-monitor DNS alias verification failed")
        atomic_write(
            marker, json.dumps({"container": redis["Id"], "created": created}) + "\n"
        )
        write_runtime_url(root, url)
    except Exception:
        if requested_new_connection:
            try:
                detach(root)
            except Exception:
                pass
        raise
    # Hot configuration is authoritative. A compatibility-env write failure
    # must not roll back an attachment backing an already published endpoint.
    write_auto_url(env, url)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=pathlib.Path)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        configure(root, args.remove)
        print("[queue-monitor] bundled monitoring configuration reconciled")
    except UnsafeSetup:
        try:
            disable_automatic_monitor(root)
        except Exception:
            pass
        print(
            "[queue-monitor] WARNING: unsafe automatic setup disabled; core services are unaffected"
        )
    except Exception:
        # Temporary discovery/daemon failures never tear down established setup.
        # New-attachment rollback is scoped inside configure(); never print
        # Docker stderr, URLs, credentials or task data.
        print(
            "[queue-monitor] WARNING: automatic setup could not be verified; existing setup retained; core services are unaffected"
        )


if __name__ == "__main__":
    main()
