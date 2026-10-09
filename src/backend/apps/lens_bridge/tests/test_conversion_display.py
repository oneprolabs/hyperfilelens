from unittest import TestCase

from apps.lens_bridge.services import conversion_display


class ConversionDisplayTests(TestCase):
    def test_parallel_file_ordinals_do_not_control_aggregate_progress(self):
        percentages = []
        processed_counts = []
        for raw_percent, ordinal, converted in (
            (76, 6, 2), (90, 7, 3), (63, 5, 4), (50, 4, 5), (36, 3, 6),
        ):
            view = conversion_display.document_conversion_view({
                "status": "STARTED",
                "progress_step": "conversion_progress",
                "progress_percent": raw_percent,
                "progress_counts": {
                    "total": 7, "candidates": 6, "processed": ordinal,
                    "converted": converted, "failed": 0, "skipped": 0, "unsupported": 1,
                },
            })
            percentages.append(view["progress_percent"])
            processed_counts.append(view["progress_counts"]["processed"] - 1)
        self.assertEqual(percentages, [36, 50, 63, 76, 90])
        self.assertEqual(processed_counts, [2, 3, 4, 5, 6])

    def test_processing_counts_survive_missing_percent_and_visual_phase_changes(self):
        for phase, raw_percent in (
            ("PARSING_DOCUMENTS", None), ("PROCESSING_EMBEDDED_IMAGES", 12),
        ):
            view = conversion_display.document_conversion_view({
                "status": "STARTED",
                "progress_phase": phase,
                "progress_percent": raw_percent,
                "progress_counts": {
                    "total": 7, "candidates": 6, "processed": 1,
                    "converted": 3, "failed": 1, "skipped": 1, "unsupported": 1,
                },
            })
            self.assertEqual(view["progress_percent"], 76)
            self.assertEqual(view["progress_counts"]["processed"], 6)

    def test_finalization_reserves_completion_for_successful_task_status(self):
        state = {
            "status": "STARTED", "progress_phase": "FINALIZING",
            "progress_counts": {
                "total": 7, "candidates": 6, "processed": 7,
                "converted": 6, "failed": 0, "skipped": 0, "unsupported": 1,
            },
        }
        view = conversion_display.document_conversion_view(state)
        self.assertEqual(view["phase"], "running")
        self.assertEqual(view["progress_percent"], 99)

    def test_success_uses_final_summary_instead_of_stale_live_metrics(self):
        view = conversion_display.document_conversion_view({
            "status": "SUCCESS", "progress_percent": 50,
            "progress_counts": {
                "total": 7, "candidates": 6, "processed": 4,
                "converted": 5, "failed": 0, "skipped": 0, "unsupported": 1,
            },
            "summary": {
                "total": 7, "candidates": 6, "success": 6,
                "failed": 0, "skipped": 0, "unsupported": 1,
            },
        })
        self.assertEqual(view["progress_percent"], 100)
        self.assertEqual(view["progress_counts"]["processed"], 7)
        self.assertEqual(view["progress_counts"]["converted"], 6)

    def test_prefers_overall_percentage_when_only_phase_local_metrics_exist(self):
        view = conversion_display.document_conversion_view({
            "status": "STARTED", "progress_percent": 50, "overall_progress_percent": 76,
        })
        self.assertEqual(view["progress_percent"], 76)

    def test_unsupported_and_failed_files_keep_distinct_outcomes(self):
        view = conversion_display.document_conversion_view({
            "status": "SUCCESS",
            "summary": {"total": 2, "unsupported": 1, "failed": 1, "items": [
                {"name": "report.ofd", "reason": "UNSUPPORTED_TYPE", "status": "unsupported"},
                {"name": "broken.pdf", "reason": "CORRUPT", "status": "failed"},
            ]},
        })
        self.assertEqual([item["outcome"] for item in view["problem_items"]], ["skipped", "failed"])

    def test_progress_counts_are_safe_and_separate_from_success_counts(self):
        view = conversion_display.document_conversion_view({
            "status": "STARTED",
            "progress_percent": 46,
            "summary": {"success": 2},
            "progress_counts": {
                "total": 45, "candidates": 40, "processed": 23,
                "unsupported": 5, "failed": -1, "private_path": "/secret",
            },
        })
        self.assertEqual(view["progress_percent"], 46)
        self.assertEqual(view["progress_counts"]["processed"], 23)
        self.assertEqual(view["counts"]["success"], 2)
        self.assertEqual(view["progress_counts"]["failed"], 0)
        self.assertNotIn("private_path", view["progress_counts"])

    def test_nonfinite_conversion_percent_is_hidden(self):
        for value in (float("inf"), float("nan"), "invalid", True, -1, 101):
            view = conversion_display.document_conversion_view({
                "status": "STARTED", "progress_percent": value,
            })
            self.assertIsNone(view["progress_percent"])

    def test_reason_label_known_and_unknown(self):
        self.assertEqual(
            conversion_display.reason_label("PASSWORD_PROTECTED"),
            "Password protected",
        )
        self.assertEqual(
            conversion_display.reason_label("CUSTOM_NEW_REASON"),
            "Custom New Reason",
        )

    def test_document_conversion_view_none_for_empty(self):
        self.assertIsNone(conversion_display.document_conversion_view(None))
        self.assertIsNone(conversion_display.document_conversion_view({}))

    def test_starting_state_is_running_not_all_ok(self):
        view = conversion_display.document_conversion_view(
            {
                "status": "STARTING",
                "summary": {},
                "warnings": [],
            }
        )
        assert view is not None
        self.assertEqual(view["phase"], "running")
        self.assertFalse(view["all_ok"])
        self.assertEqual(view["problem_items"], [])

    def test_conversion_recovery_is_exposed_without_remote_details(self):
        view = conversion_display.document_conversion_view(
            {
                "status": "PENDING",
                "recovery": {
                    "task_id": "resume-2",
                    "original_task_id": "convert-1",
                    "resumable": True,
                    "resumed": True,
                    "resume_source": "checkpoint",
                    "restart_required": False,
                    "reason": "CHECKPOINT_AVAILABLE",
                    "sensitive": "must-not-be-forwarded",
                },
            }
        )

        assert view is not None
        self.assertEqual(
            view["recovery"],
            {
                "task_id": "resume-2",
                "original_task_id": "convert-1",
                "resumable": True,
                "resumed": True,
                "resume_source": "checkpoint",
                "restart_required": False,
                "reason": "CHECKPOINT_AVAILABLE",
            },
        )

    def test_document_conversion_view_counts_and_items(self):
        view = conversion_display.document_conversion_view(
            {
                "status": "SUCCESS",
                "progress_message": "Done",
                "summary": {
                    "total": 4,
                    "candidates": 3,
                    "success": 1,
                    "failed": 1,
                    "skipped": 0,
                    "unsupported": 1,
                    "items": [
                        {"name": "ok.pdf", "reason": "UNCHANGED"},
                        {"name": "scan.pdf", "reason": "NO_EXTRACTABLE_TEXT"},
                        {"name": "notes.doc", "reason": "UNSUPPORTED_TYPE"},
                    ],
                },
                "warnings": ["CONVERSION_PARTIAL_FAILED"],
            }
        )
        assert view is not None
        self.assertEqual(view["status"], "SUCCESS")
        self.assertEqual(view["phase"], "succeeded")
        self.assertFalse(view["all_ok"])
        self.assertEqual(view["counts"]["success"], 1)
        self.assertEqual(view["counts"]["unchanged"], 1)
        self.assertTrue(view["usable"])
        self.assertEqual(
            [row["name"] for row in view["problem_items"]],
            ["scan.pdf", "notes.doc"],
        )
        self.assertEqual(
            view["problem_items"][0]["reason_label"],
            "No extractable text (may be scanned or empty)",
        )
        self.assertEqual(
            view["warnings"][0]["label"],
            "Some documents could not be converted",
        )
        self.assertEqual(
            view["format_matrix"]["unsupported_mvp"],
            [".doc"],
        )

    def test_clean_success_is_all_ok(self):
        view = conversion_display.document_conversion_view(
            {
                "status": "SUCCESS",
                "summary": {
                    "total": 1,
                    "candidates": 1,
                    "success": 1,
                    "failed": 0,
                    "unsupported": 0,
                    "items": [],
                },
            }
        )
        assert view is not None
        self.assertTrue(view["all_ok"])
        self.assertFalse(view["empty_result"])

    def test_zero_candidate_success_is_empty_not_all_ok(self):
        view = conversion_display.document_conversion_view(
            {
                "status": "SUCCESS",
                "summary": {
                    "total": 0,
                    "candidates": 0,
                    "success": 0,
                    "failed": 0,
                    "unsupported": 0,
                    "items": [],
                },
            }
        )
        assert view is not None
        self.assertFalse(view["all_ok"])
        self.assertTrue(view["empty_result"])

    def test_failed_counts_without_items_not_all_ok(self):
        view = conversion_display.document_conversion_view(
            {
                "status": "SUCCESS",
                "summary": {
                    "total": 2,
                    "success": 0,
                    "failed": 2,
                    "items": [],
                },
            }
        )
        assert view is not None
        self.assertFalse(view["all_ok"])

    def test_usable_false_when_no_success(self):
        view = conversion_display.document_conversion_view(
            {
                "status": "SUCCESS",
                "summary": {
                    "success": 0,
                    "items": [{"name": "a.pdf", "reason": "PASSWORD_PROTECTED"}],
                },
            }
        )
        assert view is not None
        self.assertFalse(view["usable"])
        self.assertFalse(view["all_ok"])
        self.assertEqual(len(view["problem_items"]), 1)

    def test_data_context_private_gateway(self):
        ctx = conversion_display.data_context_for_session(
            backup_config_id=12,
            backup_source_snapshot_id=34,
            snapshot_created_at="2026-07-01T00:00:00Z",
            gateway_scope="private",
            gateway_name="gw-a",
            gateway_selection_mode="manual",
        )
        self.assertEqual(ctx["origin"], "protected_snapshot")
        self.assertEqual(ctx["processing_location"], "private_gateway")
        self.assertEqual(ctx["restore_path"], "/protection/restore/snapshots/34")
        self.assertEqual(ctx["backup_detail_path"], "/protection/backups/12")
