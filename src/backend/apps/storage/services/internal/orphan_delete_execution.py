"""Durable Controller deletion and positive execution-termination evidence."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from datetime import timedelta
import fcntl
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

from django.db import connection, transaction
from django.utils.crypto import salted_hmac
from django.utils import timezone

from apps.storage.repositories.models import (
    OrphanSnapshotDeleteAttempt, Repository, RepositoryExecutionTarget,
    RepositoryOrphanSnapshot, RepositoryTask,
)
from apps.storage.services.internal.orphan_reconciliation_errors import OrphanReconciliationError
from apps.task.models import Task

_execution = ContextVar("orphan_delete_execution", default=None)


def repository_delete_revision(repository: Repository) -> str:
    """Fence deletion identity without treating quota edits as storage changes.

    Only known non-execution settings are excluded. Unknown/new config keys
    remain fenced so a future connection option cannot silently escape checks.
    Usage observations keep their separate, stricter revision semantics.
    """
    config = repository.config if isinstance(repository.config, dict) else {}
    quota_keys = {"quota_gb", "quota_unit", "quota_alert_enabled", "quota_alert_threshold"}
    identity = {
        "repository_uuid": str(repository.repository_uuid),
        "organization_id": repository.organization_id,
        "repo_type": repository.repo_type,
        "status": repository.status,
        "config": {key: value for key, value in config.items() if key not in quota_keys},
        "credential_id": repository.credential_id,
        "nas_protocol": repository.nas_protocol,
        "bind_node_type": repository.bind_node_type,
        "bind_node_id": repository.bind_node_id,
        "s3_platform": repository.s3_platform,
        "s3_bucket": repository.s3_bucket,
    }
    return salted_hmac(
        "hyperfilelens.repository-delete-revision",
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str),
        algorithm="sha256",
    ).hexdigest()


def _require_local_filesystem(root: Path) -> None:
    """Network lock leases can expire while a remote deleter is still alive."""
    resolved = root.resolve()
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        mount = Path(re.sub(
            r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), before.split()[4]
        ))
        if resolved == mount or mount in resolved.parents:
            mounts.append((len(mount.parts), after.split()[0]))
    if not mounts or max(mounts)[1] not in {"ext4", "xfs", "btrfs", "overlay", "zfs"}:
        raise OSError("Controller execution journals require a shared local filesystem.")


def _root() -> Path:
    # Compose mounts a persistent local volume shared by Controller Workers.
    root = Path(os.getenv("HFL_ORPHAN_EXECUTION_DIR", "").strip()
                or "/var/log/hyperfilelens/.orphan-executions")
    if not root.is_absolute():
        raise OSError("The shared execution journal directory must be absolute.")
    _require_local_filesystem(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A named volume is already a durable mount root. Do not require fsync on
    # its parent logs bind mount, which may be a different/virtual filesystem.
    sync_directory = root if root.is_mount() else root.parent
    directory_fd = os.open(sync_directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return root


def _open_lease(path: Path, *, create: bool = False) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    return os.open(path, flags, 0o600)


def _error(message: str) -> OrphanReconciliationError:
    return OrphanReconciliationError(
        "ORPHAN_EXECUTION_CONFIRMING", message, execution_unknown=True
    )


class ControllerDelete:
    def __init__(self, rt: RepositoryTask, candidate: RepositoryOrphanSnapshot) -> None:
        self.rt = rt
        self.candidate = candidate
        self.revision = repository_delete_revision(rt.repository)
        self.attempt = None
        self.outcome = ""

    def run(self, command: list[str], env: dict[str, str],
            timeout: float) -> subprocess.CompletedProcess[str]:
        """Persist intent before spawning; never let a Worker own CLI lifetime."""
        from apps.storage.services.internal.orphan_snapshot_fence import backup_blocker
        from apps.storage.services.internal.orphan_snapshots import managed_ids, pending_operation

        if connection.in_atomic_block:
            raise OrphanReconciliationError(
                "ORPHAN_DELETE_TRANSACTION_OPEN",
                "Deletion cannot start before its intent transaction is committed.",
            )
        if (
            not re.fullmatch(r"[0-9a-f]{32}", self.candidate.snapshot_id)
            or command[-4:] != ["snapshot", "delete", self.candidate.snapshot_id, "--delete"]
        ):
            raise OrphanReconciliationError(
                "ORPHAN_AGENT_IDENTITY_MISMATCH",
                "The deletion command does not match its snapshot intent.",
            )
        attempt_id = uuid4()
        root = _root()
        fd = _open_lease(root / f"{attempt_id}.lease", create=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.write(fd, str(attempt_id).encode())
            os.fsync(fd)
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            with transaction.atomic():
                repository = Repository.objects.select_for_update().get(
                    pk=self.rt.repository_id
                )
                rt = RepositoryTask.objects.select_for_update().get(pk=self.rt.pk)
                task = Task.objects.select_for_update().get(pk=rt.task_id)
                target = RepositoryExecutionTarget.objects.select_for_update().get(
                    pk=rt.execution_target_id
                )
                candidate = RepositoryOrphanSnapshot.objects.select_for_update().get(
                    pk=self.candidate.pk
                )
                if (
                    task.status not in {Task.Status.RUNNING, Task.Status.WAITING}
                    or task.organization_id != repository.organization_id
                    or target.organization_id != repository.organization_id
                    or target.active_task_id != task.id
                    or candidate.target_id != target.id or candidate.status != "candidate"
                    or repository_delete_revision(repository) != self.revision
                    or backup_blocker(repository)
                    or candidate.snapshot_id in managed_ids(repository)
                    or pending_operation(repository, candidate.operation_id)
                ):
                    raise OrphanReconciliationError(
                        "ORPHAN_DELETE_PRECONDITION_CHANGED",
                        "Deletion prerequisites changed; no deletion was started.",
                    )
                self.attempt = OrphanSnapshotDeleteAttempt.objects.create(
                    id=attempt_id, candidate=candidate, repository_task=rt,
                    observation_revision=self.revision, journal_root=str(root),
                    executor_identity=socket.gethostname(),
                )
                candidate.status = "deleting"
                candidate.save(update_fields=["status"])
                rt.execution_token = attempt_id
                rt.execution_heartbeat_at = timezone.now()
                rt.save(update_fields=["execution_token", "execution_heartbeat_at"])
            runner = Path(__file__).with_name("orphan_delete_runner.py")
            process = subprocess.Popen(
                [sys.executable, str(runner), "--lease-fd", str(fd),
                 "--receipt", str(root / f"{attempt_id}.json"),
                 "--attempt", str(attempt_id), "--timeout", str(timeout)],
                env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, pass_fds=(fd,), start_new_session=True,
            )
            # Worker death closes only its copy of fd/stdin. The independent
            # supervisor and CLI retain the lease and bounded execution.
            request = json.dumps({"command": command}).encode()
            deadline = time.monotonic() + timeout + 20
            heartbeat_at = time.monotonic()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout + 20)
                try:
                    process.communicate(input=request, timeout=min(1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    request = None
                    if time.monotonic() - heartbeat_at >= 10:
                        valid = RepositoryTask.objects.filter(
                            pk=self.rt.id, execution_token=attempt_id,
                            task__status__in=[Task.Status.RUNNING, Task.Status.WAITING],
                        ).update(execution_heartbeat_at=timezone.now())
                        heartbeat_at = time.monotonic()
                        if not valid:
                            process.terminate()  # Supervisor stops/reaps its CLI.
                            raise _error("Deletion authority changed; execution is being stopped.")
            receipt = json.loads((root / f"{attempt_id}.json").read_text())
            if receipt.get("attempt") != str(attempt_id) or receipt.get("phase") != "finished":
                raise _error("The deletion result identity could not be verified.")
            code = int(receipt["exit_code"])
            self.outcome = str(receipt.get("outcome") or "")
            os.close(fd)
            fd = None
            with confirmed_stopped(self.attempt) as stopped:
                if not stopped:
                    raise _error("Deletion descendants have not confirmed termination.")
                OrphanSnapshotDeleteAttempt.objects.filter(
                    pk=attempt_id, state="prepared"
                ).update(state="finished", exit_code=code, finished_at=timezone.now())
            return subprocess.CompletedProcess(command, code, "", "")
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            if self.attempt is not None:
                raise _error("Automatically confirming Controller deletion execution.") from exc
            raise
        finally:
            if fd is not None:
                os.close(fd)


@contextmanager
def controller_delete_execution(
    rt: RepositoryTask, candidate: RepositoryOrphanSnapshot
) -> Iterator[ControllerDelete]:
    execution = ControllerDelete(rt, candidate)
    token = _execution.set(execution)
    try:
        yield execution
    finally:
        _execution.reset(token)


def current_delete_execution() -> ControllerDelete | None:
    return _execution.get()


@contextmanager
def controller_result_registration(
    rt: RepositoryTask, execution: ControllerDelete | None
) -> Iterator[None]:
    """Fence late results inside the same transaction as candidate mutation."""
    with transaction.atomic():
        repository = Repository.objects.select_for_update().get(pk=rt.repository_id)
        operation = RepositoryTask.objects.select_for_update().get(pk=rt.pk)
        task = Task.objects.select_for_update().get(pk=rt.task_id)
        target = RepositoryExecutionTarget.objects.select_for_update().get(
            pk=rt.execution_target_id
        )
        if (
            task.status not in {Task.Status.RUNNING, Task.Status.WAITING}
            or target.active_task_id != task.id
            or execution and execution.attempt
            and operation.execution_token != execution.attempt.id
            or execution
            and repository_delete_revision(repository) != execution.revision
        ):
            raise _error("A late Controller result cannot change the current operation.")
        yield


@contextmanager
def confirmed_stopped(attempt: OrphanSnapshotDeleteAttempt) -> Iterator[bool]:
    """A positive shared-file lease proof, not TTL/PID/heartbeat inference."""
    fd = None
    acquired = False
    try:
        root = Path(attempt.journal_root)
        _require_local_filesystem(root)
        path = root / f"{attempt.pk}.lease"
        fd = _open_lease(path)
        if os.read(fd, 64).decode() != str(attempt.pk):
            acquired = False
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
    except (OSError, UnicodeError):
        acquired = False  # Missing/unreachable journal is not evidence of termination.
    try:
        yield acquired
    finally:
        if fd is not None:
            os.close(fd)


def controller_execution_pending(rt: RepositoryTask) -> bool:
    """Avoid storage scans while a bounded executor is still running."""
    for candidate in RepositoryOrphanSnapshot.objects.filter(
        target=rt.execution_target, status="deleting"
    ):
        attempt = candidate.delete_attempts.order_by("-created_at").first()
        if attempt is None:
            return True
        with confirmed_stopped(attempt) as stopped:
            if not stopped:
                return True
    return False


def recover_controller_deletions(
    rt: RepositoryTask, inventory: list[dict],
    reason: str = "Controller execution ended; snapshot state recovered without redispatching deletion.",
) -> bool:
    """Resolve stopped attempts using fresh inventory; never dispatch deletion."""
    from apps.storage.services.internal.orphan_snapshots import managed_ids, pending_operation, _record

    present = {item["snapshot_id"] for item in inventory}
    candidates = RepositoryOrphanSnapshot.objects.filter(
        target=rt.execution_target, status="deleting"
    )
    recovered = False
    for candidate in candidates:
        attempt = candidate.delete_attempts.order_by("-created_at").first()
        if attempt is None:
            raise _error("Automatically checking legacy deletion execution; no completion evidence is available.")
        with confirmed_stopped(attempt) as stopped:
            if not stopped:
                raise _error("Automatically waiting for the bounded deletion executor to stop.")
            with transaction.atomic():
                repository = Repository.objects.select_for_update().get(pk=rt.repository_id)
                operation = RepositoryTask.objects.select_for_update().get(pk=rt.pk)
                task = Task.objects.select_for_update().get(pk=rt.task_id)
                target = RepositoryExecutionTarget.objects.select_for_update().get(pk=rt.execution_target_id)
                candidate = RepositoryOrphanSnapshot.objects.select_for_update().get(pk=candidate.pk)
                attempt = OrphanSnapshotDeleteAttempt.objects.select_for_update().get(pk=attempt.pk)
                if (
                    target.active_task_id != task.id
                    or task.status not in {Task.Status.RUNNING, Task.Status.WAITING}
                    or attempt.repository_task_id != operation.id
                    or operation.execution_token != attempt.id
                    or repository_delete_revision(repository) != attempt.observation_revision
                ):
                    raise _error("Deletion recovery identity changed; automatic verification will retry.")
                protected = (
                    candidate.snapshot_id in managed_ids(repository)
                    or pending_operation(repository, candidate.operation_id)
                )
                if protected and candidate.snapshot_id not in present:
                    raise _error("A referenced snapshot is missing from the verified inventory.")
                now = timezone.now()
                candidate.status = (
                    "managed" if protected else "candidate"
                    if candidate.snapshot_id in present else "absent"
                )
                if candidate.status == "candidate":
                    candidate.delete_not_before = now + timedelta(hours=24)
                candidate.last_error = reason
                candidate.save(update_fields=["status", "delete_not_before", "last_error"])
                attempt.state = "recovered"
                attempt.finished_at = now
                attempt.save(update_fields=["state", "finished_at"])
                _record(rt, candidate, "failed" if candidate.status == "candidate" else candidate.status,
                        candidate.last_error)
                recovered = True
    return recovered
