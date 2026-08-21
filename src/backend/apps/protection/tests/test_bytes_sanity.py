from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from apps.protection.services.progress.bytes_sanity import (
    apply_reference_bytes_total,
    credible_bytes_total,
    monotonic_bytes_total,
)
from apps.protection.services.progress.lane_sampler import apply_speed_and_eta
from apps.protection.services.progress.orchestration_label import backup_orchestration_label_meta


class BytesSanityTests(SimpleTestCase):
    def test_rejects_total_smaller_than_done(self):
        self.assertFalse(credible_bytes_total(bytes_done=6_400_000_000, bytes_total=3))

    def test_monotonic_total_never_below_done(self):
        total = monotonic_bytes_total(
            bytes_done=6_400_000_000,
            bytes_total=3,
            previous_max=7_000_000_000,
        )
        self.assertEqual(total, 7_000_000_000)

    def test_apply_reference_replaces_inflated_estimate(self):
        reference = 2_000_000_000
        total, replaced = apply_reference_bytes_total(
            bytes_total=206_400_000_000,
            reference_bytes_total=reference,
        )
        self.assertTrue(replaced)
        self.assertEqual(total, reference)

    def test_monotonic_allows_decrease_to_reference(self):
        reference = 2_200_000_000
        total = monotonic_bytes_total(
            bytes_done=561_000_000,
            bytes_total=206_400_000_000,
            previous_max=206_400_000_000,
            reference_bytes_total=reference,
        )
        self.assertEqual(total, reference)
        self.assertTrue(credible_bytes_total(bytes_done=561_000_000, bytes_total=total))


