#!/usr/bin/env python3
"""Certify one immutable Agent candidate on the current native runner."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.server
import json
import os
import pathlib
import platform
import socketserver
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.parse
import zipfile


def fail(message: str) -> None:
    raise RuntimeError(message)


def artifact_id(version: str) -> str:
    return version if version.startswith("main-") else f"v{version}"


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_target(root: pathlib.Path, name: str) -> pathlib.Path:
    target = (root / name).resolve()
    if target != root.resolve() and root.resolve() not in target.parents:
        fail(f"unsafe archive entry: {name}")
    return target


def extract_tar(path: pathlib.Path, destination: pathlib.Path) -> None:
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            safe_target(destination, member.name)
            if member.issym() or member.islnk():
                fail(f"archive links are not allowed: {member.name}")
        archive.extractall(destination, filter="data")


def extract_zip(path: pathlib.Path, destination: pathlib.Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            safe_target(destination, member.filename)
        archive.extractall(destination)


def extract_archive(path: pathlib.Path, destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".zip":
        extract_zip(path, destination)
    else:
        extract_tar(path, destination)


def normalize_platform(value: str) -> str:
    return {
        "linux": "linux",
        "darwin": "darwin",
        "windows": "windows",
        "win32": "windows",
        "cygwin": "windows",
    }.get(value.lower(), value.lower())


def normalize_arch(value: str) -> str:
    lowered = value.lower()
    if lowered in {"x86_64", "amd64", "x64"}:
        return "amd64"
    if lowered in {"aarch64", "arm64"}:
        return "arm64"
    return lowered


def load_candidate(
    internal_bundle: pathlib.Path,
    workspace: pathlib.Path,
    version: str,
    expected_platform: str,
    expected_arch: str,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, dict]:
    payload = workspace / "payload"
    extract_tar(internal_bundle, workspace)
    releases = payload / "media" / "agent-releases" / version
    extension = ".zip" if expected_platform == "windows" else ".tar.gz"
    candidate_name = (
        f"hfl-agent-{version}-{expected_platform}-{expected_arch}{extension}"
    )
    candidate = releases / candidate_name
    if not candidate.is_file():
        fail(f"candidate package is missing: {candidate}")

    enroll_name = f"hfl-enroll-{expected_platform}-{expected_arch}"
    if expected_platform == "windows":
        enroll_name += ".exe"
    enroll = payload / "media" / "enroll-bootstrap" / enroll_name
    if not enroll.is_file():
        fail(f"enrollment binary is missing: {enroll}")
    if expected_platform != "windows":
        enroll.chmod(enroll.stat().st_mode | stat.S_IXUSR)

    extracted = workspace / "candidate"
    extract_archive(candidate, extracted)
    roots = [path for path in extracted.iterdir() if path.is_dir()]
    if len(roots) != 1:
        fail("candidate package must contain exactly one root directory")
    root = roots[0]
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        fail("candidate package does not contain MANIFEST.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("platform") != expected_platform:
        fail(f"manifest platform mismatch: {manifest.get('platform')}")
    if manifest.get("arch") != expected_arch:
        fail(f"manifest architecture mismatch: {manifest.get('arch')}")
    if manifest.get("agent_version") != version:
        fail(f"manifest version mismatch: {manifest.get('agent_version')}")
    if manifest.get("bundle_flavor") != "standard":
        fail("Source Host certification requires the standard package")
    verify_manifest(root, manifest)
    return candidate, enroll, root, manifest


def verify_manifest(root: pathlib.Path, manifest: dict) -> None:
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        fail("manifest files are missing")
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.name == "MANIFEST.json":
            continue
        relative = path.relative_to(root).as_posix()
        actual_files.add(relative)
        expected = str(files.get(relative) or "").removeprefix("sha256:")
        if not expected:
            fail(f"manifest does not cover {relative}")
        if sha256_file(path) != expected:
            fail(f"checksum mismatch for {relative}")
    if actual_files != set(files):
        missing = sorted(set(files) - actual_files)
        extra = sorted(actual_files - set(files))
        fail(f"manifest file-set mismatch: missing={missing}, extra={extra}")


def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: pathlib.Path | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        fail(f"command failed with exit code {result.returncode}: {' '.join(command)}")
    return result


def run_cleanup(command: list[str]) -> bool:
    try:
        run(command, timeout=300)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"cleanup warning: {exc}", file=sys.stderr)
        return False


def create_fixture(root: pathlib.Path) -> None:
    (root / "nested" / "deep").mkdir(parents=True)
    (root / "empty-directory").mkdir()
    (root / "empty.txt").write_bytes(b"")
    (root / "hello.txt").write_text("HyperFileLens\n", encoding="utf-8")
    (root / "international-file.txt").write_text("backup-verification\n", encoding="utf-8")
    (root / "nested" / "deep" / "data.bin").write_bytes(bytes(range(256)) * 4096)
    if os.name != "nt":
        os.link(root / "hello.txt", root / "hello-hardlink.txt")
        os.symlink("hello.txt", root / "hello-symlink.txt")

        (root / "hello.txt").chmod(0o640)
        (root / "nested" / "deep" / "data.bin").chmod(0o600)
        (root / "nested" / "deep").chmod(0o750)
        timestamp_ns = 1_700_000_000_123_456_789
        for path in sorted(root.rglob("*"), reverse=True):
            os.utime(path, ns=(timestamp_ns, timestamp_ns), follow_symlinks=False)
        os.utime(root, ns=(timestamp_ns, timestamp_ns))


def tree_evidence(root: pathlib.Path) -> dict[str, dict[str, str | int]]:
    evidence: dict[str, dict[str, str | int]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata: dict[str, str | int] = {}
        if os.name != "nt":
            file_stat = path.lstat()
            metadata = {
                "mode": format(stat.S_IMODE(file_stat.st_mode), "04o"),
                # Millisecond precision is portable across Linux and macOS
                # restore implementations while still detecting metadata loss.
                "mtime_ms": file_stat.st_mtime_ns // 1_000_000,
            }
        if path.is_symlink():
            evidence[relative] = {
                "type": "symlink",
                "target": os.readlink(path),
                **metadata,
            }
        elif path.is_dir():
            evidence[relative] = {"type": "directory", **metadata}
        elif path.is_file():
            evidence[relative] = {
                "type": "file",
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                **metadata,
            }
    return evidence


def certify_backup(
    root: pathlib.Path,
    workspace: pathlib.Path,
    platform_name: str,
    tests: dict[str, str],
) -> None:
    suffix = ".exe" if platform_name == "windows" else ""
    agent = root / "bin" / f"hfl-agent{suffix}"
    kopia = root / "bin" / f"kopia{suffix}"
    if platform_name != "windows":
        agent.chmod(agent.stat().st_mode | stat.S_IXUSR)
        kopia.chmod(kopia.stat().st_mode | stat.S_IXUSR)
    run([str(agent), "-version"])
    run([str(kopia), "--version"])
    tests["native_binaries"] = "passed"

    source = workspace / "source"
    repository = workspace / "repository"
    restore = workspace / "restore"
    source.mkdir()
    repository.mkdir()
    create_fixture(source)
    expected = tree_evidence(source)

    home = workspace / "home"
    home.mkdir()
    config = workspace / "repository.config"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "KOPIA_PASSWORD": "hfl-release-certification",
            "KOPIA_CACHE_DIRECTORY": str(workspace / "cache"),
            "KOPIA_LOG_DIR": str(workspace / "logs"),
            "KOPIA_CHECK_FOR_UPDATES": "false",
            "KOPIA_USE_KEYRING": "false",
            "KOPIA_PERSIST_CREDENTIALS_ON_CONNECT": "false",
        }
    )
    base = [str(kopia), "--config-file", str(config), "--no-progress"]
    run(base + ["repository", "create", "filesystem", f"--path={repository}"], env=env)
    run(base + ["snapshot", "create", str(source), "--json"], env=env)
    tests["backup"] = "passed"
    run(base + ["snapshot", "verify", "--verify-files-percent=100"], env=env)
    tests["verify"] = "passed"
    run(base + ["snapshot", "restore", str(source), str(restore)], env=env)
    tests["restore"] = "passed"
    actual = tree_evidence(restore)
    if actual != expected:
        fail(f"restored tree mismatch\nexpected={expected}\nactual={actual}")
    tests["metadata"] = "passed"


class CertificationServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], package: pathlib.Path, version: str):
        self.package = package
        self.version = version
        self.heartbeat_count = 0
        super().__init__(address, CertificationHandler)


class CertificationHandler(http.server.BaseHTTPRequestHandler):
    server: CertificationServer

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        # Enrollment preflight dials the control-plane WebSocket before auth.
        # Real consoles answer 401/403 on this route; 404 means the path is missing.
        if path.startswith("/ws/"):
            self.send_error(403, "authentication required")
            return
        if path == "/api/v1/node/health":
            self.send_json({"status": "ok"})
            return
        if path == "/api/v1/node/enrollment/node-status":
            # Post-install verification waits for a routable online node.
            self.send_json({"node_id": 1, "status": "online", "routable": True})
            return
        if path == "/api/v1/node/enrollment/agent/release":
            self.send_json(
                {
                    "version": self.server.version,
                    "download_url": f"http://127.0.0.1:{self.server.server_port}/package",
                }
            )
            return
        if path == "/package":
            data = self.server.package.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path in {"/health", "/health/ready"}:
            self.send_json({"status": "ok"})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        if path == "/api/v1/node/enrollment/session":
            # Enrollment opens a resumable installation session before download.
            self.send_json(
                {"installation_session": "hfls_certification", "gateway_scope": ""},
                status=201,
            )
            return
        if path == "/api/v1/node/nodes/heartbeat/":
            self.server.heartbeat_count += 1
            self.send_json(
                {
                    "node_id": 1,
                    "status": "online",
                    "node_credential": "hfln_certification",
                }
            )
            return
        self.send_error(404)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        if path == "/api/v1/node/enrollment/session":
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404)

    def send_json(self, value: dict, *, status: int = 200) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        print(f"mock-console: {format % args}", flush=True)


@contextlib.contextmanager
def mock_console(package: pathlib.Path, version: str):
    server = CertificationServer(("127.0.0.1", 0), package, version)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


def installed_uninstall_command(platform_name: str) -> list[str]:
    if platform_name == "windows":
        script = pathlib.Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "HyperFileLens" / "Agent" / "bin" / "install.ps1"
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "uninstall",
            "-PurgeAll",
        ]
    return ["sudo", "/opt/hyperfilelens-agent/bin/install.sh", "uninstall", "--purge-all"]


def certify_enrollment(
    enroll: pathlib.Path,
    package: pathlib.Path,
    version: str,
    platform_name: str,
) -> None:
    with mock_console(package, version) as server:
        base = f"http://127.0.0.1:{server.server_port}"
        values = {
            "HFL_ORG_KEY": "certification-org",
            "HFL_NODE_ROLE": "agent",
            "HFL_NODE_TOKEN": "certification-token",
            "HFL_API_BASE": base,
            "HFL_WSS_URL": f"ws://127.0.0.1:{server.server_port}/ws/node/agent/",
            "HFL_INSECURE_TLS": "1",
            "HFL_ENROLL_NO_COLOR": "1",
        }
        env = os.environ.copy()
        env.update(values)
        command = [str(enroll), "install", "--yes"]
        if platform_name != "windows":
            command = ["sudo", "env"] + [f"{key}={value}" for key, value in values.items()] + command
        run(command, env=env, timeout=600)
        if server.heartbeat_count < 1:
            fail("enrollment helper did not register the Source Host")


def os_evidence() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--internal-bundle", required=True, type=pathlib.Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--expected-platform", required=True)
    parser.add_argument("--expected-arch", required=True)
    parser.add_argument("--report", required=True, type=pathlib.Path)
    parser.add_argument("--skip-enrollment", action="store_true")
    args = parser.parse_args()

    expected_platform = normalize_platform(args.expected_platform)
    expected_arch = normalize_arch(args.expected_arch)
    actual_platform = normalize_platform(sys.platform.split("-")[0])
    actual_arch = normalize_arch(platform.machine())
    if actual_platform != expected_platform:
        fail(f"native runner mismatch: expected {expected_platform}, got {actual_platform}")
    if actual_arch != expected_arch:
        fail(f"native runner architecture mismatch: expected {expected_arch}, got {actual_arch}")

    tests = {
        "candidate_manifest": "pending",
        "native_binaries": "pending",
        "backup": "pending",
        "verify": "pending",
        "restore": "pending",
        "metadata": "pending",
        "enrollment": "pending",
        "service_start": "pending",
        "uninstall": "pending",
    }
    candidate_hash = ""
    candidate_name = ""
    error = ""
    try:
        with tempfile.TemporaryDirectory(prefix="hfl-agent-cert-") as temporary:
            workspace = pathlib.Path(temporary)
            candidate, enroll, root, _manifest = load_candidate(
                args.internal_bundle,
                workspace,
                args.version,
                expected_platform,
                expected_arch,
            )
            candidate_name = candidate.name
            candidate_hash = sha256_file(candidate)
            tests["candidate_manifest"] = "passed"
            certify_backup(root, workspace, expected_platform, tests)
            if args.skip_enrollment:
                tests["enrollment"] = "skipped"
                tests["service_start"] = "skipped"
                tests["uninstall"] = "skipped"
            else:
                try:
                    certify_enrollment(enroll, candidate, args.version, expected_platform)
                    tests["enrollment"] = "passed"
                    tests["service_start"] = "passed"
                finally:
                    if run_cleanup(installed_uninstall_command(expected_platform)):
                        tests["uninstall"] = "passed"
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        raise
    finally:
        report = {
            "schema": 1,
            "tag": artifact_id(args.version),
            "version": args.version,
            "commit": args.commit,
            "artifact": {"name": candidate_name, "sha256": candidate_hash},
            "platform": {
                "os_family": expected_platform,
                "arch": expected_arch,
                **os_evidence(),
            },
            "consistency": "file-level",
            "tests": tests,
            "error": error,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
