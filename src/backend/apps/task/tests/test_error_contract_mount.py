from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase

from apps.task.error_contract import node_task_error_contract


class NodeTaskErrorContractTests(SimpleTestCase):
    def test_connection_test_timeout_uses_connection_step_and_timeout_code(self):
        task = SimpleNamespace(
            id=uuid4(),
            node_id=42,
            kind="nas.test",
            status="running",
            correlation_id="source-9",
            last_error="",
            payload={"resource_id": 9},
            result={},
        )

        contract = node_task_error_contract(task, timed_out=True)

        self.assertEqual(contract["error_code"], "AGENT.TIMEOUT")
        self.assertEqual(contract["failed_step"], "connection test")
        self.assertEqual(contract["outcome"], "timeout")

    def test_failed_nas_mount_exposes_safe_structured_details(self):
        task = SimpleNamespace(
            id=uuid4(),
            node_id=42,
            kind="nas.mount",
            status="failed",
            correlation_id="source-7",
            last_error="permission denied password=secret-value",
            payload={"source_resource_id": 7, "source_name": "NAS source"},
            result={"error_code": "NAS.PERMISSION_DENIED", "endpoint": "nas.example"},
        )

        contract = node_task_error_contract(task)

        self.assertEqual(contract["severity"], "error")
        self.assertEqual(contract["outcome"], "failed")
        self.assertEqual(contract["error_code"], "NAS.PERMISSION_DENIED")
        self.assertEqual(contract["task_uuid"], str(task.id))
        self.assertEqual(contract["correlation_id"], "source-7")
        self.assertNotIn("secret-value", str(contract))
        self.assertEqual(contract["entities"][0]["type"], "source")

    def test_unmount_residue_is_a_warning(self):
        task = SimpleNamespace(
            id=uuid4(),
            node_id=42,
            kind="nas.unmount",
            status="success",
            correlation_id="source-8",
            last_error="",
            payload={"source_resource_id": 8},
            result={
                "cleanup_complete": False,
                "retained_resources": ["mount"],
                "warnings": [{"code": "MOUNT_BUSY", "detail": "Mount is busy"}],
            },
        )

        contract = node_task_error_contract(task)

        self.assertEqual(contract["severity"], "warning")
        self.assertEqual(contract["outcome"], "partial")
        self.assertFalse(contract["cleanup_complete"])
        self.assertEqual(contract["retained_resources"], ["mount"])
