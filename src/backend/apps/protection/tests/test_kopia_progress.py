from __future__ import annotations

from django.test import SimpleTestCase
from django.utils import timezone

from apps.protection.services.progress.aggregator import aggregate_lanes
from apps.protection.services.progress.display import (
    enrich_kopia_progress_payload,
    has_transfer_progress,
)
from apps.protection.services.progress.kopia_fields import normalize_lane_progress
from apps.protection.services.progress.orchestration_label import (
    backup_orchestration_label_meta,
    restore_orchestration_label,
)


class KopiaProgressAggregatorTests(SimpleTestCase):
    def test_aggregate_parallel_lanes(self):
        lanes = [
            {
                "id": "1",
                "name": "/a",
                "status": "running",
                "progress": normalize_lane_progress(
                    progress={
                        "phase": "kopia_transfer",
                        "kopia_phase": "uploading",
                        "bytes_done": 2,
                        "bytes_total": 3,
                        "bytes_total_known": True,
                        "processing_speed_bps": 4_000_000,
                        "upload_speed_bps": 1_000_000,
                        "kopia_eta_seconds": 1000,
                    },
                    status="running",
                ),
            },
            {
                "id": "2",
                "name": "/b",
                "status": "running",
                "progress": normalize_lane_progress(
                    progress={
                        "phase": "kopia_transfer",
                        "kopia_phase": "uploading",
                        "bytes_done": 1,
                        "bytes_total": 4,
                        "bytes_total_known": True,
                        "processing_speed_bps": 6_000_000,
                        "upload_speed_bps": 2_000_000,
                        "kopia_eta_seconds": 1500,
                    },
                    status="running",
                ),
            },
        ]
        aggregate = aggregate_lanes(lanes)
        self.assertAlmostEqual(aggregate["percent"], 100 * 3 / 7, places=1)
        self.assertEqual(aggregate["bytes_done"], 3)
        self.assertEqual(aggregate["bytes_total"], 7)
        self.assertEqual(aggregate["processing_speed_bps"], 10_000_000)
        self.assertEqual(aggregate["speed_bps"], 3_000_000)
        self.assertEqual(aggregate["upload_speed_bps"], 3_000_000)
        self.assertEqual(aggregate["eta_seconds"], 1500)

    def test_partial_estimate_includes_completed_lane_total(self):
        aggregate = aggregate_lanes([
            {
                "id": "completed",
                "status": "success",
                "progress": {
                    "progress_schema_version": 2,
                    "bytes_done": 500,
                    "processed_bytes": 500,
                    "bytes_total": 500,
                    "bytes_total_known": True,
                    "estimated_bytes": 0,
                    "is_transfer": True,
                },
            },
            {
                "id": "running",
                "status": "running",
                "progress": {
                    "progress_schema_version": 2,
                    "bytes_done": 200,
                    "processed_bytes": 200,
                    "bytes_total": 300,
                    "bytes_total_known": True,
                    "estimated_bytes": 300,
                    "is_transfer": True,
                },
            },
            {
                "id": "pending",
                "status": "pending",
                "progress": {},
            },
        ])

        self.assertEqual(aggregate["processed_bytes"], 700)
        self.assertEqual(aggregate["estimated_bytes"], 800)
        self.assertFalse(aggregate["bytes_total_known"])
        self.assertIsNone(aggregate["bytes_total"])

    def test_completed_restore_lane_retains_final_item_counts(self):
        lanes = [
            {
                "id": "1",
                "name": "/data",
                "status": "success",
                "progress": normalize_lane_progress(
                    progress={
                        "phase": "kopia_transfer",
                        "kopia_phase": "restoring",
                        "processed_bytes": 649_000_000,
                        "bytes_total": 649_000_000,
                        "bytes_total_known": True,
                        "processed_count": 263,
                        "total_count": 263,
                        "speed_bps": 5_000_000,
                    },
                    status="success",
                ),
            },
        ]

        aggregate = aggregate_lanes(lanes)

        self.assertEqual(aggregate["processed_count"], 263)
        self.assertEqual(aggregate["total_count"], 263)
        self.assertEqual(aggregate["bytes_done"], 649_000_000)
        self.assertIsNone(aggregate["speed_bps"])
        self.assertIsNone(aggregate["eta_seconds"])

    def test_explicit_zero_restore_total_remains_known(self):
        lane = normalize_lane_progress(
            progress={
                "phase": "kopia_transfer",
                "kopia_phase": "restoring",
                "bytes_done": 0,
                "bytes_total": 0,
                "bytes_total_known": True,
                "processed_count": 0,
                "total_count": 1,
                "kopia_percent": 0,
            },
            status="running",
        )

        self.assertEqual(lane["bytes_total"], 0)
        self.assertTrue(lane["bytes_total_known"])
        self.assertEqual(lane["total_count"], 1)
        self.assertEqual(lane["percent"], 0)

    def test_schema_v2_3ec_uses_processed_bytes_for_progress(self):
        processed = 3_478_373_863
        estimated = 4_130_621_356
        uploaded = 270_077_614
        lane = normalize_lane_progress(
            progress={
                "progress_schema_version": 2,
                "phase": "kopia_transfer",
                "kopia_phase": "processing",
                "processed_bytes": processed,
                "uploaded_bytes": uploaded,
                "estimated_bytes": estimated,
                "bytes_total": estimated,
                "bytes_total_known": True,
                "processing_speed_bps": 200_000_000,
                "upload_speed_bps": 20_000_000,
                "kopia_eta_seconds": 4,
            },
            status="running",
        )

        self.assertEqual(lane["bytes_done"], processed)
        self.assertEqual(lane["processed_bytes"], processed)
        self.assertEqual(lane["uploaded_bytes"], uploaded)
        self.assertAlmostEqual(lane["percent"], 84.21, places=2)

    def test_schema_v2_3ed_deduplicated_snapshot_does_not_use_upload_as_progress(self):
        processed = 3_157_346_250
        lane = normalize_lane_progress(
            progress={
                "progress_schema_version": 2,
                "phase": "kopia_transfer",
                "kopia_phase": "processing",
                "processed_bytes": processed,
                "uploaded_bytes": 192,
                "estimated_bytes": processed + 1_000_000,
                "bytes_total": processed + 1_000_000,
                "bytes_total_known": True,
                "upload_speed_bps": 0,
            },
            status="running",
        )

        self.assertEqual(lane["bytes_done"], processed)
        self.assertEqual(lane["uploaded_bytes"], 192)
        self.assertGreater(lane["percent"], 99.0)
        self.assertEqual(lane["upload_speed_bps"], 0)

    def test_orchestration_progress_not_counted_as_transfer(self):
        lane = normalize_lane_progress(
            progress={
                "phase": "orchestration",
                "orchestration_phase": "repository_prepare",
                "orchestration_label": "Connecting repository",
            },
            status="running",
        )
        self.assertFalse(lane["is_transfer"])
        self.assertIsNone(lane["percent"])

    def test_aggregate_uses_lane_percent_when_bytes_total_unknown(self):
        lanes = [
            {
                "id": "1",
                "name": "/data",
                "status": "running",
                "progress": normalize_lane_progress(
                    progress={
                        "phase": "kopia_transfer",
                        "kopia_phase": "hashing",
                        "kopia_percent": 37,
                        "percent": 37,
                        "hashed_bytes": 1024,
                    },
                    status="running",
                ),
            },
        ]
        aggregate = aggregate_lanes(lanes)
        self.assertIsNone(aggregate["percent"])

    def test_uploading_caught_up_to_hashed_does_not_fake_full_total(self):
        size = 13_100_000_000
        lane = normalize_lane_progress(
            progress={
                "phase": "kopia_transfer",
                "kopia_phase": "uploading",
                "uploaded_bytes": size,
                "hashed_bytes": size,
                "bytes_done": size,
                "bytes_total": size,
                "bytes_total_known": True,
                "kopia_percent": 95,
            },
            status="running",
        )
        self.assertFalse(lane["bytes_total_known"])
        self.assertIsNone(lane["bytes_total"])
        self.assertEqual(lane["percent"], 95.0)

    def test_uploading_prefers_bytes_percent_when_total_known(self):
        bytes_total = 204 * 1000 * 1000 * 1000
        bytes_done = 507 * 1000 * 1000
        lane = normalize_lane_progress(
            progress={
                "phase": "kopia_transfer",
                "kopia_phase": "uploading",
                "uploaded_bytes": bytes_done,
                "bytes_done": bytes_done,
                "bytes_total": bytes_total,
                "bytes_total_known": True,
                "estimated_bytes": bytes_total,
                "kopia_percent": 1.26,
            },
            status="running",
        )
        expected = 100.0 * bytes_done / bytes_total
        self.assertAlmostEqual(lane["percent"], expected, places=3)
        self.assertEqual(lane["percent_source"], "computed")
        self.assertLess(lane["percent"], 1.26)

    def test_inflated_estimate_replaced_by_reference(self):
        reference = 2_200_000_000
        lane = normalize_lane_progress(
            progress={
                "phase": "kopia_transfer",
                "kopia_phase": "uploading",
                "uploaded_bytes": 561_000_000,
                "hashed_bytes": 561_000_000,
                "estimated_bytes": 206_400_000_000,
                "bytes_total": 206_400_000_000,
                "bytes_total_known": True,
            },
            status="running",
            reference_bytes_total=reference,
        )
        self.assertEqual(lane["bytes_total"], reference)
        self.assertTrue(lane["bytes_total_reference"])

    def test_uploaded_decimal_bytes_preferred_for_done(self):
        lane = normalize_lane_progress(
            progress={
                "phase": "kopia_transfer",
                "kopia_phase": "uploading",
                "uploaded_bytes": 420_500_000,
                "hashed_bytes": 561_300_000,
                "bytes_done": 561_300_000,
            },
            status="running",
        )
        self.assertEqual(lane["bytes_done"], 420_500_000)

    def test_aggregate_caps_percent_while_lane_still_running(self):
        lanes = [
            {
                "id": "1",
                "name": "/data",
                "status": "running",
                "progress": normalize_lane_progress(
                    progress={
                        "phase": "kopia_transfer",
                        "kopia_phase": "hashing",
                        "bytes_done": 10,
                        "bytes_total": 10,
                        "bytes_total_known": True,
                        "kopia_percent": 100,
                    },
                    status="running",
                ),
            },
        ]
        aggregate = aggregate_lanes(lanes)
        self.assertEqual(aggregate["percent"], 99.0)


