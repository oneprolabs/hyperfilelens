from django.test import SimpleTestCase

from apps.lens_bridge.services.chat_lifecycle_errors import (
    classify_chat_lifecycle_error,
)


class ChatLifecycleErrorTests(SimpleTestCase):
    def test_model_capability_failure_is_safe_and_not_retryable(self):
        failure = classify_chat_lifecycle_error(
            "multimodal_model_ref: MODEL_NOT_VISION_CAPABLE; "
            "assistant_create: remote create outcome is unknown"
        )

        self.assertEqual(failure.code, "INSIGHT.CHAT_MODEL_NOT_VISION_CAPABLE")
        self.assertFalse(failure.retryable)
        self.assertNotIn("MODEL_NOT_VISION_CAPABLE", failure.message)

    def test_unknown_remote_creation_failure_is_retryable(self):
        failure = classify_chat_lifecycle_error(
            "assistant_create: remote create outcome is unknown"
        )

        self.assertEqual(failure.code, "INSIGHT.CHAT_ASSISTANT_CREATE_UNKNOWN")
        self.assertTrue(failure.retryable)

    def test_empty_error_keeps_legacy_generic_behavior(self):
        failure = classify_chat_lifecycle_error("")

        self.assertEqual(failure.code, "INSIGHT.CHAT_PREPARATION_FAILED")
        self.assertTrue(failure.retryable)

    def test_reader_upgrade_and_browse_timeout_are_actionable(self):
        upgrade = classify_chat_lifecycle_error(
            "INSIGHT.REPOSITORY_READER_UPGRADE_REQUIRED"
        )
        timeout = classify_chat_lifecycle_error("Snapshot browsing timed out.")

        self.assertFalse(upgrade.retryable)
        self.assertEqual(upgrade.code, "INSIGHT.REPOSITORY_READER_UPGRADE_REQUIRED")
        self.assertTrue(timeout.retryable)
        self.assertEqual(timeout.code, "INSIGHT.SNAPSHOT_BROWSE_TIMEOUT")

    def test_reader_repository_snapshot_and_gateway_use_existing_contracts(self):
        cases = [
            (
                "Repository Reader is offline.",
                "INSIGHT.REPOSITORY_READER_UNAVAILABLE",
                True,
            ),
            (
                "Snapshot repository is unavailable.",
                "INSIGHT.REPOSITORY_UNAVAILABLE",
                True,
            ),
            (
                "The selected snapshot was not found.",
                "INSIGHT.SNAPSHOT_UNAVAILABLE",
                False,
            ),
            (
                "INSIGHT.SNAPSHOT_UNAVAILABLE",
                "INSIGHT.SNAPSHOT_UNAVAILABLE",
                False,
            ),
            (
                "Data Gateway is not ready.",
                "INSIGHT.DATA_GATEWAY_UNAVAILABLE",
                True,
            ),
        ]

        for raw_error, expected_code, expected_retryable in cases:
            with self.subTest(raw_error=raw_error):
                failure = classify_chat_lifecycle_error(raw_error)
                self.assertEqual(failure.code, expected_code)
                self.assertEqual(failure.retryable, expected_retryable)