class LaneSamplerTests(SimpleTestCase):
    def test_read_only_path_preserves_sample_timestamp(self):
        sample = {
            "counter": 1000,
            "sampled_at": "2026-07-02T10:00:00+00:00",
            "_max_bytes_total": 5000,
        }
        result = apply_speed_and_eta(
            lane={
                "kopia_phase": "uploading",
                "uploaded_bytes": 1000,
                "bytes_done": 1000,
                "bytes_total_known": False,
            },
            sample=sample,
            persist_sample=False,
        )
        self.assertEqual(result["last_sample"]["sampled_at"], sample["sampled_at"])
        self.assertEqual(result["last_sample"]["counter"], 1000)

    def test_eta_prefers_computed_when_kopia_eta_too_short_for_total(self):
        bytes_total = 204 * 1000 * 1000 * 1000
        bytes_done = 28 * 1000 * 1000
        speed_bps = int(2.68 * 1000 * 1000)
        result = apply_speed_and_eta(
            lane={
                "kopia_phase": "uploading",
                "uploaded_bytes": bytes_done,
                "bytes_done": bytes_done,
                "bytes_total": bytes_total,
                "bytes_total_known": True,
                "speed_bps": speed_bps,
                "kopia_eta_seconds": 46,
            },
            sample=None,
            persist_sample=False,
        )
        self.assertEqual(result["eta_source"], "computed")
        remaining = bytes_total - bytes_done
        expected = int(remaining / speed_bps)
        self.assertEqual(result["eta_seconds"], expected)
        self.assertGreater(result["eta_seconds"], 3600)

    def test_eta_keeps_credible_kopia_eta_when_total_unknown(self):
        result = apply_speed_and_eta(
            lane={
                "kopia_phase": "hashing",
                "hashed_bytes": 500_000_000,
                "bytes_done": 500_000_000,
                "bytes_total_known": False,
                "speed_bps": 10_000_000,
                "kopia_eta_seconds": 120,
            },
            sample=None,
            persist_sample=False,
        )
        self.assertEqual(result["eta_source"], "kopia")
        self.assertEqual(result["eta_seconds"], 120)

    def test_schema_v2_eta_uses_processing_speed_not_upload_speed(self):
        result = apply_speed_and_eta(
            lane={
                "progress_schema_version": 2,
                "kopia_phase": "processing",
                "processed_bytes": 3_478_373_863,
                "uploaded_bytes": 270_077_614,
                "bytes_done": 3_478_373_863,
                "bytes_total": 4_130_621_356,
                "bytes_total_known": True,
                "processing_speed_bps": 200_000_000,
                "upload_speed_bps": 20_000_000,
            },
            sample=None,
            persist_sample=False,
        )
        self.assertEqual(result["eta_source"], "computed")
        self.assertEqual(result["eta_seconds"], 3)
        self.assertEqual(result["speed_bps"], 20_000_000)

    def test_fresh_zero_upload_speed_is_distinct_from_unknown(self):
        now = timezone.now()
        result = apply_speed_and_eta(
            lane={
                "progress_schema_version": 2,
                "kopia_phase": "processing",
                "processed_bytes": 2_000,
                "uploaded_bytes": 192,
                "bytes_done": 2_000,
                "bytes_total_known": False,
                "upload_speed_bps": 0,
                "metrics_sampled_at": now.isoformat(),
            },
            sample=None,
            now=now,
            persist_sample=False,
        )
        self.assertEqual(result["upload_speed_bps"], 0)
        self.assertEqual(result["speed_bps"], 0)

    def test_stale_speed_and_eta_expire(self):
        now = timezone.now()
        sampled_at = now - timedelta(seconds=7)
        result = apply_speed_and_eta(
            lane={
                "progress_schema_version": 2,
                "kopia_phase": "processing",
                "processed_bytes": 2_000,
                "uploaded_bytes": 100,
                "bytes_done": 2_000,
                "bytes_total": 10_000,
                "bytes_total_known": True,
                "processing_speed_bps": 1_000,
                "upload_speed_bps": 100,
                "kopia_eta_seconds": 8,
                "metrics_sampled_at": sampled_at.isoformat(),
            },
            sample={"sampled_at": sampled_at.isoformat(), "processing_counter": 2_000},
            now=now,
            persist_sample=False,
        )
        self.assertIsNone(result["processing_speed_bps"])
        self.assertIsNone(result["upload_speed_bps"])
        self.assertIsNone(result["eta_seconds"])

    def test_finalizing_never_reports_eta(self):
        result = apply_speed_and_eta(
            lane={
                "progress_schema_version": 2,
                "kopia_phase": "finalizing",
                "processed_bytes": 10_000,
                "bytes_done": 10_000,
                "bytes_total": 10_000,
                "bytes_total_known": True,
                "processing_speed_bps": 1_000,
                "kopia_eta_seconds": 0,
            },
            sample=None,
            persist_sample=False,
        )
        self.assertIsNone(result["eta_seconds"])
        self.assertIsNone(result["eta_source"])

    def test_new_agent_sample_is_fresh_despite_clock_skew(self):
        now = timezone.now()
        agent_time = now - timedelta(minutes=2)
        result = apply_speed_and_eta(
            lane={
                "progress_schema_version": 2,
                "kopia_phase": "processing",
                "processed_bytes": 2_000,
                "uploaded_bytes": 100,
                "bytes_done": 2_000,
                "bytes_total_known": False,
                "processing_speed_bps": 1_000,
                "upload_speed_bps": 100,
                "metrics_sampled_at": agent_time.isoformat(),
            },
            sample=None,
            now=now,
            persist_sample=True,
        )
        self.assertEqual(result["processing_speed_bps"], 1_000)
        self.assertEqual(result["upload_speed_bps"], 100)
        self.assertEqual(result["last_sample"]["sampled_at"], now.isoformat())
        self.assertEqual(result["last_sample"]["counter_sampled_at"], agent_time.isoformat())


class OrchestrationLabelMetaTests(SimpleTestCase):
    def test_uploading_label_key(self):
        meta, phase = backup_orchestration_label_meta(
            task_status="running",
            lanes=[{
                "id": "1",
                "name": "/data",
                "status": "running",
                "progress": {
                    "is_transfer": True,
                    "kopia_phase": "uploading",
                    "bytes_done": 1,
                    "bytes_total_known": True,
                    "bytes_total": 10,
                },
            }],
            aggregate={"lanes_done": 0, "lanes_total": 1},
        )
        self.assertEqual(phase, "transferring")
        self.assertEqual(meta["label_key"], "protection.taskProgress.transfer.hashedOnly")

    def test_finalizing_kopia_phase_uses_finalizing_label(self):
        meta, phase = backup_orchestration_label_meta(
            task_status="running",
            lanes=[{
                "id": "1",
                "name": "/data",
                "status": "running",
                "progress": {
                    "is_transfer": True,
                    "kopia_phase": "finalizing",
                    "bytes_done": 10,
                    "bytes_total_known": True,
                    "bytes_total": 10,
                },
            }],
            aggregate={"lanes_done": 0, "lanes_total": 1},
        )
        self.assertEqual(phase, "finalizing")
        self.assertEqual(meta["label_key"], "protection.taskProgress.backup.finalizing")
