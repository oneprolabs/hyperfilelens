from __future__ import annotations

from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from apps.protection.services.progress.step3_progress import (
    compute_step3_display_percent,
    compute_step3_eta_seconds,
    enrich_step3_backup_transfer,
    enrich_step3_restore_transfer,
    should_latch_kopia_switch,
)


class Step3ProgressTests(SimpleTestCase):
    def test_display_percent_only_increases(self):
        first = compute_step3_display_percent(bytes_done=100, effective_total=1000, previous_display=None)
        second = compute_step3_display_percent(bytes_done=50, effective_total=1000, previous_display=first)
        self.assertEqual(first, 10.0)
        self.assertEqual(second, 10.0)

    def test_backup_switch_uses_du_then_kopia(self):
        now = timezone.now()
        history = []
        for index in range(12):
            at = now - timedelta(seconds=90 - index * 5)
            history.append({"at": at.isoformat(), "estimated_bytes": 1_000_000})
        transfer = enrich_step3_backup_transfer(
            transfer={"phase": "transferring", "upload_speed_bps": 1000},
            previous={"step3_display_percent": 5.0, "estimated_history": history},
            aggregate={
                "uploaded_bytes": 100_000,
                "uploaded_count": 10,
                "hashed_count": 20,
                "estimated_bytes": 1_000_000,
                "bytes_total_known": True,
            },
            du_total=2_000_000,
            now=now,
        )
        self.assertTrue(transfer["switch_latched"])
        self.assertEqual(transfer["bytes_total"], 1_000_000)
        self.assertEqual(transfer["bytes_done"], 100_000)
        self.assertGreaterEqual(float(transfer["step3_display_percent"]), 5.0)

    def test_backup_before_switch_uses_du_total(self):
        transfer = enrich_step3_backup_transfer(
            transfer={"phase": "transferring"},
            previous={},
            aggregate={
                "uploaded_bytes": 50_000,
                "uploaded_count": 0,
                "hashed_count": 12,
                "estimated_bytes": 900_000,
            },
            du_total=2_000_000,
        )
        self.assertFalse(transfer["switch_latched"])
        self.assertEqual(transfer["bytes_total"], 2_000_000)
        self.assertTrue(transfer["bytes_total_estimated"])

    def test_backup_without_du_total_degrades_progress(self):
        transfer = enrich_step3_backup_transfer(
            transfer={"phase": "estimating", "upload_speed_bps": 0},
            previous={},
            aggregate={
                "uploaded_bytes": 0,
                "uploaded_count": 0,
                "hashed_count": 0,
                "estimated_bytes": 0,
            },
            du_total=0,
        )
        self.assertFalse(transfer["switch_latched"])
        self.assertFalse(transfer["bytes_total_known"])
        self.assertFalse(transfer["bytes_total_estimated"])
        self.assertNotIn("bytes_total", transfer)
        self.assertIsNone(transfer.get("step3_display_percent"))
        self.assertIsNone(transfer.get("eta_seconds"))

    def test_backup_reconnect_keeps_cumulative_transfer_counters(self):
        transfer = enrich_step3_backup_transfer(
            transfer={"phase": "transferring", "uploaded_bytes": 0},
            previous={
                "phase": "transferring",
                "uploaded_bytes": 5_000_000,
                "bytes_done": 5_000_000,
                "uploaded_count": 40,
                "hashed_count": 80,
                "estimated_bytes": 12_500_000,
                "step3_display_percent": 40.0,
            },
            aggregate={
                "uploaded_bytes": 0,
                "uploaded_count": 0,
                "hashed_count": 0,
                "estimated_bytes": 0,
            },
            du_total=12_500_000,
        )

        self.assertEqual(transfer["bytes_done"], 5_000_000)
        self.assertEqual(transfer["uploaded_bytes"], 5_000_000)
        self.assertEqual(transfer["uploaded_count"], 40)
        self.assertEqual(transfer["hashed_count"], 80)
        self.assertEqual(transfer["estimated_bytes"], 12_500_000)
        self.assertEqual(transfer["step3_display_percent"], 40.0)

    def test_backup_keeps_task_scoped_source_total(self):
        first = enrich_step3_backup_transfer(
            transfer={"phase": "transferring"},
            previous={},
            aggregate={"uploaded_bytes": 100_000_000},
            du_total=2_000_000_000,
        )
        second = enrich_step3_backup_transfer(
            transfer={"phase": "transferring"},
            previous=first,
            aggregate={"uploaded_bytes": 200_000_000},
            du_total=3_000_000_000,
        )

        self.assertEqual(second["du_total"], 2_000_000_000)
        self.assertEqual(second["bytes_total"], 2_000_000_000)

    def test_should_latch_requires_uploaded_and_stable_estimate(self):
        now = timezone.now()
        history = [{"at": (now - timedelta(seconds=index)).isoformat(), "estimated_bytes": 1_050_000} for index in range(12)]
        self.assertTrue(
            should_latch_kopia_switch(
                uploaded_bytes=100_000,
                estimated_bytes=1_050_000,
                history=history,
                now=now,
            )
        )
        self.assertFalse(
            should_latch_kopia_switch(
                uploaded_bytes=0,
                estimated_bytes=1_050_000,
                history=history,
                now=now,
            )
        )

    def test_restore_items_monotonic_and_label_synced(self):
        transfer = enrich_step3_restore_transfer(
            transfer={"phase": "transferring"},
            previous={
                "processed_count": 7994,
                "total_count": 17322,
                "path_index": 1,
                "path_total": 1,
            },
            aggregate={
                "bytes_done": 0,
                "processed_count": 0,
                "total_count": 1,
                "path_index": 1,
                "path_total": 1,
            },
            bytes_total=263 * 1_000_000,
            file_count_seed=17322,
        )
        self.assertEqual(transfer["processed_count"], 7994)
        self.assertEqual(transfer["total_count"], 17322)
        self.assertEqual(transfer["label_args"]["done"], 7994)
        self.assertEqual(transfer["label_args"]["total"], 17322)
        self.assertIsNone(transfer.get("eta_seconds"))

    def test_restore_bytes_done_monotonic(self):
        transfer = enrich_step3_restore_transfer(
            transfer={"phase": "transferring", "bytes_done": 43_300_000},
            previous={"bytes_done": 43_300_000, "step3_display_percent": 16.46},
            aggregate={"bytes_done": 0, "processed_count": 11122, "total_count": 17322},
            bytes_total=263 * 1_000_000,
            file_count_seed=17322,
        )
        self.assertEqual(transfer["bytes_done"], 43_300_000)
        self.assertEqual(transfer["step3_display_percent"], 16.46)

    def test_restore_items_path_fields(self):
        transfer = enrich_step3_restore_transfer(
            transfer={"phase": "transferring", "label_key": "protection.taskProgress.restore.itemsPath"},
            previous={},
            aggregate={
                "bytes_done": 10_000,
                "processed_count": 3,
                "total_count": 10,
                "path_index": 2,
                "path_total": 5,
                "upload_speed_bps": 10_000,
            },
            bytes_total=500_000,
            file_count_seed=100,
        )
        self.assertEqual(transfer["processed_count"], 3)
        self.assertEqual(transfer["path_index"], 2)
        self.assertEqual(transfer["bytes_total"], 500_000)
        self.assertEqual(transfer["step3_display_percent"], 2.0)
        self.assertEqual(transfer["eta_source"], "step3")
        self.assertEqual(transfer["eta_seconds"], 49)

    def test_backup_eta_matches_capacity_and_upload_speed(self):
        uploaded = 138 * 1_000_000
        du_total = 263 * 1_000_000
        speed_bps = int(1.49 * 1_000_000)
        transfer = enrich_step3_backup_transfer(
            transfer={"phase": "transferring", "upload_speed_bps": speed_bps},
            previous={},
            aggregate={
                "uploaded_bytes": uploaded,
                "uploaded_count": 0,
                "hashed_count": 15_603,
                "estimated_bytes": 200 * 1_000_000,
                "upload_speed_bps": speed_bps,
                "eta_seconds": 33,
                "eta_source": "kopia",
            },
            du_total=du_total,
        )
        self.assertEqual(transfer["eta_source"], "step3")
        expected = compute_step3_eta_seconds(
            bytes_done=uploaded,
            bytes_total=du_total,
            upload_speed_bps=speed_bps,
        )
        self.assertEqual(transfer["eta_seconds"], expected)
        self.assertGreater(transfer["eta_seconds"], 60)

    def test_backup_hides_eta_without_reliable_speed(self):
        transfer = enrich_step3_backup_transfer(
            transfer={"phase": "transferring", "eta_seconds": 33},
            previous={},
            aggregate={
                "uploaded_bytes": 50_000_000,
                "hashed_count": 100,
                "eta_seconds": 33,
            },
            du_total=200_000_000,
        )
        self.assertIsNone(transfer.get("eta_seconds"))
        self.assertIsNone(transfer.get("eta_source"))
