"""Controller failures recover automatically without weakening deletion fencing."""

from datetime import timedelta
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TransactionTestCase, SimpleTestCase
from django.utils import timezone

from apps.storage.repositories.models import (
    OrphanSnapshotDeleteAttempt, Repository, RepositoryOrphanSnapshot, RepositoryTask,
)
from apps.storage.services.internal import orphan_delete_runner
from apps.storage.services.internal.orphan_delete_execution import (
    ControllerDelete, confirmed_stopped, _open_lease, _require_local_filesystem,
    _root, controller_result_registration, recover_controller_deletions,
    repository_delete_revision,
)
from apps.storage.services.internal.kopia_cli import KopiaCliError
from apps.storage.services.internal.orphan_reconciliation_errors import OrphanReconciliationError
from apps.storage.services.internal.orphan_snapshot_fence import assert_no_orphan_cleanup
from apps.storage.services.internal.orphan_snapshots import _finish, _record, run_orphan_reconciliation
from apps.storage.services.internal.repository_operations import (
    create_repository_operation_task, discover_repository_execution_targets,
)
from apps.storage.services.internal.repository_usage import repository_observation_revision
from apps.storage.services.interface import update_repository
from apps.storage.tests import test_repository_tasks as fixtures
from apps.task.models import Task

SID = "a" * 32
ITEM = {"snapshot_id": SID, "source": {}, "operation_id": ""}


