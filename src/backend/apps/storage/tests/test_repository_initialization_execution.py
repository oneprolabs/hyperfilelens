"""Real process/lease checks for initialization after abrupt Worker exit."""

from contextlib import ExitStack, contextmanager
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase

from apps.storage.services.internal.repository_errors import (
    ManagedInitializationError, ManagedInitializationPending,
)
from apps.storage.services.internal.repository_initialization_execution import (
    InitializationExecution, current_initialization_execution, initialization_execution,
)


def _interrupted_worker(root: str, repository_uuid, started: str, done: str) -> None:
    repository = SimpleNamespace(repository_uuid=repository_uuid)
    with patch(
        "apps.storage.services.internal.repository_initialization_execution._root",
        return_value=Path(root),
    ):
        execution = InitializationExecution(repository)
        try:
            execution.acquire()
            execution.run([
                sys.executable, "-c",
                "from pathlib import Path; import time; "
                f"Path({started!r}).touch(); time.sleep(2); Path({done!r}).touch()",
            ], os.environ.copy(), 5)
        finally:
            execution.close()


class RepositoryInitializationExecutionTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = SimpleNamespace(repository_uuid=uuid4(), pk=42)
        self.enterContext(patch(
            "apps.storage.services.internal.repository_initialization_execution._root",
            return_value=self.root,
        ))

    def acquire(self):
        execution = InitializationExecution(self.repository)
        self.addCleanup(execution.close)
        execution.acquire()
        return execution

    def test_execution_journal_identity_is_stable_across_restarts(self):
        first = self.acquire()
        identity = first.identity
        with self.assertRaises(ManagedInitializationPending):
            self.acquire()
        first.close()
        second = self.acquire()
        self.assertEqual(second.identity, identity)
        self.assertEqual(second.path.stat().st_mode & 0o777, 0o600)

    def test_missing_or_corrupt_journal_is_not_termination_evidence(self):
        execution = self.acquire()
        execution.close()
        execution.path.write_text("not-a-journal")
        with self.assertRaises(ManagedInitializationError):
            self.acquire()

    def test_unavailable_persistent_storage_fails_closed(self):
        with patch(
            "apps.storage.services.internal.repository_initialization_execution._root",
            side_effect=OSError("private host detail"),
        ), self.assertRaises(ManagedInitializationError) as raised:
            InitializationExecution(self.repository)
        self.assertIn("persistent execution storage", str(raised.exception))
        self.assertNotIn("private host detail", str(raised.exception))

    def test_supervisor_finishes_and_releases_all_child_leases(self):
        execution = self.acquire()
        command = [sys.executable, "-c", "pass"]
        result = execution.run(command, os.environ.copy(), 3)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        # The parent keeps its reacquired lease for final marker publication.
        with self.assertRaises(ManagedInitializationPending):
            self.acquire()
        execution.close()
        self.acquire()

    def test_supervisor_timeout_stops_and_reaps_the_child_before_unlock(self):
        execution = self.acquire()
        result = execution.run(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            os.environ.copy(), 0.1,
        )
        self.assertEqual(result.returncode, -signal.SIGKILL)
        execution.close()
        self.acquire()

    def test_worker_sigkill_does_not_release_a_live_kopia_execution(self):
        started, done = self.root / "started", self.root / "done"
        worker = multiprocessing.get_context("fork").Process(
            target=_interrupted_worker,
            args=(str(self.root), self.repository.repository_uuid, str(started), str(done)),
        )
        worker.start()
        try:
            deadline = time.monotonic() + 8
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(started.exists(), "The supervised test CLI did not start.")
            os.kill(worker.pid, signal.SIGKILL)
            worker.join(timeout=3)
            self.assertEqual(worker.exitcode, -signal.SIGKILL)
            with self.assertRaises(ManagedInitializationPending):
                self.acquire()
            self.assertFalse(done.exists())
            # No timeout/PID inference: only actually acquiring the inherited
            # kernel lease permits the next initialization to proceed.
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    self.acquire()
                    break
                except ManagedInitializationPending:
                    time.sleep(0.05)
            else:
                self.fail("The supervised CLI did not release its execution lease.")
            self.assertTrue(done.exists())
        finally:
            if worker.is_alive():
                worker.kill()
            worker.join(timeout=3)

    def test_context_restores_previous_execution_even_on_interruption(self):
        @contextmanager
        def lock(**kwargs):
            yield True

        with patch(
            "apps.storage.services.internal.repository_initialization_execution.repository_execution_lock",
            side_effect=lock,
        ):
            self.assertIsNone(current_initialization_execution())
            with self.assertRaises(KeyboardInterrupt):
                with initialization_execution(self.repository) as execution:
                    self.assertIs(current_initialization_execution(), execution)
                    raise KeyboardInterrupt
            self.assertIsNone(current_initialization_execution())
            self.acquire()

    def test_changed_journal_after_process_completion_is_rejected(self):
        execution = self.acquire()
        with patch(
            "apps.storage.services.internal.repository_initialization_execution.subprocess.Popen",
        ) as popen:
            process = popen.return_value
            process.communicate.side_effect = lambda **kwargs: execution.path.write_text(str(uuid4()))
            process.poll.return_value = 0
            with self.assertRaises(ManagedInitializationError):
                execution.run([sys.executable, "-c", "pass"], os.environ.copy(), 3)

    def test_normal_commands_keep_the_existing_process_path(self):
        from apps.storage.services.internal.kopia_cli import _run_repository_process

        repository = SimpleNamespace(config={}, id=42)
        with ExitStack() as stack:
            stack.enter_context(patch(
                "apps.storage.services.internal.kopia_cli._kopia_path", return_value="/kopia",
            ))
            stack.enter_context(patch(
                "apps.storage.services.internal.kopia_cli._config_file", return_value=self.root / "repository.config",
            ))
            stack.enter_context(patch(
                "apps.storage.services.internal.kopia_cli._invalidate_changed_s3_connection",
            ))
            stack.enter_context(patch(
                "apps.storage.services.internal.kopia_cli._environment", return_value={},
            ))
            popen = stack.enter_context(patch(
                "apps.storage.services.internal.kopia_cli.subprocess.Popen",
            ))
            process = popen.return_value
            process.communicate.return_value = ("ordinary output", "")
            process.returncode = 0
            result = _run_repository_process(repository, ["repository", "status"], timeout_seconds=3)
        self.assertEqual(result.stdout, "ordinary output")
        popen.assert_called_once()

    def test_only_trusted_initialization_context_selects_supervision(self):
        from apps.storage.services.internal.kopia_cli import _run_repository_process

        repository = SimpleNamespace(config={}, id=42)
        execution = Mock()
        execution.run.return_value = subprocess.CompletedProcess(["/kopia"], 0, "", "")
        with ExitStack() as stack:
            for name, value in (
                ("_kopia_path", "/kopia"),
                ("_config_file", self.root / "repository.config"),
                ("_environment", {}),
            ):
                stack.enter_context(patch(
                    f"apps.storage.services.internal.kopia_cli.{name}", return_value=value,
                ))
            stack.enter_context(patch(
                "apps.storage.services.internal.kopia_cli._invalidate_changed_s3_connection",
            ))
            stack.enter_context(patch(
                "apps.storage.services.internal.repository_initialization_execution.current_initialization_execution",
                return_value=execution,
            ))
            popen = stack.enter_context(patch(
                "apps.storage.services.internal.kopia_cli.subprocess.Popen",
            ))
            _run_repository_process(repository, ["repository", "status"], timeout_seconds=3)
        execution.run.assert_called_once()
        popen.assert_not_called()