class KopiaProgressDisplayTests(SimpleTestCase):
    def test_has_transfer_progress_requires_transfer_counters(self):
        lanes = [
            {
                "status": "running",
                "progress": normalize_lane_progress(
                    progress={
                        "phase": "orchestration",
                        "orchestration_phase": "repository_prepare",
                    },
                    status="running",
                ),
            }
        ]
        self.assertFalse(has_transfer_progress(lanes))

    def test_backup_estimating_before_transfer(self):
        lanes = [
            {
                "status": "running",
                "progress": normalize_lane_progress(
                    progress={
                        "phase": "orchestration",
                        "orchestration_phase": "repository_prepare",
                        "orchestration_label": "Connecting repository",
                    },
                    status="running",
                ),
            }
        ]
        meta, phase = backup_orchestration_label_meta(
            task_status="running",
            lanes=lanes,
            aggregate=aggregate_lanes(lanes),
        )
        self.assertEqual(phase, "preparing")
        self.assertEqual(meta["label_key"], "protection.taskProgress.backup.preparing")

    def test_backup_estimating_when_running_without_transfer(self):
        lanes = [{"id": "1", "name": "/data", "status": "running", "progress": {}}]
        meta, phase = backup_orchestration_label_meta(
            task_status="running",
            lanes=lanes,
            aggregate=aggregate_lanes(lanes),
        )
        self.assertEqual(phase, "estimating")
        self.assertEqual(meta["label_key"], "protection.taskProgress.backup.estimating")

    def test_restore_estimating_uses_placeholder_display(self):
        payload = enrich_kopia_progress_payload(
            {
                "orchestration_label": "Preparing restore...",
                "orchestration_phase": "estimating",
                "aggregate": {
                    "percent": None,
                    "bytes_done": 0,
                    "bytes_total": 13_100_000_000,
                    "bytes_total_known": True,
                    "bytes_total_reference": True,
                    "speed_bps": None,
                    "eta_seconds": None,
                    "lanes_done": 0,
                    "lanes_total": 1,
                    "slowest_lane": None,
                },
            }
        )
        self.assertEqual(payload["display_percent"], 8.0)
        self.assertEqual(payload["percent_source"], "placeholder")
        self.assertTrue(payload["show_metrics"])

    def test_transferring_uses_kopia_percent_when_total_known(self):
        payload = enrich_kopia_progress_payload(
            {
                "orchestration_phase": "transferring",
                "aggregate": {
                    "percent": 42.0,
                    "bytes_done": 4,
                    "bytes_total": 10,
                    "bytes_total_known": True,
                    "bytes_total_reference": False,
                    "speed_bps": 1000,
                    "eta_seconds": 30,
                    "lanes_done": 0,
                    "lanes_total": 1,
                    "slowest_lane": None,
                },
            }
        )
        self.assertEqual(payload["display_percent"], 42.0)
        self.assertEqual(payload["percent_source"], "kopia")
        self.assertTrue(payload["show_metrics"])

    def test_transferring_unknown_total_uses_time_creep(self):
        payload = enrich_kopia_progress_payload(
            {
                "orchestration_phase": "transferring",
                "aggregate": {
                    "percent": 42.0,
                    "bytes_done": 1400,
                    "bytes_total": None,
                    "bytes_total_known": False,
                    "speed_bps": None,
                    "eta_seconds": None,
                    "lanes_done": 0,
                    "lanes_total": 1,
                    "slowest_lane": None,
                },
            },
            transfer_progress={
                "unknown_total_started_at": timezone.now().isoformat(),
                "phase": "transferring",
            },
        )
        self.assertAlmostEqual(payload["display_percent"], 3.0, places=1)
        self.assertEqual(payload["percent_source"], "placeholder")
        self.assertFalse(payload["show_metrics"])

    def test_restore_transferring_label(self):
        lanes = [
            {
                "id": "1",
                "name": "/restore",
                "status": "running",
                "progress": normalize_lane_progress(
                    progress={
                        "phase": "kopia_transfer",
                        "kopia_phase": "restoring",
                        "bytes_done": 1,
                        "bytes_total": 4,
                        "bytes_total_known": True,
                    },
                    status="running",
                ),
            }
        ]
        label, phase = restore_orchestration_label(
            task_status="running",
            lanes=lanes,
            aggregate=aggregate_lanes(lanes),
        )
        self.assertEqual(phase, "transferring")
        _ = label