class ControllerRecoveryTests(TransactionTestCase):
    def setUp(self):
        fixtures.RepositoryTaskTests.setUp(self)
        discover_repository_execution_targets()
        self.target = self.repository.execution_targets.get()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {"HFL_ORPHAN_EXECUTION_DIR": self.directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.candidate = RepositoryOrphanSnapshot.objects.create(
            target=self.target, snapshot_id=SID, source={},
            first_seen_at=timezone.now() - timedelta(days=3),
            last_seen_at=timezone.now(),
        )
        self.original_discovery = self.candidate.first_seen_at
        self.rt = create_repository_operation_task(
            target_id=self.target.id,
            operation_type=RepositoryTask.OperationType.SNAPSHOT_RECONCILE,
        )
        Task.objects.filter(pk=self.rt.task_id).update(status=Task.Status.RUNNING)
        self.rt.task.refresh_from_db()

    def stopped_attempt(self, *, locked=False):
        attempt = OrphanSnapshotDeleteAttempt.objects.create(
            candidate=self.candidate, repository_task=self.rt,
            observation_revision=repository_delete_revision(self.repository),
            journal_root=self.directory.name, executor_identity="test-worker",
        )
        fd = _open_lease(Path(self.directory.name) / f"{attempt.id}.lease", create=True)
        os.write(fd, str(attempt.id).encode())
        if locked:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.addCleanup(os.close, fd)
        else:
            os.close(fd)
        self.candidate.status = "deleting"
        self.candidate.save(update_fields=["status"])
        RepositoryTask.objects.filter(pk=self.rt.pk).update(execution_token=attempt.id)
        return attempt

    def advance_past_stale_recovery(self) -> tuple[RepositoryOrphanSnapshot, ControllerDelete]:
        """Interleave two advances after loss of the shared cache lock."""
        first = self.stopped_attempt()
        first.state, first.exit_code, first.finished_at = "finished", 0, timezone.now()
        first.save(update_fields=["state", "exit_code", "finished_at"])
        second = RepositoryOrphanSnapshot.objects.create(
            target=self.target, snapshot_id="b" * 32, source={},
            first_seen_at=timezone.now() - timedelta(days=3),
            last_seen_at=timezone.now(),
        )
        # B recovers the stopped first command, then pauses before finalizing.
        self.assertTrue(recover_controller_deletions(self.rt, [{
            "snapshot_id": second.snapshot_id, "source": {}, "operation_id": "",
        }]))
        # A registers its valid first result and moves to the second snapshot.
        previous = SimpleNamespace(attempt=first, revision=first.observation_revision)
        with controller_result_registration(self.rt, previous):
            self.candidate.status = "deleted"
            self.candidate.save(update_fields=["status"])
            _record(self.rt, self.candidate, "deleted")
        execution = ControllerDelete(self.rt, second)
        original_popen = subprocess.Popen
        observed = {}

        def launch_then_finish_old_recovery(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            # Intent two is committed and its executor owns the lease when B
            # resumes the stale finalization of recovery one.
            observed["result"] = _finish(
                self.rt, False, "Previous command recovered",
                scanned=2, error_code="ORPHAN_DELETE_RECOVERED",
            )
            self.rt.task.refresh_from_db()
            self.target.refresh_from_db()
            observed["status"] = self.rt.task.status
            observed["active_task"] = self.target.active_task_id
            return process

        with patch(
            "apps.storage.services.internal.orphan_delete_execution.subprocess.Popen",
            side_effect=launch_then_finish_old_recovery,
        ):
            result = execution.run(
                [self.fake_cli("pass"), "snapshot", "delete", second.snapshot_id, "--delete"],
                os.environ.copy(), 3,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(observed, {
            "result": {"status": "recovering"},
            "status": Task.Status.RUNNING, "active_task": self.rt.task_id,
        })
        second.refresh_from_db()
        self.assertEqual(second.status, "deleting")
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)
        return second, execution

    def test_stale_recovery_finish_does_not_strand_next_delete_result(self):
        second, execution = self.advance_past_stale_recovery()
        with controller_result_registration(self.rt, execution):
            second.status = "deleted"
            second.deleted_at = timezone.now()
            second.save(update_fields=["status", "deleted_at"])
            _record(self.rt, second, "deleted")
        self.assertEqual(_finish(self.rt, True, scanned=2), {"status": "success"})
        self.target.refresh_from_db()
        self.assertIsNone(self.target.active_task_id)
        assert_no_orphan_cleanup(self.repository.id)

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[])
    def test_stale_recovery_finish_preserves_recovery_of_next_lost_result(self, inventory):
        second, execution = self.advance_past_stale_recovery()
        # A disappears after command two completes, without registering it.
        RepositoryTask.objects.filter(pk=self.rt.pk).update(
            due_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        second.refresh_from_db()
        execution.attempt.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(second.status, "absent")
        self.assertEqual(execution.attempt.state, "recovered")
        self.assertEqual(second.delete_attempts.count(), 1)
        self.assertIsNone(self.target.active_task_id)
        assert_no_orphan_cleanup(self.repository.id)

    @patch("apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots")
    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[ITEM])
    def test_stopped_execution_with_present_snapshot_fails_cycle_and_releases_gate(self, inventory, delete):
        attempt = self.stopped_attempt()
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        self.candidate.refresh_from_db()
        attempt.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.candidate.status, "candidate")
        self.assertEqual(self.candidate.first_seen_at, self.original_discovery)
        self.assertGreater(self.candidate.delete_not_before, timezone.now() + timedelta(hours=23))
        self.assertEqual(attempt.state, "recovered")
        self.assertIsNone(self.target.active_task_id)
        assert_no_orphan_cleanup(self.repository.id)
        delete.assert_not_called()
        self.assertFalse(Task.objects.filter(replaces_task=self.rt.task).exists())

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[])
    def test_stopped_execution_with_absent_snapshot_can_release(self, inventory):
        self.stopped_attempt()
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "absent")
        assert_no_orphan_cleanup(self.repository.id)

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory")
    def test_live_execution_is_not_recovered_or_redispatched(self, inventory):
        attempt = self.stopped_attempt(locked=True)
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        self.rt.task.refresh_from_db()
        self.assertEqual(self.rt.task.status, Task.Status.RUNNING)
        self.assertEqual(self.rt.task.error_code, "ORPHAN_EXECUTION_CONFIRMING")
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)
        inventory.assert_not_called()
        with confirmed_stopped(attempt) as stopped:
            self.assertFalse(stopped)

    def test_missing_journal_is_not_termination_evidence(self):
        attempt = self.stopped_attempt()
        (Path(self.directory.name) / f"{attempt.id}.lease").unlink()
        with confirmed_stopped(attempt) as stopped:
            self.assertFalse(stopped)

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory")
    @patch("apps.storage.tasks.execute_repository_operation.apply_async")
    def test_periodic_recovery_respects_backoff(self, enqueue, inventory):
        from apps.storage.tasks import reconcile_repository_operations

        self.stopped_attempt(locked=True)
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        self.assertEqual(reconcile_repository_operations()["redispatched"], 0)
        enqueue.assert_not_called()
        RepositoryTask.objects.filter(pk=self.rt.pk).update(
            due_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(reconcile_repository_operations()["redispatched"], 1)
        enqueue.assert_called_once_with(kwargs={"repository_task_id": self.rt.id})

    def test_late_recovery_cannot_revive_terminal_task(self):
        from apps.storage.services.internal.orphan_snapshots import _wait

        Task.objects.filter(pk=self.rt.task_id).update(status=Task.Status.FAILED)
        self.assertEqual(_wait(self.rt)["status"], Task.Status.FAILED)
        self.rt.task.refresh_from_db()
        self.assertEqual(self.rt.task.status, Task.Status.FAILED)

    @patch("apps.storage.services.internal.orphan_snapshots.task_updated.send_robust")
    def test_recovery_notification_reports_current_status(self, notify):
        from apps.storage.services.internal.orphan_snapshots import _notify_recovery

        Task.objects.filter(pk=self.rt.task_id).update(status=Task.Status.FAILED)
        _notify_recovery(self.rt.task_id)
        self.assertEqual(notify.call_args.kwargs["status"], Task.Status.FAILED)

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory")
    def test_legacy_deleting_is_not_automatically_treated_as_safe(self, inventory):
        self.candidate.status = "deleting"
        self.candidate.save()
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        inventory.assert_not_called()
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[ITEM])
    def test_changed_repository_cannot_use_old_observation(self, inventory):
        self.stopped_attempt()
        self.repository.config = {**self.repository.config, "prefix": "another-location"}
        self.repository.save(update_fields=["config"])
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "deleting")

    @patch("apps.storage.services.interface.enqueue_repository_usage_refresh")
    @patch("apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots")
    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[ITEM])
    def test_quota_edit_does_not_block_lost_result_recovery(self, inventory, delete, refresh):
        attempt = self.stopped_attempt()
        self.repository = update_repository(
            repository=self.repository,
            config={"quota_gb": 20, "quota_unit": "TB",
                    "quota_alert_enabled": True, "quota_alert_threshold": 85},
        )
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        self.candidate.refresh_from_db()
        self.target.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(self.candidate.status, "candidate")
        self.assertGreater(self.candidate.delete_not_before, timezone.now() + timedelta(hours=23))
        self.assertEqual(attempt.state, "recovered")
        self.assertIsNone(self.target.active_task_id)
        assert_no_orphan_cleanup(self.repository.id)
        delete.assert_not_called()
        refresh.assert_called_once()

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[ITEM])
    def test_replaced_execution_token_cannot_be_finalized_by_old_attempt(self, inventory):
        self.stopped_attempt()
        RepositoryTask.objects.filter(pk=self.rt.pk).update(execution_token=uuid4())
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "deleting")

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[ITEM])
    @patch("apps.storage.services.internal.orphan_snapshots.managed_ids", return_value={SID})
    def test_restored_reference_is_protected_after_execution_ends(self, managed, inventory):
        self.stopped_attempt()
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "managed")
        assert_no_orphan_cleanup(self.repository.id)

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[])
    @patch("apps.storage.services.internal.orphan_snapshots.managed_ids", return_value={SID})
    def test_missing_referenced_snapshot_is_not_hidden(self, managed, inventory):
        self.stopped_attempt()
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)

    def test_recovery_grace_does_not_allow_immediate_redeletion(self):
        self.stopped_attempt()
        with patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[ITEM]):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
            following = create_repository_operation_task(
                target_id=self.target.id,
                operation_type=RepositoryTask.OperationType.SNAPSHOT_RECONCILE,
            )
            with patch("apps.storage.services.internal.orphan_snapshots.delete_s3_snapshots") as delete:
                self.assertEqual(run_orphan_reconciliation(following.id)["status"], "success")
            delete.assert_not_called()
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.first_seen_at, self.original_discovery)

    @patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[ITEM])
    def test_repeated_recovery_does_not_change_terminal_history(self, inventory):
        self.stopped_attempt()
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        before = list(self.rt.task.events.values())
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        self.assertEqual(list(self.rt.task.events.values()), before)

    def test_actual_supervised_command_records_intent_and_completion(self):
        execution = ControllerDelete(self.rt, self.candidate)
        result = execution.run(
            [sys.executable, "-c", "pass", "snapshot", "delete", SID, "--delete"],
            os.environ.copy(), 2,
        )
        self.assertEqual(result.returncode, 0)
        attempt = execution.attempt
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, "finished")
        self.assertEqual(attempt.exit_code, 0)
        with confirmed_stopped(attempt) as stopped:
            self.assertTrue(stopped)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "deleting")  # Outcome still needs registration.

    def test_uncommitted_intent_cannot_launch_a_physical_delete(self):
        execution = ControllerDelete(self.rt, self.candidate)
        with transaction.atomic(), self.assertRaisesMessage(
            OrphanReconciliationError, "intent transaction is committed"
        ):
            execution.run(
                ["unused", "snapshot", "delete", SID, "--delete"], os.environ.copy(), 2
            )
        self.assertFalse(OrphanSnapshotDeleteAttempt.objects.exists())

    @patch("apps.storage.services.internal.orphan_delete_execution.subprocess.Popen", side_effect=OSError)
    def test_crash_before_supervisor_start_is_automatically_recoverable(self, launch):
        execution = ControllerDelete(self.rt, self.candidate)
        with self.assertRaises(OrphanReconciliationError):
            execution.run(
                ["unused", "snapshot", "delete", SID, "--delete"], os.environ.copy(), 2
            )
        with confirmed_stopped(execution.attempt) as stopped:
            self.assertTrue(stopped)
        with patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory", return_value=[ITEM]):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        assert_no_orphan_cleanup(self.repository.id)

    def fake_cli(self, body):
        path = Path(self.directory.name) / "fake-kopia"
        path.write_text(f"#!{sys.executable}\n{body}\n")
        path.chmod(0o700)
        return str(path)

    def cli_environment(self):
        """Patch preparation, not execution: run the real supervisor path."""
        from contextlib import ExitStack

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {
            "HFL_KOPIA_CONFIG_DIR": self.directory.name,
            "HFL_KOPIA_TIMEOUT_SECONDS": "1",
            "STORAGE_ORPHAN_SNAPSHOT_DELETE_ENABLED": "true",
        }))
        stack.enter_context(patch(
            "apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory",
            return_value=[ITEM],
        ))
        stack.enter_context(patch(
            "apps.storage.services.internal.repository_ownership.verify_s3_repository_ownership"
        ))
        stack.enter_context(patch(
            "apps.storage.services.internal.kopia_cli._connect_maintenance_repository"
        ))
        stack.enter_context(patch(
            "apps.storage.services.internal.kopia_cli._invalidate_changed_s3_connection"
        ))

    def test_end_to_end_timeout_fails_and_releases_without_manual_recovery(self):
        self.cli_environment()
        with patch("apps.storage.services.internal.kopia_cli._kopia_path",
                   return_value=self.fake_cli("import time; time.sleep(60)")):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        self.candidate.refresh_from_db()
        self.rt.task.refresh_from_db()
        self.assertEqual(self.candidate.status, "candidate")
        self.assertIn("timed out", self.candidate.last_error)
        self.assertEqual(self.candidate.first_seen_at, self.original_discovery)
        self.assertEqual(self.rt.task.status, Task.Status.FAILED)
        attempt = OrphanSnapshotDeleteAttempt.objects.get()
        self.assertEqual(attempt.exit_code, -signal.SIGKILL)
        with confirmed_stopped(attempt) as stopped:
            self.assertTrue(stopped)
        assert_no_orphan_cleanup(self.repository.id)

    def test_end_to_end_success_records_deleted_only_after_execution_finishes(self):
        self.cli_environment()
        with patch("apps.storage.services.internal.kopia_cli._kopia_path",
                   return_value=self.fake_cli("pass")):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "success")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "deleted")
        self.assertEqual(OrphanSnapshotDeleteAttempt.objects.get().state, "finished")
        assert_no_orphan_cleanup(self.repository.id)

    def test_supervisor_does_not_persist_raw_cli_output(self):
        self.cli_environment()
        with patch(
            "apps.storage.services.internal.kopia_cli._kopia_path",
            return_value=self.fake_cli(
                "import sys\nprint('AWS_SECRET_ACCESS_KEY=DO_NOT_LOG')\n"
                "print('password=DO_NOT_LOG', file=sys.stderr)\nraise SystemExit(1)"
            ),
        ):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        for path in Path(self.directory.name).glob("*.json"):
            self.assertNotIn("DO_NOT_LOG", path.read_text())
        self.rt.task.refresh_from_db()
        self.assertNotIn("DO_NOT_LOG", str(self.rt.task.result_payload))
        self.assertNotIn("DO_NOT_LOG", str(list(self.rt.task.events.values())))

    def test_timeout_with_unavailable_confirmation_recovers_when_storage_returns(self):
        self.cli_environment()
        with (
            patch("apps.storage.services.internal.kopia_cli._kopia_path",
                  return_value=self.fake_cli("import time; time.sleep(60)")),
            patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory",
                  side_effect=[[ITEM], [ITEM], ConnectionError("storage unavailable")]),
        ):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        with self.assertRaises(ValidationError):
            assert_no_orphan_cleanup(self.repository.id)
        RepositoryTask.objects.filter(pk=self.rt.pk).update(
            due_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        self.assertEqual(OrphanSnapshotDeleteAttempt.objects.count(), 1)
        assert_no_orphan_cleanup(self.repository.id)

    def test_verified_absence_after_timeout_is_not_reported_as_retained_data(self):
        self.cli_environment()
        with (
            patch("apps.storage.services.internal.kopia_cli._kopia_path",
                  return_value=self.fake_cli("import time; time.sleep(60)")),
            patch("apps.storage.services.internal.orphan_snapshots.controller_snapshot_inventory",
                  side_effect=[[ITEM], [ITEM], []]),
        ):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "success")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "absent")
        self.assertIsNone(self.candidate.deleted_at)
        assert_no_orphan_cleanup(self.repository.id)

    def test_connection_failure_never_creates_a_delete_attempt(self):
        self.cli_environment()
        with patch("apps.storage.services.internal.kopia_cli._connect_maintenance_repository",
                   side_effect=KopiaCliError("connection unavailable")):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "retry_scheduled")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "candidate")
        self.assertFalse(OrphanSnapshotDeleteAttempt.objects.exists())

    def test_lost_result_recovers_without_repeating_physical_command(self):
        self.cli_environment()
        original = ControllerDelete.run

        def finished_then_interrupted(execution, *args):
            original(execution, *args)
            raise ConnectionError("worker result registration interrupted")

        with (
            patch("apps.storage.services.internal.kopia_cli._kopia_path",
                  return_value=self.fake_cli("pass")),
            patch.object(ControllerDelete, "run", finished_then_interrupted),
        ):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        RepositoryTask.objects.filter(pk=self.rt.pk).update(
            due_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "failed")
        self.assertEqual(OrphanSnapshotDeleteAttempt.objects.count(), 1)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "candidate")
        assert_no_orphan_cleanup(self.repository.id)

    def test_late_physical_result_cannot_overwrite_cancelled_task(self):
        self.cli_environment()
        original = ControllerDelete.run

        def finished_then_cancelled(execution, *args):
            result = original(execution, *args)
            Task.objects.filter(pk=self.rt.task_id).update(status=Task.Status.CANCELLED)
            return result

        with (
            patch("apps.storage.services.internal.kopia_cli._kopia_path",
                  return_value=self.fake_cli("pass")),
            patch.object(ControllerDelete, "run", finished_then_cancelled),
        ):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "cancelled")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "deleting")
        self.assertEqual(Task.objects.get(pk=self.rt.task_id).status, Task.Status.CANCELLED)

    def test_late_result_for_changed_location_cannot_mark_current_snapshot_deleted(self):
        self.cli_environment()
        original = ControllerDelete.run

        def finished_then_changed(execution, *args):
            result = original(execution, *args)
            self.repository.config = {**self.repository.config, "prefix": "other-location"}
            self.repository.save(update_fields=["config"])
            return result

        with (
            patch("apps.storage.services.internal.kopia_cli._kopia_path",
                  return_value=self.fake_cli("pass")),
            patch.object(ControllerDelete, "run", finished_then_changed),
        ):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "recovering")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "deleting")

    @patch("apps.storage.services.interface.enqueue_repository_usage_refresh")
    def test_quota_edit_before_intent_does_not_invalidate_prepared_connection(self, refresh):
        self.cli_environment()
        original = ControllerDelete.run

        def changed_then_started(execution, *args):
            self.repository = update_repository(
                repository=self.repository, config={"quota_gb": 20},
            )
            return original(execution, *args)

        with (
            patch("apps.storage.services.internal.kopia_cli._kopia_path",
                  return_value=self.fake_cli("pass")),
            patch.object(ControllerDelete, "run", changed_then_started),
        ):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "success")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, "deleted")
        self.assertEqual(self.candidate.delete_attempts.count(), 1)
        assert_no_orphan_cleanup(self.repository.id)

    @patch("apps.storage.services.interface.enqueue_repository_usage_refresh")
    def test_quota_edit_during_execution_does_not_reject_successful_result(self, refresh):
        self.cli_environment()
        original = ControllerDelete.run

        def finished_then_changed(execution, *args):
            result = original(execution, *args)
            self.repository = update_repository(
                repository=self.repository,
                config={"quota_gb": 30, "quota_unit": "TB",
                        "quota_alert_enabled": False, "quota_alert_threshold": 90},
            )
            return result

        with (
            patch("apps.storage.services.internal.kopia_cli._kopia_path",
                  return_value=self.fake_cli("pass")),
            patch.object(ControllerDelete, "run", finished_then_changed),
        ):
            self.assertEqual(run_orphan_reconciliation(self.rt.id)["status"], "success")
        self.candidate.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.candidate.status, "deleted")
        self.assertIsNone(self.target.active_task_id)
        assert_no_orphan_cleanup(self.repository.id)


