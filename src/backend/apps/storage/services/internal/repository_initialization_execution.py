"""Small initialization fence reusing the Controller's durable CLI supervisor."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Callable, Iterator
from uuid import UUID, uuid4

from apps.storage.services.internal.orphan_delete_execution import _open_lease, _root
from apps.storage.services.internal.repository_errors import (
    ManagedInitializationError,
    ManagedInitializationPending,
)
from apps.storage.services.internal.repository_execution_lock import repository_execution_lock

if TYPE_CHECKING:
    from apps.storage.repositories.models import Repository

_execution = ContextVar("repository_initialization_execution", default=None)
_PENDING_MESSAGE = (
    "A previous Managed Repository initialization is still running. "
    "Waiting for its execution to stop before retrying."
)


class InitializationExecution:
    """Keep one kernel lease in the Worker, independent supervisor and Kopia."""

    def __init__(self, repository: Repository) -> None:
        try:
            self.root = _root()
        except OSError:
            raise ManagedInitializationError(
                "Controller persistent execution storage is unavailable. "
                "Check its shared local volume before retrying initialization."
            ) from None
        self.path = self.root / f"initialization-{repository.repository_uuid}.lease"
        self.claim_path = self.root / f"initialization-{repository.repository_uuid}.claim.json"
        self.fd: int | None = None
        self.identity = ""

    def acquire(self) -> None:
        if self.fd is not None:
            raise ManagedInitializationError("Initialization execution is already acquired.")
        try:
            try:
                self.fd = _open_lease(self.path, create=True)
                created = True
            except FileExistsError:
                self.fd = _open_lease(self.path)
                created = False
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if created:
                os.write(self.fd, str(uuid4()).encode())
                os.fsync(self.fd)
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            os.lseek(self.fd, 0, os.SEEK_SET)
            identity = str(UUID(os.read(self.fd, 64).decode()))
            if self.identity and identity != self.identity:
                self.close()
                raise ManagedInitializationError(
                    "The initialization execution journal changed. Data was retained; "
                    "review the Controller execution storage before retrying."
                )
            self.identity = identity
        except BlockingIOError:
            self.close()
            raise ManagedInitializationPending(_PENDING_MESSAGE) from None
        except (OSError, ValueError, UnicodeError):
            self.close()
            raise ManagedInitializationError(
                "The initialization execution journal could not be verified. "
                "Data was retained; check the Controller execution storage."
            ) from None

    def close(self) -> None:
        if self.fd is not None:
            # No LOCK_UN: inherited descriptors may still belong to a live
            # supervisor/Kopia. Closing only our copy preserves that fence.
            os.close(self.fd)
            self.fd = None

    def run(
        self, command: list[str], env: dict[str, str], timeout: float, *,
        renew_identity: Callable[[], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if self.fd is None:
            raise ManagedInitializationError("Initialization execution is not acquired.")
        attempt = str(uuid4())
        receipt = self.root / f"initialization-{attempt}.json"
        runner = Path(__file__).with_name("orphan_delete_runner.py")
        process = None
        try:
            if renew_identity is not None:
                renew_identity()
            process = subprocess.Popen(
                [sys.executable, str(runner), "--lease-fd", str(self.fd),
                 "--receipt", str(receipt), "--attempt", attempt,
                 "--timeout", str(timeout)],
                env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, pass_fds=(self.fd,),
                start_new_session=True,
            )
            request = json.dumps({"command": command}).encode()
            deadline = time.monotonic() + timeout + 20
            while True:
                if renew_identity is not None:
                    renew_identity()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ManagedInitializationPending(_PENDING_MESSAGE)
                try:
                    process.communicate(input=request, timeout=min(1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    request = None
            # A completion receipt alone is not termination proof. Reacquire
            # the same durable lease after dropping our inherited copy. If any
            # descendant remains, or another Worker won, no result is promoted.
            self.close()
            self.acquire()
            result = json.loads(receipt.read_text())
            if (
                result.get("attempt") != attempt or result.get("phase") != "finished"
                or type(result.get("exit_code")) is not int
            ):
                raise ManagedInitializationError(
                    "The initialization execution result could not be verified. "
                    "Data was retained; retry after checking Controller execution."
                )
            receipt.unlink()
            return subprocess.CompletedProcess(command, result["exit_code"], "", "")
        except BaseException:
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    raise ManagedInitializationPending(_PENDING_MESSAGE) from None
            raise


@contextmanager
def initialization_execution(repository: Repository) -> Iterator[InitializationExecution]:
    # Repository-scoped, not attempt-scoped: a later Retry uses the same fence.
    with repository_execution_lock(
        operation="managed-initialization", operation_id=repository.pk,
    ) as acquired:
        if not acquired:
            raise ManagedInitializationPending(_PENDING_MESSAGE)
        execution = InitializationExecution(repository)
        try:
            execution.acquire()
            token = _execution.set(execution)
            try:
                yield execution
            finally:
                _execution.reset(token)
        finally:
            execution.close()


def current_initialization_execution() -> InitializationExecution | None:
    return _execution.get()
