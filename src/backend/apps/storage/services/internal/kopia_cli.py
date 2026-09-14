from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Callable

from apps.storage.repositories.models import Repository
from apps.storage.conf import kopia_config_lock_timeout_seconds
from apps.storage.services.internal.s3_client import endpoint_for_kopia
from apps.storage.services.internal.repository_secrets import (
    resolve_repository_secrets,
    scrub_secrets,
    secret_values_for_scrub,
)
from apps.storage.services.internal.repository_endpoints import repository_control_endpoint
from apps.storage.services.internal.repository_maintenance_summary import (
    parse_maintenance_info_summary,
    parse_maintenance_stderr_summary,
)
from apps.storage.services.internal.s3_url_style import (
    S3_URL_STYLE_AUTO,
    S3_URL_STYLE_VIRTUAL_HOSTED,
    kopia_s3_url_style,
    normalize_s3_url_style,
)


class KopiaCliError(Exception):
    pass


class KopiaRepositoryAlreadyExistsError(KopiaCliError):
    pass


class KopiaRepositoryBusyError(KopiaCliError):
    pass


class KopiaCliCancelled(KopiaCliError):
    pass


class KopiaExecutionLeaseLost(KopiaCliError):
    pass


class KopiaProcessTerminatedError(KopiaCliError):
    """Kopia exited because an external signal terminated the process."""

    def __init__(self, *, signal_number: int):
        self.signal_number = int(signal_number)
        super().__init__(
            f"Kopia process was terminated by signal {self.signal_number}."
        )


