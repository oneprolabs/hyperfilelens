import uuid
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.node.models import NodeTask
from apps.protection.services.backup_orchestrator import (
    _node_task_error_code,
    _redeliver_pending_node_task_after_commit,
)


class BackupOrchestratorErrorCodeTests(SimpleTestCase):
    def test_pending_task_resume_runs_after_commit(self):
        node_task = NodeTask(id=uuid.uuid4())

        with (
            patch(
                "apps.protection.services.backup_orchestrator.transaction.on_commit"
            ) as on_commit,
            patch(
                "apps.protection.services.backup_orchestrator.redeliver_pending_agent_task"
            ) as redeliver,
        ):
            _redeliver_pending_node_task_after_commit(node_task=node_task)

            redeliver.assert_not_called()
            callback = on_commit.call_args.args[0]
            callback()

        redeliver.assert_called_once_with(task_id=str(node_task.id))

    def test_result_ack_timeout_is_preserved(self):
        task = NodeTask(
            status=NodeTask.Status.TIMEOUT,
            last_error="result acknowledgement timeout",
            result={"diagnostic_error_code": "RESULT_ACK_TIMEOUT"},
        )

        self.assertEqual(
            _node_task_error_code(task),
            ("RESULT_ACK_TIMEOUT", "result acknowledgement timeout"),
        )

    def test_agent_connection_diagnostic_is_not_projected_as_watchdog_stall(self):
        task = NodeTask(
            status=NodeTask.Status.TIMEOUT,
            last_error="AGENT_CONNECTION_UNSTABLE: reconnecting",
            result={"diagnostic_error_code": "AGENT_CONNECTION_UNSTABLE"},
        )

        error_code, message = _node_task_error_code(task)

        self.assertEqual(error_code, "AGENT_CONNECTION_UNSTABLE")
        self.assertIn("connection remained unstable", message)

    def test_failed_delivery_diagnostic_is_preserved(self):
        task = NodeTask(
            status=NodeTask.Status.FAILED,
            last_error="agent websocket is not routable",
            result={"diagnostic_error_code": "AGENT_UNAVAILABLE"},
        )

        error_code, message = _node_task_error_code(task)

        self.assertEqual(error_code, "AGENT_UNAVAILABLE")
        self.assertIn("Bring the Agent online", message)

    def test_agent_path_protection_errors_are_preserved(self):
        expected_messages = {
            "AGENT_PATH_FORBIDDEN": "cannot be backed up directly",
            "BACKUP_PROTECTION_POLICY_MISSING": "path exclusion is missing",
            "BACKUP_PROTECTION_POLICY_VERIFY_FAILED": "could not verify",
        }

        for error_code, expected_message in expected_messages.items():
            with self.subTest(error_code=error_code):
                task = NodeTask(
                    status=NodeTask.Status.FAILED,
                    last_error="internal diagnostic",
                    result={"error_code": error_code},
                )

                projected_code, message = _node_task_error_code(task)

                self.assertEqual(projected_code, error_code)
                self.assertIn(expected_message, message)

    def test_repository_server_internal_detail_is_not_projected(self):
        task = NodeTask(
            status=NodeTask.Status.FAILED,
            last_error="REPOSITORY_SERVER_READY_TIMEOUT: sensitive Proxy log",
            result={"error_code": "REPOSITORY_SERVER_READY_TIMEOUT"},
        )

        error_code, message = _node_task_error_code(task)

        self.assertEqual(error_code, "PROXY_REPOSITORY_SERVER_START_FAILED")
        self.assertIn("did not make", message)
        self.assertNotIn("sensitive", message)
