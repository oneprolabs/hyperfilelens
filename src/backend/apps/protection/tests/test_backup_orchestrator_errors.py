from django.test import SimpleTestCase

from apps.node.models import NodeTask
from apps.protection.services.backup_orchestrator import _node_task_error_code


class BackupOrchestratorErrorCodeTests(SimpleTestCase):
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