class DeleteRevisionTests(SimpleTestCase):
    def setUp(self):
        self.repository = Repository(
            organization_id=1, repo_type=Repository.Type.S3,
            s3_bucket="bucket", config={"prefix": "original"},
        )

    def test_only_quota_and_alert_settings_are_ignored(self):
        revision = repository_delete_revision(self.repository)
        observation = repository_observation_revision(self.repository)
        for key, value in {
            "quota_gb": 20, "quota_unit": "TB",
            "quota_alert_enabled": True, "quota_alert_threshold": 90,
        }.items():
            with self.subTest(key=key):
                self.repository.config[key] = value
                self.assertEqual(repository_delete_revision(self.repository), revision)
                # Do not weaken metric observation fencing as a side effect.
                self.assertNotEqual(repository_observation_revision(self.repository), observation)

    def test_all_connection_settings_including_unknown_options_remain_fenced(self):
        revision = repository_delete_revision(self.repository)
        for key, value in {
            "prefix": "other", "endpoint": "other.example.com",
            "external_endpoint": "other.example.com", "internal_endpoint": "internal.example.com",
            "region": "other-region", "use_tls": False, "s3_url_style": "path",
            "access_key_id": "other-key", "secret_access_key": "other-secret",
            "kopia_password": "other-password", "future_connection_option": "other",
        }.items():
            with self.subTest(key=key):
                self.repository.config = {"prefix": "original", key: value}
                self.assertNotEqual(repository_delete_revision(self.repository), revision)

    def test_repository_identity_and_authority_remain_fenced(self):
        for key, value in {
            "s3_bucket": "other", "s3_platform": "custom", "credential_id": 99,
            "organization_id": 2, "repository_uuid": uuid4(), "status": "removing",
            "repo_type": Repository.Type.NAS, "nas_protocol": "nfs",
            "bind_node_id": 123, "bind_node_type": "agent",
        }.items():
            with self.subTest(key=key):
                previous = getattr(self.repository, key)
                revision = repository_delete_revision(self.repository)
                setattr(self.repository, key, value)
                self.assertNotEqual(repository_delete_revision(self.repository), revision)
                setattr(self.repository, key, previous)


