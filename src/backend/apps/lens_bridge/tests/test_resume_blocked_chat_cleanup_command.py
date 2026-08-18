from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.lens_bridge.management.commands.resume_blocked_chat_cleanup import Command


class ResumeBlockedChatCleanupCommandTests(SimpleTestCase):
    def _options(self, **overrides):
        options = {
            "session_id": 42,
            "source_lens_task_id": "convert-1",
            "reason": "LensNode executor stop confirmed by the operator.",
            "confirm_executor_stopped": True,
        }
        options.update(overrides)
        return options

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id"
    )
    def test_requires_explicit_executor_stop_confirmation(self, get_task):
        with self.assertRaisesRegex(CommandError, "confirm-executor-stopped"):
            Command().handle(
                **self._options(confirm_executor_stopped=False),
            )

        get_task.assert_not_called()

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value=None,
    )
    def test_rejects_a_missing_source_lens_task(self, get_task):
        with self.assertRaisesRegex(CommandError, "was not found"):
            Command().handle(**self._options())

        get_task.assert_called_once_with("convert-1")

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"status": "REVOKED"},
    )
    def test_requires_source_lens_to_return_the_exact_task_identity(self, get_task):
        with self.assertRaisesRegex(CommandError, "exact requested task identity"):
            Command().handle(**self._options())

        get_task.assert_called_once_with("convert-1")

    @patch(
        "apps.lens_bridge.management.commands.resume_blocked_chat_cleanup."
        "sl_client.get_task_by_id",
        return_value={"task_id": "convert-1", "status": "STARTED"},
    )
    def test_rejects_a_nonterminal_source_lens_task(self, get_task):
        with self.assertRaisesRegex(CommandError, "not terminal"):
            Command().handle(**self._options())

        get_task.assert_called_once_with("convert-1")
