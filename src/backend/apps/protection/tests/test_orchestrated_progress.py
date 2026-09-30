from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.protection.services.progress.backup_runtime import (
    _active_comparison,
    build_backup_kopia_progress,
)
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
    def test_parallel_comparison_remains_visible_during_upload_and_clears_when_done(self):
        started = timezone.now() - timedelta(seconds=10)
        lanes = [
            {"status": "running", "progress": {"kopia_phase": "uploading"}},
            {"status": "running", "progress": {
                "kopia_phase": "processing", "phase_started_at": started.isoformat(),
            }},
        ]
        aggregate = {"lanes_done": 0, "lanes_total": 2, "lanes_running": 2}

        comparison = _active_comparison(lanes, aggregate)
        self.assertEqual(comparison["label_key"], "protection.taskProgress.backup.comparing")
        self.assertEqual(comparison["label_args"], {"done": 0, "total": 2})
        self.assertEqual(comparison["phase_started_at"], started.isoformat())

        slim = slim_transfer_progress({
            "orchestration_phase": "transferring",
            "comparison": comparison,
            "aggregate": aggregate,
        })
        self.assertGreaterEqual(slim["comparison"]["phase_elapsed_seconds"], 10)

        lanes[1]["progress"]["kopia_phase"] = "uploading"
        self.assertIsNone(_active_comparison(lanes, aggregate))
        self.assertIsNone(slim_transfer_progress({
            "orchestration_phase": "transferring",
            "aggregate": aggregate,
        })["comparison"])

    def test_backup_runtime_includes_parallel_comparison_with_transfer_metrics(self):
        started = (timezone.now() - timedelta(seconds=10)).isoformat()
        rows = [
            {"status": "running", "progress": {
                "kopia_phase": "uploading", "is_transfer": True, "bytes_done": 123,
            }},
            {"status": "running", "progress": {
                "kopia_phase": "processing", "is_transfer": True,
                "phase_started_at": started, "bytes_done": 456,
            }},
        ]
        task = SimpleNamespace(status="running", current_step="kopia_snapshot", result_payload={})
        with (
            patch("apps.protection.services.progress.backup_runtime._directories_for_task", return_value=[1, 2]),
            patch("apps.protection.services.progress.backup_runtime._lane_from_directory", side_effect=rows),
            patch("apps.protection.services.progress.backup_runtime.du_total_for_task", return_value=0),
        ):
            payload = build_backup_kopia_progress(task=task)

        self.assertEqual(payload["orchestration_phase"], "transferring")
        self.assertEqual(payload["transfer_progress"]["comparison"]["label_args"], {"done": 0, "total": 2})
        self.assertEqual(payload["transfer_progress"]["bytes_done"], 579)

    def test_preparing_runtime_keeps_valid_bytes_from_same_task(self):
        task = SimpleNamespace(
            status="running",
            current_step="kopia_snapshot",
            result_payload={"transfer_progress": {
                "phase": "transferring",
                "progress_schema_version": 2,
                "bytes_done": 900_000_000,
                "processed_bytes": 900_000_000,
                "bytes_total": 2_000_000_000,
                "bytes_total_known": True,
                "bytes_total_source": "kopia",
                "kopia_total_locked": 2_000_000_000,
                "step3_display_percent": 45,
            }},
        )
        lane = {
            "status": "running",
            "progress": {
                "orchestration_label": "Preparing backup...",
                "kopia_phase": "repository_prepare",
                "is_transfer": False,
            },
        }
        with (
            patch("apps.protection.services.progress.backup_runtime._directories_for_task", return_value=[1]),
            patch("apps.protection.services.progress.backup_runtime._lane_from_directory", return_value=lane),
            patch("apps.protection.services.progress.backup_runtime.du_total_for_task", return_value=0),
        ):
            payload = build_backup_kopia_progress(task=task)

        transfer = payload["transfer_progress"]
        self.assertEqual(transfer["phase"], "preparing")
        self.assertEqual(transfer["label_key"], "protection.taskProgress.backup.preparing")
        self.assertEqual(transfer["processed_bytes"], 900_000_000)
        self.assertEqual(transfer["bytes_total"], 2_000_000_000)
        self.assertEqual(transfer["step3_display_percent"], 45)

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

    def test_backup_queued_stays_at_prepare_progress(self):
        task = _TaskStub(progress=BACKUP_PREPARE_END, current_step="kopia_snapshot")

        percent = orchestrated_task_percent(
            task=task,
            kopia_payload={"orchestration_phase": "queued", "aggregate": {}},
            kind="backup",
        )

        self.assertEqual(percent, BACKUP_PREPARE_END)

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
                    "phase_started_at": "2026-09-23T08:00:00+00:00",
                    "last_progress_at": "2026-09-23T08:01:00+00:00",
                },
            }
        )
        self.assertEqual(payload["phase"], "transferring")
        self.assertEqual(payload["label_key"], "protection.taskProgress.backup.uploading")
        self.assertEqual(payload["speed_bps"], 1000)
        self.assertTrue(payload["show_metrics"])
        self.assertEqual(payload["phase_started_at"], "2026-09-23T08:00:00+00:00")
        self.assertEqual(payload["last_progress_at"], "2026-09-23T08:01:00+00:00")
        self.assertIsInstance(payload["phase_elapsed_seconds"], int)
        self.assertNotIn("transfer_percent", payload)

    def test_merge_transfer_progress_preserves_phase_timing_for_same_phase(self):
        started_at = "2026-09-23T08:00:00+00:00"
        merged = merge_transfer_progress(
            previous={
                "phase": "transferring",
                "phase_started_at": started_at,
                "last_progress_at": "2026-09-23T08:01:00+00:00",
                "phase_elapsed_seconds": 60,
            },
            current={"phase": "transferring"},
        )

        self.assertEqual(merged["phase_started_at"], started_at)
        self.assertEqual(merged["last_progress_at"], "2026-09-23T08:01:00+00:00")
        self.assertEqual(merged["phase_elapsed_seconds"], 60)

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
