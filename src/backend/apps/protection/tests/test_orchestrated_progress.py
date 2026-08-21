from __future__ import annotations

from django.test import SimpleTestCase
from django.utils import timezone

from apps.protection.services.progress.orchestrated_progress import (
    BACKUP_ESTIMATE_END,
    BACKUP_PREPARE_END,
    BACKUP_TRANSFER_END,
    BACKUP_TRANSFER_START,
    RESTORE_PREPARE_END,
    RESTORE_TRANSFER_END,
    RESTORE_TRANSFER_START,
    merge_transfer_progress,
    orchestrated_task_percent,
    slim_transfer_progress,
)


class _TaskStub:
    def __init__(self, *, status: str = "running", progress: float = 0.0, current_step: str = "") -> None:
        self.status = status
        self.progress = progress
        self.current_step = current_step


class OrchestratedProgressTests(SimpleTestCase):
    def test_backup_transferring_maps_kopia_percent_into_task_range(self):
        task = _TaskStub(current_step="kopia_snapshot")
        kopia_payload = {
            "orchestration_phase": "transferring",
            "aggregate": {"percent": 50.0, "bytes_total_known": True},
            "percent_source": "kopia",
            "display_percent": 50.0,
        }
        percent = orchestrated_task_percent(task=task, kopia_payload=kopia_payload, kind="backup")
        expected = BACKUP_TRANSFER_START + 0.5 * (BACKUP_TRANSFER_END - BACKUP_TRANSFER_START)
        self.assertAlmostEqual(percent, expected, places=2)

    def test_backup_transferring_never_decreases_when_kopia_percent_drops(self):
        task = _TaskStub(progress=13.07, current_step="kopia_snapshot")
        kopia_payload = {
            "orchestration_phase": "transferring",
            "aggregate": {"percent": 0.25, "bytes_total_known": True},
            "percent_source": "computed",
        }
        percent = orchestrated_task_percent(task=task, kopia_payload=kopia_payload, kind="backup")
        self.assertGreaterEqual(percent, 13.07)

    def test_backup_estimating_creeps_over_time(self):
        task = _TaskStub(progress=BACKUP_PREPARE_END, current_step="kopia_snapshot")
        started_at = timezone.now().isoformat()
        kopia_payload = {"orchestration_phase": "estimating", "aggregate": {}}
        transfer = {"estimating_started_at": started_at, "phase": "estimating"}
        percent = orchestrated_task_percent(
            task=task,
            kopia_payload=kopia_payload,
            kind="backup",
            transfer=transfer,
        )
        self.assertGreaterEqual(percent, BACKUP_PREPARE_END)
        self.assertLessEqual(percent, BACKUP_ESTIMATE_END)

    def test_restore_transferring_maps_kopia_percent(self):
        task = _TaskStub(current_step="restore")
        kopia_payload = {
            "orchestration_phase": "transferring",
            "aggregate": {"percent": 40.0, "bytes_total_known": True},
            "percent_source": "kopia",
            "display_percent": 40.0,
        }
        percent = orchestrated_task_percent(task=task, kopia_payload=kopia_payload, kind="restore")
        expected = RESTORE_TRANSFER_START + 0.4 * (RESTORE_TRANSFER_END - RESTORE_TRANSFER_START)
        self.assertAlmostEqual(percent, expected, places=2)

    def test_restore_preparing_uses_prepare_end(self):
        task = _TaskStub(progress=0, current_step="dispatch_agent")
        kopia_payload = {"orchestration_phase": "preparing", "aggregate": {}}
        percent = orchestrated_task_percent(task=task, kopia_payload=kopia_payload, kind="restore")
        self.assertEqual(percent, RESTORE_PREPARE_END)

    def test_slim_transfer_progress_extracts_metrics(self):
        payload = slim_transfer_progress(
            {
                "orchestration_phase": "transferring",
                "orchestration_label_key": "protection.taskProgress.backup.uploading",
                "orchestration_label_args": {"done": 0, "total": 1},
                "show_metrics": True,
                "aggregate": {
                    "percent": 42.0,
                    "bytes_done": 4,
                    "bytes_total": 10,
                    "bytes_total_known": True,
                    "speed_bps": 1000,
                    "eta_seconds": 30,
                    "lanes_done": 0,
                    "lanes_total": 1,
                },
            }
        )
        self.assertEqual(payload["phase"], "transferring")
        self.assertEqual(payload["label_key"], "protection.taskProgress.backup.uploading")
        self.assertEqual(payload["speed_bps"], 1000)
        self.assertTrue(payload["show_metrics"])
        self.assertNotIn("transfer_percent", payload)

    def test_merge_transfer_progress_preserves_estimating_started_at(self):
        now = timezone.now().isoformat()
        merged = merge_transfer_progress(
            previous={"phase": "estimating", "estimating_started_at": now},
            current={"phase": "estimating", "label": "Scanning and estimating..."},
        )
        self.assertEqual(merged["estimating_started_at"], now)

    def test_merge_transfer_progress_preserves_unknown_total_started_at(self):
        now = timezone.now().isoformat()
        merged = merge_transfer_progress(
            previous={
                "phase": "transferring",
                "bytes_total_known": False,
                "unknown_total_started_at": now,
            },
            current={"phase": "transferring", "bytes_total_known": False},
        )
        self.assertEqual(merged["unknown_total_started_at"], now)

    def test_merge_transfer_progress_keeps_monotonic_bytes_during_transfer(self):
        merged = merge_transfer_progress(
            previous={
                "phase": "transferring",
                "bytes_done": 9_500_000,
                "bytes_total": 563_000_000,
                "bytes_total_known": True,
                "display_percent": 1.69,
                "upload_speed_bps": 500_000,
                "eta_seconds": 30,
                "show_metrics": True,
            },
            current={
                "phase": "transferring",
                "bytes_done": 0,
                "bytes_total": 563_000_000,
                "bytes_total_known": True,
                "display_percent": 0.0,
                "show_metrics": True,
            },
        )
        self.assertEqual(merged["bytes_done"], 9_500_000)
        self.assertEqual(merged["display_percent"], 1.69)
        self.assertIsNone(merged.get("upload_speed_bps"))
        self.assertIsNone(merged.get("eta_seconds"))
