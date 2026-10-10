"""Missing backup roots are explained without rewriting historical results."""

from copy import deepcopy
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.task.api.serializers.task import TaskEventSerializer
from apps.task.error_contract import backup_source_failure_projection, task_error_contract
from apps.task.models import TaskEvent


class BackupSourceErrorContractTests(SimpleTestCase):
    path = "/root/test data/test"

    def payload(self, *, missing_path=None, preparation=True):
        missing_path = missing_path or self.path
        prefix = "failed to prepare source: unable to get local filesystem entry: " if preparation else ""
        return {
            "terminal_failure": {
                "error_code": "KOPIA_PROCESS_DIED",
                "message": f"upload error: unsupported source: root@agent:{self.path}",
                "technical_detail": (
                    f"Snapshotting root@agent:{self.path} ...\n"
                    f"{prefix}resolveSymlink: stat: lstat {missing_path}: no such file or directory\n"
                    f"upload error: unsupported source: root@agent:{self.path}"
                ),
            },
            "successful_directory_count": 18,
            "failed_directory_count": 1,
            "source_snapshot_status": "partial",
        }

    def test_historical_task_explains_root_and_retains_original_diagnostics(self):
        payload = self.payload()
        original = deepcopy(payload)
        task = SimpleNamespace(
            task_type="backup", status="failed", result_payload=payload,
            error_code="KOPIA_PROCESS_DIED",
            error_message=payload["terminal_failure"]["message"],
            current_step="finalize_snapshot", task_uuid="backup-task",
        )
        contract = task_error_contract(task)
        self.assertEqual(contract["error_code"], "SOURCE_PATH_NOT_FOUND")
        self.assertEqual(contract["reasons"][0]["code"], "SOURCE_PATH_NOT_FOUND")
        self.assertNotIn("unsupported source", contract["reasons"][0]["detail"])
        self.assertEqual(contract["entities"][0]["name"], self.path)
        self.assertEqual([item["code"] for item in contract["suggestions"]], [
            "restore_backup_source_path", "remove_obsolete_backup_source", "retry_after_source_path_fixed",
        ])
        self.assertEqual(contract["technical_detail"], original)
        self.assertEqual(task.result_payload, original)
        self.assertEqual(task.error_code, "KOPIA_PROCESS_DIED")

    def test_event_serializer_projects_the_same_reason_without_mutation(self):
        metadata = {**self.payload(), "source_path": self.path}
        event = TaskEvent(seq=1, level="ERROR", message="Directory backup failed", metadata=metadata)
        projected = TaskEventSerializer(event).data["metadata"]
        self.assertEqual(projected["terminal_failure"]["error_code"], "SOURCE_PATH_NOT_FOUND")
        self.assertEqual(projected["terminal_failure"]["path"], self.path)
        self.assertEqual(projected["terminal_failure"]["technical_detail"], metadata["terminal_failure"]["technical_detail"])
        self.assertEqual(event.metadata, metadata)

    def test_unrelated_missing_paths_and_incomplete_evidence_use_existing_fallback(self):
        cases = [
            self.payload(missing_path=self.path + "/child"),
            self.payload(missing_path="/repository/cache/config"),
            self.payload(preparation=False),
            {"terminal_failure": {"message": "upload error: unsupported source"}},
            {**self.payload(), "source_path": "/different/root"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(backup_source_failure_projection(payload), payload)

    def test_non_backup_task_is_not_reclassified(self):
        task = SimpleNamespace(
            task_type="restore", status="failed", result_payload=self.payload(),
            error_code="RESTORE_FAILED", error_message="Restore failed.",
            current_step="restore", task_uuid="restore-task",
        )
        self.assertEqual(task_error_contract(task)["error_code"], "RESTORE_FAILED")