class SupervisorTests(SimpleTestCase):
    def test_volume_mount_does_not_require_syncing_parent_log_filesystem(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"HFL_ORPHAN_EXECUTION_DIR": directory}),
            patch.object(Path, "is_mount", return_value=True),
            patch("apps.storage.services.internal.orphan_delete_execution.os.open",
                  wraps=os.open) as open_directory,
        ):
            self.assertEqual(_root(), Path(directory))
            open_directory.assert_called_once_with(
                Path(directory), os.O_RDONLY | os.O_DIRECTORY,
            )

    @patch("pathlib.Path.read_text", return_value="1 0 0:1 / / rw - ext4 /dev/root rw\n2 1 0:2 / /shared rw - nfs host:/volume rw\n")
    def test_network_lease_expiry_is_not_accepted_as_termination_evidence(self, mounts):
        with self.assertRaises(OSError):
            _require_local_filesystem(Path("/shared/orphan-executions"))

    @patch("apps.storage.services.internal.repository_ownership.verify_s3_repository_ownership")
    @patch("apps.storage.services.internal.kopia_cli.ensure_usage_connection")
    @patch("apps.storage.services.internal.kopia_cli.connect_s3_repository")
    @patch("apps.storage.services.internal.kopia_cli._run_repository_command")
    @patch("apps.storage.services.internal.kopia_cli.usage_config_file", return_value=Path("/tmp/observation.config"))
    def test_inventory_does_not_reconnect_observation_client_as_writable(
        self, config, run, writable_connect, readonly_connect, ownership,
    ):
        from apps.storage.services.internal.snapshot_inventory import controller_snapshot_inventory

        run.return_value = subprocess.CompletedProcess([], 0, "[]", "")
        repository = SimpleNamespace(id=10)
        self.assertEqual(controller_snapshot_inventory(repository), [])
        readonly_connect.assert_called_once_with(repository, config_file=config.return_value)
        writable_connect.assert_not_called()
        ownership.assert_called_once_with(repository, adopt_legacy=False)

    def test_killed_supervisor_cannot_leave_its_cli_running(self):
        with tempfile.TemporaryDirectory() as directory:
            attempt = str(uuid4())
            root = Path(directory)
            fd = _open_lease(root / f"{attempt}.lease", create=True)
            os.write(fd, attempt.encode())
            fcntl.flock(fd, fcntl.LOCK_EX)
            process = subprocess.Popen(
                [sys.executable, orphan_delete_runner.__file__, "--lease-fd", str(fd),
                 "--receipt", str(root / f"{attempt}.json"), "--attempt", attempt,
                 "--timeout", "60"],
                stdin=subprocess.PIPE, pass_fds=(fd,), start_new_session=True,
            )
            os.close(fd)
            try:
                process.stdin.write(json.dumps({
                    "command": [sys.executable, "-c", "import time; time.sleep(60)"]
                }).encode())
                process.stdin.close()
                receipt = root / f"{attempt}.json"
                deadline = time.monotonic() + 5
                while not receipt.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(json.loads(receipt.read_text())["phase"], "running")
                process.kill()
                process.wait(timeout=5)
                deadline = time.monotonic() + 5
                stopped = False
                while not stopped and time.monotonic() < deadline:
                    with confirmed_stopped(SimpleNamespace(pk=attempt, journal_root=directory)) as stopped:
                        pass
                    time.sleep(0.02)
                self.assertTrue(stopped)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_timeout_stops_cli_before_publishing_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            attempt = str(uuid4())
            root = Path(directory)
            fd = _open_lease(root / f"{attempt}.lease", create=True)
            os.write(fd, attempt.encode())
            fcntl.flock(fd, fcntl.LOCK_EX)
            process = subprocess.Popen(
                [sys.executable, orphan_delete_runner.__file__, "--lease-fd", str(fd),
                 "--receipt", str(root / f"{attempt}.json"), "--attempt", attempt,
                 "--timeout", "0.3"],
                stdin=subprocess.PIPE, pass_fds=(fd,), start_new_session=True,
            )
            os.close(fd)
            process.communicate(input=json.dumps({
                "command": [sys.executable, "-c", "import time; time.sleep(60)"]
            }).encode(), timeout=10)
            receipt = json.loads((root / f"{attempt}.json").read_text())
            self.assertEqual(receipt["exit_code"], -signal.SIGKILL)
            self.assertEqual(receipt["outcome"], "timeout")
            with confirmed_stopped(SimpleNamespace(pk=attempt, journal_root=directory)) as stopped:
                self.assertTrue(stopped)

    def test_supervisor_survives_worker_death_and_releases_after_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            parent_code = """
import fcntl,json,os,subprocess,sys,time
fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600)
os.write(fd,sys.argv[3].encode())
fcntl.flock(fd,fcntl.LOCK_EX)
p=subprocess.Popen([sys.executable,sys.argv[4],'--lease-fd',str(fd),
 '--receipt',sys.argv[2],'--attempt',sys.argv[3],'--timeout','1'],
 stdin=subprocess.PIPE,pass_fds=(fd,),start_new_session=True)
p.stdin.write(json.dumps({'command':[sys.executable,'-c','import time; time.sleep(60)']}).encode())
p.stdin.close()
print('ready',flush=True)
time.sleep(60)
"""
            attempt = str(uuid4())
            root = Path(directory)
            parent = subprocess.Popen(
                [sys.executable, "-c", parent_code, str(root / f"{attempt}.lease"),
                 str(root / f"{attempt}.json"), attempt, orphan_delete_runner.__file__],
                stdout=subprocess.PIPE, text=True,
            )
            try:
                self.assertEqual(parent.stdout.readline().strip(), "ready")
                parent.kill()
                parent.wait(timeout=5)
                deadline = time.monotonic() + 10
                receipt = root / f"{attempt}.json"
                stopped = False
                while time.monotonic() < deadline:
                    if receipt.exists() and json.loads(receipt.read_text()).get("phase") == "finished":
                        # The receipt is published just before supervisor exit;
                        # its inherited lease may still be open briefly.
                        with confirmed_stopped(SimpleNamespace(pk=attempt, journal_root=directory)) as stopped:
                            if stopped:
                                break
                    time.sleep(0.05)
                self.assertEqual(json.loads(receipt.read_text())["phase"], "finished")
                self.assertTrue(stopped)
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)
                parent.stdout.close()
