from django.test import SimpleTestCase

from apps.lens_bridge.services.chat_lifecycle_errors import (
    classify_chat_lifecycle_error,
    lifecycle_error_state_from_exception,
)
from common.errors import AppError


class ChatLifecycleErrorTests(SimpleTestCase):
    def test_model_capability_failure_is_safe_and_not_retryable(self):
        failure = classify_chat_lifecycle_error(
            "multimodal_model_ref: MODEL_NOT_VISION_CAPABLE; "
            "assistant_create: remote create outcome is unknown"
        )

        self.assertEqual(failure.code, "INSIGHT.CHAT_MODEL_NOT_VISION_CAPABLE")
        self.assertFalse(failure.retryable)
        self.assertNotIn("MODEL_NOT_VISION_CAPABLE", failure.message)

    def test_invalid_scope_summary_is_safe_and_not_retryable(self):
        for diagnostic in (
            "Agent returned an invalid Insight scope summary for /private/path.",
            "Agent returned an invalid Insight scope type for /private/path.",
        ):
            with self.subTest(diagnostic=diagnostic):
                failure = classify_chat_lifecycle_error(diagnostic)

                self.assertEqual(failure.code, "INSIGHT.SCOPE_SUMMARY_INVALID")
                self.assertFalse(failure.retryable)
                self.assertIn("Upgrade the Reader", failure.message)
                self.assertNotIn("/private/path", failure.message)

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

    def test_gateway_capacity_failure_is_actionable_for_legacy_rows(self):
        failure = classify_chat_lifecycle_error(
            "Public Data Gateway capacity is full. Contact your platform administrator."
        )

        self.assertEqual(failure.code, "SUBSCRIPTION.QUOTA_EXCEEDED")
        self.assertEqual(failure.meta["scope"], "gateway")
        self.assertEqual(
            failure.meta["quota_type"],
            "gateway.public_capacity_bytes",
        )
        self.assertTrue(failure.retryable)

    def test_structured_quota_error_keeps_safe_context(self):
        state = lifecycle_error_state_from_exception(
            AppError(
                code="SUBSCRIPTION.QUOTA_EXCEEDED",
                status=403,
                diagnostic="internal capacity diagnostic",
                meta={
                    "quota_type": "gateway.public_capacity_bytes",
                    "scope": "gateway",
                    "limit": 10,
                    "used": 8,
                    "requested": 3,
                    "gateway_link_id": 123,
                },
            )
        )
        failure = classify_chat_lifecycle_error("internal capacity diagnostic", state)

        self.assertEqual(failure.code, "SUBSCRIPTION.QUOTA_EXCEEDED")
        self.assertNotIn("limit", failure.meta)
        self.assertNotIn("used", failure.meta)
        self.assertNotIn("requested", failure.meta)
        self.assertNotIn("gateway_link_id", failure.meta)
        self.assertNotIn("internal capacity diagnostic", failure.message)

    def test_organization_quota_is_not_described_as_gateway_capacity(self):
        state = lifecycle_error_state_from_exception(
            AppError(
                code="SUBSCRIPTION.QUOTA_EXCEEDED",
                status=403,
                meta={
                    "quota_type": "gateway_select_max_files",
                    "scope": "organization",
                },
            )
        )

        failure = classify_chat_lifecycle_error("quota exceeded", state)

        self.assertEqual(failure.meta["scope"], "organization")
        self.assertNotIn("Public Data Gateway", failure.message)

    def test_infrastructure_meter_values_are_hidden_even_with_a_bad_scope(self):
        state = lifecycle_error_state_from_exception(
            AppError(
                code="SUBSCRIPTION.QUOTA_EXCEEDED",
                status=403,
                meta={
                    "quota_type": "gateway.public_capacity_bytes",
                    "scope": "organization",
                    "limit": 100,
                    "used": 95,
                    "requested": 10,
                },
            )
        )

        failure = classify_chat_lifecycle_error("quota exceeded", state)

        self.assertEqual(failure.meta["scope"], "organization")
        self.assertNotIn("limit", failure.meta)
        self.assertNotIn("used", failure.meta)
        self.assertNotIn("requested", failure.meta)

    def test_quota_usage_unavailable_keeps_a_safe_retryable_contract(self):
        state = lifecycle_error_state_from_exception(
            AppError(
                code="SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE",
                status=503,
                diagnostic="internal meter diagnostic",
                meta={
                    "quota_type": "max_public_gateway_capacity_bytes",
                    "scope": "instance",
                    "limit": 100,
                    "used": 95,
                },
            )
        )

        failure = classify_chat_lifecycle_error("internal meter diagnostic", state)

        self.assertEqual(
            failure.code,
            "SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE",
        )
        self.assertTrue(failure.retryable)
        self.assertEqual(failure.meta["scope"], "instance")
        self.assertNotIn("limit", failure.meta)
        self.assertNotIn("used", failure.meta)
        self.assertNotIn("internal meter diagnostic", failure.message)

    def test_reader_upgrade_and_browse_timeout_are_actionable(self):
        upgrade = classify_chat_lifecycle_error(
            "INSIGHT.REPOSITORY_READER_UPGRADE_REQUIRED"
        )
        timeout = classify_chat_lifecycle_error("Snapshot browsing timed out.")

        self.assertFalse(upgrade.retryable)
        self.assertEqual(upgrade.code, "INSIGHT.REPOSITORY_READER_UPGRADE_REQUIRED")
        self.assertTrue(timeout.retryable)
        self.assertEqual(timeout.code, "INSIGHT.SNAPSHOT_BROWSE_TIMEOUT")

    def test_missing_snapshot_path_is_actionable_without_exposing_the_path(self):
        failure = classify_chat_lifecycle_error(
            "INSIGHT_SNAPSHOT_PATH_NOT_FOUND: /private/customer/report.pdf"
        )

        self.assertEqual(failure.code, "INSIGHT.SNAPSHOT_PATH_NOT_FOUND")
        self.assertFalse(failure.retryable)
        self.assertIn("selected file or folder", failure.message)
        self.assertNotIn("/private/customer/report.pdf", failure.message)

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
