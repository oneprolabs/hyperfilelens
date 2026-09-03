import json
from datetime import UTC, datetime

from django.test import SimpleTestCase

from apps.storage.services.internal.repository_maintenance_summary import (
    maintenance_summary_from_result,
    parse_maintenance_info_summary,
    parse_maintenance_stderr_summary,
)


class RepositoryMaintenanceSummaryTests(SimpleTestCase):
    def test_uses_only_current_successful_maintenance_runs(self):
        payload = {
            "schedule": {
                "runs": {
                    "snapshot-gc": [
                        {
                            "start": "2026-09-01T04:00:00Z",
                            "success": True,
                            "extra": [
                                {
                                    "kind": "snapshotGCStats",
                                    "data": {"deletedContentCount": 999},
                                }
                            ],
                        },
                        {
                            "start": "2026-09-02T07:43:41Z",
                            "success": True,
                            "extra": [
                                {
                                    "kind": "snapshotGCStats",
                                    "data": {
                                        "unreferencedContentCount": 55,
                                        "unreferencedContentSize": 3_145_728,
                                        "deletedContentCount": 55,
                                        "deletedContentSize": 3_145_728,
                                        "unreferencedRecentContentCount": 7_214,
                                        "unreferencedRecentContentSize": 7_408_351_232,
                                        "inUseContentCount": 78_672,
                                        "inUseContentSize": 1_288_490_188,
                                        "inUseSystemContentCount": 341,
                                        "inUseSystemContentSize": 223_600,
                                    },
                                }
                            ],
                        },
                    ],
                    "full-delete-blobs": [
                        {
                            "start": "2026-09-02T07:43:50Z",
                            "success": True,
                            "extra": [
                                {
                                    "kind": "deleteUnreferencedPacksStats",
                                    "data": {
                                        "unreferencedPackCount": 12,
                                        "unreferencedTotalSize": 12_000,
                                        "deletedPackCount": 10,
                                        "deletedTotalSize": 10_000,
                                        "retainedPackCount": 2,
                                        "retainedTotalSize": 2_000,
                                    },
                                }
                            ],
                        }
                    ],
                }
            }
        }

        summary = parse_maintenance_info_summary(
            json.dumps(payload),
            mode="full",
            started_at=datetime(2026, 9, 2, 7, 43, 40, tzinfo=UTC),
        )

        self.assertEqual(summary["source"], "maintenance_info")
        self.assertFalse(summary["approximate"])
        self.assertEqual(summary["content_gc"]["deleted_count"], 55)
        self.assertEqual(summary["content_gc"]["deferred_count"], 7_214)
        self.assertEqual(summary["pack_gc"]["deleted_bytes"], 10_000)

    def test_pack_gc_is_absent_when_current_cycle_skipped_pack_deletion(self):
        payload = {
            "schedule": {
                "runs": {
                    "snapshot-gc": [
                        {
                            "start": "2026-09-02T07:43:41Z",
                            "success": True,
                            "extra": [
                                {
                                    "kind": "snapshotGCStats",
                                    "data": {"inUseContentCount": 3},
                                }
                            ],
                        }
                    ],
                    "full-delete-blobs": [
                        {
                            "start": "2026-09-01T04:00:00Z",
                            "success": True,
                            "extra": [
                                {
                                    "kind": "deleteUnreferencedPacksStats",
                                    "data": {"deletedPackCount": 99},
                                }
                            ],
                        }
                    ],
                }
            }
        }

        summary = parse_maintenance_info_summary(
            json.dumps(payload),
            mode="full",
            started_at=datetime(2026, 9, 2, 7, 43, 40, tzinfo=UTC),
        )

        self.assertIsNone(summary["pack_gc"])

    def test_stderr_fallback_is_approximate_and_has_no_pack_claim(self):
        summary = parse_maintenance_stderr_summary(
            "\n".join(
                [
                    "GC found 55 unused contents (3 MB)",
                    "GC found 7214 unused contents that are too recent to delete (6.9 GB)",
                    "GC found 78672 in-use contents (1.2 GB)",
                    "GC found 341 in-use system-contents (223.6 KB)",
                    "GC undeleted 0 contents (0 B)",
                ]
            ),
            mode="full",
        )

        self.assertTrue(summary["approximate"])
        self.assertEqual(summary["source"], "stderr")
        self.assertEqual(summary["content_gc"]["deleted_bytes"], 3 * 1024**2)
        self.assertEqual(summary["content_gc"]["deferred_count"], 7_214)
        self.assertIsNone(summary["pack_gc"])

    def test_old_agent_result_falls_back_to_nested_maintenance_stderr(self):
        summary = maintenance_summary_from_result(
            {
                "operation_type": "maintenance.full",
                "maintenance": {
                    "stderr": "GC found 4 in-use contents (12 MB)",
                },
            },
            mode="full",
        )

        self.assertEqual(summary["content_gc"]["in_use_count"], 4)
        self.assertEqual(summary["source"], "stderr")
