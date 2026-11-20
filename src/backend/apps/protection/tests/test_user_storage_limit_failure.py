"""Renewal denials retain their cause through backup task error presentation."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.iam.models import Organization
from apps.node.models import NodeTask
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.backup_orchestrator import _finalize_backup_task, _node_task_error_code
from apps.protection.services.backup_task import (
    _directory_error,
    classify_kopia_execution_failure,
)
from apps.task.error_contract import task_error_contract
from apps.task.models import Task, TaskStep


class UserStorageLimitFailureTests(SimpleTestCase):
    def test_quota_denial_takes_precedence_over_stale_process_diagnostics(self):
        result = {
            "error_code": "USER_BACKUP_STORAGE_LIMIT_REACHED",
            "exit_code": 137,
            "stderr": "quota exceeded; no space left on device",
            "failure_details": {"category": "source_read_failed", "count": 1},
        }
        child = SimpleNamespace(
            result=result, status=NodeTask.Status.FAILED,
            last_error="temporary storage credential authorization failed",
        )
        code, message = classify_kopia_execution_failure(result, last_error=child.last_error)
        self.assertEqual(code, "USER_BACKUP_STORAGE_LIMIT_REACHED")
        self.assertIn("repository owner's", message)
        self.assertIn("Restore and cleanup remain available", message)
        self.assertNotIn("device", message)
        self.assertEqual(_node_task_error_code(child), (code, message))
        self.assertEqual(_directory_error(SimpleNamespace(task=child, result=result)), (code, message))
        contract = task_error_contract(SimpleNamespace(
            task_uuid="quota-backup-task", task_type="backup", status="failed",
            error_code=code, error_message=message, current_step="kopia_snapshot",
            result_payload={"terminal_failure": {"error_code": code, "message": message}},
        ))
        self.assertEqual(contract["outcome"], "failed")
        self.assertEqual(contract["reasons"][0]["code"], code)
        self.assertEqual(contract["suggestions"][0]["code"], "review_user_resource_usage")
        self.assertIn("Restore and cleanup remain available", contract["suggestions"][0]["detail"])
        with_residue = task_error_contract(SimpleNamespace(
            task_uuid="quota-backup-with-residue", task_type="backup", status="failed",
            error_code=code, error_message=message, current_step="kopia_snapshot",
            result_payload={"cleanup_complete": False, "retained_resources": ["pending cleanup"]},
        ))
        self.assertIn("review_cleanup", [item["code"] for item in with_residue["suggestions"]])

    def test_plain_cloud_storage_full_is_not_a_user_quota_denial(self):
        code, _message = classify_kopia_execution_failure(
            {"exit_code": 1}, last_error="unable to write pack: no space left on device",
        )
        self.assertEqual(code, "BACKUP_TARGET_STORAGE_FULL")


class UserStorageLimitTaskFinalizationTests(TestCase):
    def test_quota_denied_directory_finalizes_task_and_snapshot_as_failed(self):
        org = Organization.objects.create(key="quota-finalization", name="Quota finalization")
        task = Task.objects.create(organization_id=org.pk, task_type="backup", status="running")
        for index, name in enumerate(("kopia_snapshot", "finalize_snapshot")):
            TaskStep.objects.create(task=task, step_index=index, step_name=name, status="running")
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=org.pk, snapshot_uid="quota-failed", idempotency_key="quota-failed",
            backup_config_id=1, repository_id=1, source_type="agent", source_ref_id=1,
            task_id=task.pk, task_uuid=task.task_uuid, directory_count=1,
        )
        code, message = classify_kopia_execution_failure({"error_code": "USER_BACKUP_STORAGE_LIMIT_REACHED"})
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot, organization_id=org.pk, backup_config_id=1,
            backup_config_dir_id=1, repository_id=1, source_path="/data",
            status="failed", error_code=code, error_message=message,
        )
        with patch("apps.protection.services.backup_orchestrator._stop_repository_server_for_task"):
            _finalize_backup_task(task=task, source_snapshot=snapshot, organization_id=org.pk, total_dirs=1)
        task.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertEqual(snapshot.status, "failed")
        self.assertIsNotNone(task.finished_at)
        self.assertIsNotNone(snapshot.finished_at)
        self.assertEqual(task.error_code, code)
        self.assertEqual(snapshot.error_code, code)
        self.assertEqual(task.result_payload["terminal_failure"]["error_code"], code)
        self.assertEqual(set(task.steps.values_list("status", flat=True)), {"failed"})
        self.assertEqual(task_error_contract(task)["suggestions"][0]["code"], "review_user_resource_usage")