class KopiaFailureMessageTests(SimpleTestCase):
    def test_failure_metadata_groups_causes_and_limits_samples(self):
        from apps.protection.services.backup_task import kopia_snapshot_failure_metadata

        errors = [
            {"path": f"Library/Caches/private-{index}", "error": "cannot create iterator: operation not permitted"}
            for index in range(4)
        ] + [
            {"path": f".docker/run/docker-{index}.sock", "error": "unknown or unsupported entry type"}
            for index in range(3)
        ]
        metadata = kopia_snapshot_failure_metadata(
            {"snapshot": {"rootEntry": {"summ": {"errors": errors}}}},
        )["failure_details"]

        self.assertEqual(metadata["total_count"], 7)
        self.assertEqual(metadata["reported_count"], 5)
        self.assertTrue(metadata["truncated"])
        self.assertEqual(sum(item["count"] for item in metadata["causes"]), 7)
        self.assertLessEqual(len(metadata["items"]), 5)
        self.assertIn("enable_skip_unsupported_entries", metadata["remediation"])
        self.assertIn("grant_macos_full_disk_access", metadata["remediation"])
        self.assertEqual(metadata["remediation"][0], "enable_backup_policy")
        self.assertLess(
            metadata["remediation"].index("enable_skip_unreadable_directories"),
            metadata["remediation"].index("grant_macos_full_disk_access"),
        )

    def test_failure_metadata_recovers_total_from_truncated_kopia_output(self):
        from apps.protection.services.backup_task import kopia_snapshot_failure_metadata

        metadata = kopia_snapshot_failure_metadata({
            "snapshot_create": {
                "stdout": "{\"rootEntry\": {\"summ\": {\"errors\": [",
                "stderr_tail": "Found 795 fatal error(s) while snapshotting ghw@mini:/Users/ghw.",
                "stdout_truncated": True,
            },
        })["failure_details"]

        self.assertEqual(metadata["total_count"], 795)
        self.assertEqual(metadata["reported_count"], 0)
        self.assertEqual(metadata["causes"][0]["code"], "snapshot_errors")

    def test_failure_metadata_uses_compact_agent_summary_after_wire_truncation(self):
        from apps.protection.services.backup_task import (
            _directory_error,
            extract_kopia_failure_message,
            kopia_snapshot_failure_metadata,
        )

        result = {
            "result_truncated": True,
            "snapshot_failure_summary": {
                "total_count": 795,
                "items": [{
                    "path": "Library/Caches/com.apple.Safari",
                    "error": "open /Users/ghw/Library/Caches/com.apple.Safari: operation not permitted",
                }],
            },
        }
        metadata = kopia_snapshot_failure_metadata(result)["failure_details"]

        self.assertEqual(metadata["total_count"], 795)
        self.assertEqual(metadata["reported_count"], 1)
        self.assertEqual(metadata["causes"][0]["code"], "macos_privacy_denied")
        self.assertIn("795 source items", extract_kopia_failure_message(result))
        outcome = type(
            "Outcome",
            (),
            {
                "result": result,
                "task": type("AgentTask", (), {"last_error": "exit 1: exit status 1"})(),
            },
        )()
        self.assertEqual(_directory_error(outcome)[0], "KOPIA_SNAPSHOT_FATAL")

    def test_failure_metadata_uses_exact_agent_cause_counts(self):
        from apps.protection.services.backup_task import kopia_snapshot_failure_metadata

        metadata = kopia_snapshot_failure_metadata({
            "snapshot_failure_summary": {
                "total_count": 955,
                "cause_counts": {
                    "macos_privacy_denied": 900,
                    "unsupported_entry_type": 55,
                },
                "item_types": {
                    "macos_privacy_denied": "directory",
                    "unsupported_entry_type": "special",
                },
                "item_type_counts": {"directory": 900, "special": 55},
                "items": [
                    {
                        "path": "Library/Caches/com.apple.Safari",
                        "error": "cannot create iterator: operation not permitted",
                        "cause": "macos_privacy_denied",
                        "item_type": "directory",
                    },
                    {
                        "path": ".docker/run/docker.sock",
                        "error": "unknown or unsupported entry type",
                        "cause": "unsupported_entry_type",
                        "item_type": "special",
                    },
                ],
            },
        })["failure_details"]

        self.assertEqual(metadata["total_count"], 955)
        self.assertEqual(
            {item["code"]: item["count"] for item in metadata["causes"]},
            {"macos_privacy_denied": 900, "unsupported_entry_type": 55},
        )
        self.assertNotIn(
            "snapshot_errors", {item["code"] for item in metadata["causes"]}
        )
        self.assertEqual(
            metadata["remediation"][:3],
            [
                "enable_backup_policy",
                "enable_skip_unreadable_directories",
                "enable_skip_unsupported_entries",
            ],
        )

    def test_failure_metadata_uses_reported_total_when_samples_are_incomplete(self):
        from apps.protection.services.backup_task import kopia_snapshot_failure_metadata

        metadata = kopia_snapshot_failure_metadata({
            "snapshot": {
                "rootEntry": {
                    "summ": {
                        "errors": [{
                            "path": "Library/Caches/private",
                            "error": "cannot create iterator: operation not permitted",
                        }],
                    },
                },
            },
            "snapshot_create": {"stderr": "Found 795 fatal error(s)."},
        })["failure_details"]

        self.assertEqual(metadata["total_count"], 795)
        self.assertEqual(sum(item["count"] for item in metadata["causes"]), 795)

    def test_extracts_actionable_structured_windows_file_lock_failures(self):
        from apps.protection.services.backup_task import (
            _directory_error,
            extract_kopia_failure_message,
            extract_kopia_snapshot_failure_details,
            kopia_snapshot_failure_metadata,
        )

        result = {
            "snapshot": {
                "rootEntry": {
                    "summ": {
                        "numFailed": 2,
                        "errors": [
                            {
                                "path": "Veeam/PerfCache/cpu/LOCK",
                                "error": "read E:\\ProgramData\\Veeam\\PerfCache\\cpu\\LOCK: "
                                "The process cannot access the file because another process "
                                "has locked a portion of the file.",
                            },
                            {
                                "path": "Veeam/PerfCache/memory/LOCK",
                                "error": "read E:\\ProgramData\\Veeam\\PerfCache\\memory\\LOCK: "
                                "The process cannot access the file because another process "
                                "has locked a portion of the file.",
                            },
                        ],
                    }
                }
            },
            "snapshot_create": {"stderr": "Found 2 fatal errors."},
        }

        details = extract_kopia_snapshot_failure_details(result)
        self.assertEqual(len(details), 2)
        self.assertEqual(details[1]["path"], "Veeam/PerfCache/memory/LOCK")
        self.assertEqual(
            extract_kopia_failure_message(result, last_error="exit status 1"),
            "2 files could not be read because another process locked them. "
            "Review the failed file list and remediation guidance.",
        )
        metadata = kopia_snapshot_failure_metadata(result)["failure_details"]
        self.assertEqual(metadata["category"], "source_file_locked")
        self.assertEqual(metadata["count"], 2)
        self.assertEqual(len(metadata["items"]), 2)
        self.assertEqual(
            metadata["remediation"][:2],
            ["enable_backup_policy", "enable_skip_unreadable_files"],
        )
        outcome = type(
            "Outcome",
            (),
            {
                "result": result,
                "task": type("AgentTask", (), {"last_error": "exit status 1"})(),
            },
        )()
        code, _ = _directory_error(outcome)
        self.assertEqual(code, "SOURCE_FILE_LOCKED")

    def test_failure_metadata_guides_disabled_policy_skip_settings(self):
        from apps.protection.services.backup_task import (
            kopia_snapshot_failure_metadata,
        )

        result = {
            "snapshot": {
                "rootEntry": {
                    "summ": {
                        "errors": [
                            {"path": "locked.txt", "error": "sharing violation"},
                            {
                                "path": "private",
                                "error": "readdir private: access denied",
                            },
                        ]
                    }
                }
            }
        }
        metadata = kopia_snapshot_failure_metadata(
            result,
            backup_policy={
                "active": False,
                "advanced_settings": {
                    "enabled": False,
                    "skip_unreadable_files": False,
                    "skip_unreadable_directories": False,
                },
            },
        )["failure_details"]

        self.assertIn("enable_backup_policy", metadata["remediation"])
        self.assertIn("enable_skip_unreadable_files", metadata["remediation"])
        self.assertIn("enable_skip_unreadable_directories", metadata["remediation"])
        self.assertEqual(
            metadata["remediation"][:3],
            [
                "enable_backup_policy",
                "enable_skip_unreadable_files",
                "enable_skip_unreadable_directories",
            ],
        )

    def test_failure_metadata_keeps_generic_directory_permissions_platform_neutral(self):
        from apps.protection.services.backup_task import (
            kopia_snapshot_failure_metadata,
        )

        result = {
            "snapshot": {
                "rootEntry": {
                    "summ": {
                        "errors": [{
                            "path": "System Volume Information",
                            "error": "readdir System Volume Information: access denied",
                        }],
                    }
                }
            }
        }
        remediation = kopia_snapshot_failure_metadata(result)["failure_details"][
            "remediation"
        ]

        self.assertIn("check_source_access", remediation)
        self.assertIn("enable_skip_unreadable_directories", remediation)
        self.assertNotIn("enable_skip_unreadable_files", remediation)
        self.assertNotIn("grant_macos_full_disk_access", remediation)

    def test_skipped_metadata_preserves_paths_reasons_and_counts(self):
        from apps.protection.services.backup_task import (
            kopia_snapshot_skipped_metadata,
        )

        result = {
            "snapshot": {
                "rootEntry": {
                    "summ": {
                        "errors": [
                            {"path": "locked.txt", "error": "sharing violation"},
                            {
                                "path": "private",
                                "error": "readdir private: access denied",
                            },
                        ]
                    }
                }
            }
        }
        details = kopia_snapshot_skipped_metadata(result)["skipped_details"]

        self.assertEqual(details["count"], 2)
        self.assertEqual(details["file_count"], 1)
        self.assertEqual(details["directory_count"], 1)
        self.assertEqual(details["special_count"], 0)
        self.assertEqual(details["items"][1]["path"], "private")
        self.assertEqual(details["items"][1]["error"], "readdir private: access denied")

    def test_skipped_metadata_limits_event_details_to_ten_items(self):
        from apps.protection.services.backup_task import (
            kopia_snapshot_skipped_metadata,
        )

        errors = [
            {"path": f"locked-{index}.txt", "error": "sharing violation"}
            for index in range(25)
        ]
        result = {
            "snapshot": {"rootEntry": {"summ": {"errors": errors}}},
        }

        details = kopia_snapshot_skipped_metadata(result)["skipped_details"]

        self.assertEqual(details["count"], 25)
        self.assertEqual(details["reported_count"], 10)
        self.assertEqual(len(details["items"]), 10)
        self.assertTrue(details["truncated"])

    def test_skipped_metadata_uses_exact_summary_type_counts(self):
        from apps.protection.services.backup_task import (
            kopia_snapshot_skipped_metadata,
        )

        result = {
            "snapshot_failure_summary": {
                "total_count": 955,
                "reported_count": 2,
                "item_type_counts": {
                    "file": 584,
                    "directory": 329,
                    "special": 42,
                },
                "items": [
                    {"path": "Desktop", "error": "operation not permitted"},
                    {
                        "path": "runtime.sock",
                        "error": "unknown or unsupported entry type",
                    },
                ],
            }
        }

        details = kopia_snapshot_skipped_metadata(result)["skipped_details"]

        self.assertEqual(details["count"], 955)
        self.assertEqual(details["file_count"], 584)
        self.assertEqual(details["directory_count"], 329)
        self.assertEqual(details["special_count"], 42)
        self.assertEqual(details["reported_count"], 2)
        self.assertTrue(details["truncated"])

    def test_failed_snapshot_metrics_do_not_project_failure_samples_as_skipped(self):
        from apps.protection.services.backup_task import _extract_snapshot_metrics

        result = {
            "kopia_snapshot_id": "partial-snapshot",
            "snapshot_failure_summary": {
                "total_count": 955,
                "reported_count": 2,
                "items": [
                    {
                        "path": "Desktop",
                        "error": "unable to read directory: operation not permitted",
                    },
                    {
                        "path": "runtime.sock",
                        "error": "unknown or unsupported entry type",
                    },
                ],
            },
        }

        *_, stats = _extract_snapshot_metrics(
            result,
            include_skipped_items=False,
        )

        self.assertNotIn("skipped_item_count", stats)
        self.assertNotIn("skipped_file_count", stats)
        self.assertNotIn("skipped_directory_count", stats)

    def test_extract_kopia_failure_message_prefers_failed_policy_over_status(self):
        from apps.protection.services.backup_task import (
            extract_kopia_failure_message,
            public_repository_failure_message,
        )

        result = {
            "error_code": "POLICY_APPLY_FAILED",
            "policy_phase": "reset",
            "policy_reset": {
                "stderr_tail": (
                    "Setting policy for root@jlb35:/opt/hyperfilelens-agent\n"
                    "unable to write diagnostics blob despite 10 retries: "
                    "Bucket quota exceeded\n"
                    "error flushing writer: unable to write session marker"
                ),
                "exit_code": 1,
            },
            "repository_status": {
                "stdout_tail": "Epoch range-compaction every: 7 epochs",
                "exit_code": 0,
            },
        }

        message = extract_kopia_failure_message(
            result, last_error="reset backup policy: exit 1: exit status 1"
        )

        self.assertIn("Bucket quota exceeded", message)
        self.assertNotIn("Epoch range-compaction", message)
        self.assertEqual(
            public_repository_failure_message(message),
            "The underlying storage rejected the backup because its capacity "
            "or provider-side quota was reached. Free space or increase the "
            "quota on the NAS or object-storage platform, then retry.",
        )

    def test_public_repository_failure_message_classifies_provider_capacity_markers(
        self,
    ):
        from apps.protection.services.backup_task import (
            extract_kopia_failure_message,
            public_repository_failure_message,
        )

        for marker in ("ENOSPC", "no space left on device", "storage limit reached"):
            with self.subTest(marker=marker):
                extracted = extract_kopia_failure_message(
                    {"stderr_tail": f"write failed: {marker}", "exit_code": 1},
                    last_error="exit 1: exit status 1",
                )
                self.assertIn(marker, extracted)
                public_message = public_repository_failure_message(extracted)
                self.assertIn("underlying storage", public_message)
                self.assertIn("NAS or object-storage platform", public_message)

    def test_extract_kopia_failure_message_prefers_fatal_errors(self):
        from apps.protection.services.backup_task import extract_kopia_failure_message

        result = {
            "stderr_tail": (
                "Snapshotting root@zjb:/share ...\n"
                ' ! Error when processing "hp-repos/storage-3/kopia.blobcfg.f": '
                "unable to open file: resource temporarily unavailable\n"
                "Found 3 fatal error(s) while snapshotting root@zjb:/share."
            ),
            "exit_code": 1,
        }
        message = extract_kopia_failure_message(
            result, last_error="exit 1: exit status 1"
        )
        self.assertIn("hp-repos/storage-3/kopia.blobcfg.f", message)
        self.assertIn("resource temporarily unavailable", message)
        self.assertNotEqual(message, "exit 1: exit status 1")

    def test_extract_kopia_failure_message_prefers_access_denied_over_log_file_error(
        self,
    ):
        from apps.protection.services.backup_task import extract_kopia_failure_message

        result = {
            "repository_connect": {
                "stderr": (
                    "failed to open repository: unable to establish session: "
                    "rpc error: code = PermissionDenied desc = access denied for hfl-backup@proxy: EOF\n"
                    "write error: unable to open log file: open /opt/hyperfilelens-agent/cache/kopia/cli-logs/x.log: no such file or directory"
                )
            }
        }
        message = extract_kopia_failure_message(
            result, last_error="exit 1: exit status 1"
        )
        self.assertIn("access denied", message)
        self.assertIn("PermissionDenied", message)

    def test_extract_kopia_failure_message_keeps_repository_connect_failure(self):
        from apps.protection.services.backup_task import extract_kopia_failure_message

        result = {
            "repository_connect": {
                "stderr": "error connecting to repository: repository not initialized in the provided storage"
            },
            "repository_status": {
                "stderr": "open repository: repository is not connected. See https://kopia.io/docs/repositories/"
            },
        }
        message = extract_kopia_failure_message(
            result, last_error="exit 1: exit status 1"
        )
        self.assertIn("repository not initialized", message)

    def test_public_repository_failure_message_hides_internal_details(self):
        from apps.protection.services.backup_task import (
            public_repository_failure_message,
        )

        message = public_repository_failure_message(
            "open repository: repository is not connected. See https://kopia.io/docs/repositories/"
        )
        self.assertEqual(
            message,
            "Backup repository is not connected. Check the repository and retry.",
        )
        self.assertNotIn("kopia", message.lower())
        self.assertNotIn("http", message.lower())

    def test_is_generic_exit_message(self):
        from apps.protection.services.backup_task import _is_generic_exit_message

        self.assertTrue(_is_generic_exit_message("exit 1: exit status 1"))
        self.assertFalse(_is_generic_exit_message('Error when processing "foo": boom'))