class KopiaControlDecision(StrEnum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    LOST_LEASE = "lost_lease"


KopiaControlCallback = Callable[[], KopiaControlDecision]


@dataclass(frozen=True)
class KopiaResult:
    stdout: str
    stderr: str
    maintenance_summary: dict | None = None


def create_s3_repository(repository: Repository, *, timeout_seconds: int | None = None) -> KopiaResult:
    result = _run_repository_command(
        repository,
        ["repository", "create", "s3", *_s3_flags(repository)],
        timeout_seconds=timeout_seconds,
    )
    if result.returncode == 0:
        return KopiaResult(stdout=result.stdout, stderr=result.stderr)

    if _repository_already_exists(result):
        raise KopiaRepositoryAlreadyExistsError(
            _format_failure("Kopia S3 repository already exists", result)
        )

    raise KopiaCliError(_format_failure("Kopia S3 repository create failed", result))


def connect_s3_repository(repository: Repository, *, timeout_seconds: int | None = None) -> KopiaResult:
    result = _run_repository_command(
        repository,
        ["repository", "connect", "s3", *_s3_flags(repository)],
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0:
        raise KopiaCliError(_format_failure("Kopia S3 repository connect failed", result))
    return KopiaResult(stdout=result.stdout, stderr=result.stderr)


def status(repository: Repository, *, timeout_seconds: int | None = None) -> KopiaResult:
    result = _run_repository_command(
        repository,
        ["repository", "status"],
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0:
        raise KopiaCliError(_format_failure("Kopia repository status failed", result))
    return KopiaResult(stdout=result.stdout, stderr=result.stderr)


def content_stats(repository: Repository, *, timeout_seconds: int | None = None) -> KopiaResult:
    result = _run_repository_command(
        repository,
        ["content", "stats", "--json"],
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0 and "unknown long flag '--json'" in f"{result.stdout}\n{result.stderr}":
        result = _run_repository_command(
            repository,
            ["content", "stats"],
            timeout_seconds=timeout_seconds,
        )
    if result.returncode != 0:
        raise KopiaCliError(_format_failure("Kopia content stats failed", result))
    return KopiaResult(stdout=result.stdout, stderr=result.stderr)


def run_maintenance(
    repository: Repository,
    *,
    full: bool,
    owner_identity: str,
    timeout_seconds: int | None = None,
    control: KopiaControlCallback | None = None,
) -> KopiaResult:
    if repository.repo_type == Repository.Type.S3:
        from apps.storage.services.internal.repository_ownership import (
            verify_s3_repository_ownership,
        )

        verify_s3_repository_ownership(repository, adopt_legacy=False)
    config_file = _maintenance_config_file(repository)
    _connect_maintenance_repository(
        repository,
        timeout_seconds=timeout_seconds,
        config_file=config_file,
        control=control,
    )
    username, hostname = _owner_parts(owner_identity)
    client = _run_repository_command(
        repository,
        ["repository", "set-client", f"--username={username}", f"--hostname={hostname}"],
        timeout_seconds=timeout_seconds,
        config_file=config_file,
        control=control,
    )
    if client.returncode != 0:
        raise KopiaCliError(_format_failure("Kopia maintenance client configuration failed", client))
    configured = _run_repository_command(
        repository,
        ["maintenance", "set", f"--owner={owner_identity}", "--enable-quick=false", "--enable-full=false"],
        timeout_seconds=timeout_seconds,
        config_file=config_file,
        control=control,
    )
    if configured.returncode != 0:
        raise KopiaCliError(_format_failure("Kopia maintenance owner configuration failed", configured))
    args = ["maintenance", "run"]
    if full:
        args.append("--full")
    maintenance_started_at = datetime.now(UTC)
    result = _run_repository_command(
        repository,
        args,
        timeout_seconds=timeout_seconds,
        config_file=config_file,
        control=control,
    )
    if result.returncode != 0:
        raise KopiaCliError(_format_failure("Kopia repository maintenance failed", result))
    mode = "full" if full else "quick"
    summary = None
    try:
        info = _run_repository_command(
            repository,
            ["maintenance", "info", "--json"],
            timeout_seconds=timeout_seconds,
            config_file=config_file,
            control=control,
        )
        if info.returncode == 0:
            summary = parse_maintenance_info_summary(
                info.stdout,
                mode=mode,
                started_at=maintenance_started_at,
            )
    except (KopiaCliCancelled, KopiaExecutionLeaseLost):
        raise
    except KopiaCliError:
        summary = None
    if summary is None:
        summary = parse_maintenance_stderr_summary(result.stderr, mode=mode)
    return KopiaResult(
        stdout=result.stdout,
        stderr=result.stderr,
        maintenance_summary=summary,
    )


def delete_s3_snapshots(
    repository: Repository,
    *,
    snapshot_ids: list[str],
    timeout_seconds: int | None = None,
) -> dict[str, object]:
    """Delete selected S3 snapshots from the control-plane worker.

    This is a fallback for an unavailable original writer. Normal snapshot
    deletion continues to run on the Agent or Proxy that created the snapshot.
    """

    if repository.repo_type != Repository.Type.S3:
        raise KopiaCliError("Control-plane snapshot delete only supports S3 repositories")

    # The database Claim is only a coordination record. Re-read the physical
    # marker immediately before this Controller-side data operation.
    from apps.storage.services.internal.repository_ownership import (
        verify_s3_repository_ownership,
    )

    verify_s3_repository_ownership(repository, adopt_legacy=False)
    config_file = _snapshot_delete_config_file(repository)
    secrets_payload = resolve_repository_secrets(repository)
    secret_values = secret_values_for_scrub(
        repository,
        secrets_payload,
    )
    try:
        _connect_maintenance_repository(
            repository,
            timeout_seconds=timeout_seconds,
            config_file=config_file,
        )
    except KopiaCliError as exc:
        safe_message = str(
            scrub_secrets(str(exc), extra_values=secret_values) or ""
        )
        raise KopiaCliError(safe_message) from exc
    results: list[dict[str, object]] = []
    failed = 0
    for raw_snapshot_id in snapshot_ids:
        snapshot_id = str(raw_snapshot_id or "").strip()
        if not snapshot_id:
            continue
        result = _run_repository_command(
            repository,
            ["snapshot", "delete", snapshot_id, "--delete"],
            timeout_seconds=timeout_seconds,
            config_file=config_file,
        )
        stdout_tail = str(
            scrub_secrets(result.stdout[-2000:], extra_values=secret_values) or ""
        )
        stderr_tail = str(
            scrub_secrets(result.stderr[-2000:], extra_values=secret_values) or ""
        )
        item: dict[str, object] = {
            "kopia_snapshot_id": snapshot_id,
            "status": "success" if result.returncode == 0 else "failed",
            "delete": {
                "exit_code": result.returncode,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            },
        }
        if result.returncode != 0:
            failed += 1
            item["error_message"] = str(
                scrub_secrets(
                    _format_failure("Kopia snapshot delete failed", result),
                    extra_values=secret_values,
                )
            )
        results.append(item)
    return {
        "results": results,
        "deleted_count": len(results) - failed,
        "failed_count": failed,
        "execution_mode": "controller_fallback",
    }


def _connect_maintenance_repository(
    repository: Repository,
    *,
    timeout_seconds: int | None,
    config_file: Path,
    control: KopiaControlCallback | None = None,
) -> None:
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(3):
        last_result = _run_repository_command(
            repository,
            ["repository", "connect", "s3", *_s3_flags(repository)],
            timeout_seconds=timeout_seconds,
            config_file=config_file,
            control=control,
        )
        if last_result.returncode == 0:
            return
        # A previous connect may have written a usable config before reporting
        # an error. Preserve that behavior before retrying a fresh connection.
        status_result = _run_repository_command(
            repository,
            ["repository", "status"],
            timeout_seconds=timeout_seconds,
            config_file=config_file,
            control=control,
        )
        if status_result.returncode == 0:
            return
        if attempt < 2:
            time.sleep(2**attempt)
    assert last_result is not None
    raise KopiaCliError(_format_failure("Kopia maintenance repository connect failed", last_result))


def _owner_parts(owner_identity: str) -> tuple[str, str]:
    parts = str(owner_identity or "").strip().split("@", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise KopiaCliError("Invalid repository owner identity")
    return parts[0], parts[1]


def _run_repository_command(
    repository: Repository,
    args: list[str],
    *,
    timeout_seconds: int | None,
    config_file: Path | None = None,
    control: KopiaControlCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    resolved_config_file = config_file or _config_file(repository)
    with _repository_config_lock(resolved_config_file):
        return _run_repository_command_unlocked(
            repository,
            args,
            timeout_seconds=timeout_seconds,
            config_file=resolved_config_file,
            control=control,
        )


def _run_repository_command_unlocked(
    repository: Repository,
    args: list[str],
    *,
    timeout_seconds: int | None,
    config_file: Path | None = None,
    control: KopiaControlCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    kopia_path = _kopia_path()
    config_file = config_file or _config_file(repository)
    _invalidate_changed_s3_connection(repository, config_file)
    env = _environment(repository)
    timeout = timeout_seconds or int(os.environ.get("HFL_KOPIA_TIMEOUT_SECONDS", "120"))
    command = [
        kopia_path,
        f"--config-file={config_file}",
        "--no-persist-credentials",
        "--no-progress",
        *args,
    ]
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while True:
            if control is not None:
                decision = control()
                if decision != KopiaControlDecision.CONTINUE:
                    _terminate_process_group(process)
                    if decision == KopiaControlDecision.CANCEL:
                        raise KopiaCliCancelled("Kopia repository maintenance was cancelled")
                    raise KopiaExecutionLeaseLost(
                        "Kopia repository maintenance execution lease was lost"
                    )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(process)
                raise KopiaCliError(f"Kopia CLI timed out after {timeout} seconds")
            try:
                stdout, stderr = process.communicate(timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        result = subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout,
            stderr,
        )
        if result.returncode < 0:
            raise KopiaProcessTerminatedError(
                signal_number=abs(result.returncode),
            )
        if result.returncode == 0 and args[:3] in (
            ["repository", "create", "s3"],
            ["repository", "connect", "s3"],
        ):
            _write_s3_connection_fingerprint(repository, config_file)
        return result
    except OSError as exc:
        raise KopiaCliError(f"Kopia CLI failed to start: {exc}") from exc
    except Exception:
        if process is not None and process.poll() is None:
            _terminate_process_group(process)
        raise


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=5)


def _s3_flags(repository: Repository) -> list[str]:
    config = repository.config or {}
    flags = [f"--bucket={repository.s3_bucket}"]
    control_endpoint = repository_control_endpoint(config)
    endpoint = endpoint_for_kopia(control_endpoint)
    if endpoint:
        flags.append(f"--endpoint={endpoint}")
    region = str(config.get("region") or "").strip()
    if not region and control_endpoint:
        # Match the boto3 path used by repository health checks. Supplying a
        # region also prevents Kopia from issuing a GetBucketLocation request,
        # which is unreliable on some S3-compatible gateways such as MinIO.
        region = "us-east-1"
    if region:
        flags.append(f"--region={region}")
    prefix = str(config.get("prefix") or "").strip()
    if prefix:
        flags.append(f"--prefix={prefix}")
    if config.get("use_tls") is False:
        flags.append("--disable-tls")
    try:
        url_style = normalize_s3_url_style(
            config.get("s3_url_style"), platform=repository.s3_platform
        )
    except ValueError as exc:
        raise KopiaCliError(str(exc)) from exc
    if url_style != S3_URL_STYLE_AUTO:
        supports_url_style = _kopia_supports_s3_url_style(_kopia_path())
        if supports_url_style:
            flags.append(
                f"--url-style={kopia_s3_url_style(url_style, platform=repository.s3_platform)}"
            )
        elif url_style == S3_URL_STYLE_VIRTUAL_HOSTED:
            raise KopiaCliError(
                "This repository requires virtual-hosted S3 URLs, but the installed "
                "Kopia binary does not support --url-style. Use the HyperFileLens-built "
                "Kopia artifact mode."
            )
    return flags


def _kopia_supports_s3_url_style(kopia_path: str) -> bool:
    try:
        modified_ns = os.stat(kopia_path).st_mtime_ns
    except OSError:
        modified_ns = 0
    return _probe_kopia_s3_url_style(kopia_path, modified_ns)


@lru_cache(maxsize=8)
def _probe_kopia_s3_url_style(kopia_path: str, _modified_ns: int) -> bool:
    try:
        result = subprocess.run(
            [kopia_path, "repository", "create", "s3", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "--url-style" in f"{result.stdout}\n{result.stderr}"


def _s3_connection_fingerprint(repository: Repository) -> str:
    config = repository.config or {}
    secrets_payload = resolve_repository_secrets(repository)
    payload = {
        "bucket": str(repository.s3_bucket or ""),
        "region": str(config.get("region") or ""),
        "endpoint": repository_control_endpoint(config),
        "prefix": str(config.get("prefix") or ""),
        "access_key_id": str(config.get("access_key_id") or ""),
        "secret_access_key": str(secrets_payload.get("secret_access_key") or ""),
        "kopia_password": str(secrets_payload.get("kopia_password") or ""),
        "use_tls": config.get("use_tls") is not False,
        "s3_url_style": normalize_s3_url_style(
            config.get("s3_url_style"), platform=repository.s3_platform
        ),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _connection_fingerprint_file(config_file: Path) -> Path:
    return config_file.with_name(f"{config_file.name}.connection.sha256")


def _invalidate_changed_s3_connection(repository: Repository, config_file: Path) -> None:
    if repository.repo_type != Repository.Type.S3 or not config_file.exists():
        return
    fingerprint_file = _connection_fingerprint_file(config_file)
    try:
        current = fingerprint_file.read_text(encoding="utf-8").strip()
    except OSError:
        current = ""
    if current == _s3_connection_fingerprint(repository):
        return
    config_file.unlink(missing_ok=True)


def _write_s3_connection_fingerprint(repository: Repository, config_file: Path) -> None:
    fingerprint_file = _connection_fingerprint_file(config_file)
    temporary = fingerprint_file.with_suffix(f"{fingerprint_file.suffix}.tmp")
    temporary.write_text(_s3_connection_fingerprint(repository) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(fingerprint_file)


def _kopia_path() -> str:
    configured = os.environ.get("HFL_KOPIA_PATH")
    if configured:
        return configured
    found = shutil.which("kopia")
    if found:
        return found
    raise KopiaCliError("Kopia CLI not found. Set HFL_KOPIA_PATH or install kopia in PATH.")


@contextmanager
def _repository_config_lock(config_file: Path):
    lock_file = config_file.parent / ".repository.lock"
    lock_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_file.open("a+", encoding="utf-8") as handle:
        timeout_seconds = kopia_config_lock_timeout_seconds()
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise KopiaRepositoryBusyError(
                        "Another Controller operation is still using this repository. "
                        "Please retry after it finishes."
                    ) from exc
                time.sleep(0.1)
        handle.truncate(0)
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _config_file(repository: Repository) -> Path:
    base_dir = Path(os.environ.get("HFL_KOPIA_CONFIG_DIR", "/tmp/hfl-kopia"))
    repo_dir = base_dir / str(repository.id)
    repo_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return repo_dir / "repository.config"


def _maintenance_config_file(repository: Repository) -> Path:
    return _config_file(repository).with_name("maintenance.repository.config")


def _snapshot_delete_config_file(repository: Repository) -> Path:
    return _config_file(repository).with_name("snapshot-delete.repository.config")


def _environment(repository: Repository) -> dict[str, str]:
    config = repository.config or {}
    secrets_payload = resolve_repository_secrets(repository)
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = str(config.get("access_key_id") or "")
    env["AWS_SECRET_ACCESS_KEY"] = str(secrets_payload.get("secret_access_key") or "")
    region = str(config.get("region") or "").strip()
    if region:
        env["AWS_REGION"] = region
        env["AWS_DEFAULT_REGION"] = region
    env["KOPIA_PASSWORD"] = str(secrets_payload.get("kopia_password") or "")
    env["KOPIA_CHECK_FOR_UPDATES"] = "false"
    env["KOPIA_USE_KEYRING"] = "false"
    env["KOPIA_PERSIST_CREDENTIALS_ON_CONNECT"] = "false"
    return env


def _format_failure(message: str, result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if not output:
        return f"{message}: exit code {result.returncode}"
    return f"{message}: exit code {result.returncode}: {output[-1200:]}"


def _repository_already_exists(result: subprocess.CompletedProcess[str]) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    return any(
        token in output
        for token in (
            "already exists",
            "already initialized",
            "repository exists",
            "found existing data in storage location",
        )
    )
