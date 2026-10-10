"""Presentation-only lifecycle coverage; no task steps or diagnostics are mutated."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase

from apps.task.error_contract import task_error_contract


class TaskErrorContractLifecycleTests(TestCase):
    def task(self, status="failed", payload=None):
        return SimpleNamespace(
            status=status, result_payload=payload or {}, task_type="source_unregister",
            task_uuid="parent-task", error_code="TASK_FAILED", error_message="Original reason",
            current_step="cleanup",
        )

    def test_retry_running_and_clean_recovery_have_no_obsolete_contract(self):
        for status in ("pending", "running", "success"):
            task = self.task(status)
            task.error_code = task.error_message = None
            self.assertIsNone(task_error_contract(task))

    def test_cancelled_is_explicit_warning(self):
        contract = task_error_contract(self.task("cancelled"))
        self.assertEqual((contract["outcome"], contract["severity"]), ("cancelled", "warning"))

    def test_timeout_is_a_task_result_not_a_monitor_timeout(self):
        contract = task_error_contract(self.task("timeout"))
        self.assertEqual((contract["outcome"], contract["severity"]), ("timeout", "error"))

    def test_mixed_children_preserve_failed_task_identity_and_warning_residue(self):
        task = self.task("success", {
            "cleanup_complete": False, "retained_resources": ["mount"],
            "repository_cleanup_tasks": [
                {"task_uuid": "child-ok", "status": "success"},
                {"task_uuid": "child-failed", "status": "failed", "error_message": "Original child reason"},
                {"task_uuid": "child-timeout", "status": "timeout"},
            ],
        })
        contract = task_error_contract(task)
        self.assertEqual(contract["severity"], "warning")
        self.assertEqual([item["id"] for item in contract["entities"]], ["child-failed", "child-timeout"])
        self.assertEqual(contract["entities"][0]["error"], "Original child reason")
        self.assertEqual(contract["retained_resources"], ["mount"])

    def test_projection_does_not_rewrite_analysis_suggestions_or_task_state(self):
        task = self.task(payload={"reasons": [{"code": "BUSY", "detail": "Original analysis"}],
                                 "suggestions": [{"code": "RETRY", "detail": "Original solution"}]})
        original = deepcopy(task.__dict__)
        first = task_error_contract(task)
        self.assertEqual(first, task_error_contract(task))
        self.assertEqual(task.__dict__, original)
        self.assertEqual(first["suggestions"][0]["detail"], "Original solution")

    def test_sensitive_entity_details_remain_redacted(self):
        task = self.task(payload={"sources": [{"source_id": "nas:1", "detail": "password=private-value"}]})
        self.assertNotIn("private-value", str(task_error_contract(task)))
