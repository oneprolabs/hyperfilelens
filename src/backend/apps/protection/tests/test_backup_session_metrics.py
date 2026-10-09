from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.protection.api.serializers.backup_source_snapshot import (
    BackupSourceSnapshotListSerializer,
)
from apps.protection.services.backup_task import _extract_snapshot_metrics
from apps.protection.services.progress.aggregator import aggregate_lanes
from apps.protection.services.progress.backup_runtime import _has_substantive_progress_change
from apps.protection.services.progress.kopia_fields import normalize_lane_progress


class BackupSessionMetricsTests(SimpleTestCase):
    def test_optional_backup_counters_aggregate_without_restore_classification(self):
        lanes = []
        for entries, hashed, cached in [(3, 4, 6), (5, 2, 8)]:
            progress = normalize_lane_progress(
                status="running",
                progress={
                    "phase": "kopia_transfer", "kopia_phase": "processing",
                    "progress_schema_version": 2, "processed_bytes": hashed + cached,
                    "hashed_bytes": hashed, "cached_bytes": cached,
                    "hashed_count": 1, "cached_count": entries - 1,
                    "processed_entry_count": entries, "estimated_file_count": entries,
                    "completed_directory_count": 1,
                },
            )
            self.assertEqual(progress["bytes_done"], hashed + cached)
            lanes.append({"status": "running", "progress": progress})
        totals = aggregate_lanes(lanes)
        self.assertEqual(totals["processed_entry_count"], 8)
        self.assertEqual(totals["hashed_bytes"], 6)
        self.assertEqual(totals["cached_bytes"], 14)
        lanes[1]["progress"].pop("cached_bytes")
        self.assertIsNone(aggregate_lanes(lanes)["cached_bytes"])

    def test_empty_entries_are_work_but_heartbeat_is_not(self):
        self.assertTrue(_has_substantive_progress_change(
            current={"processed_bytes": 0, "processed_entry_count": 2},
            previous={"processed_bytes": 0, "processed_entry_count": 1},
        ))
        self.assertFalse(_has_substantive_progress_change(
            current={"progress_sequence": 2}, previous={"progress_sequence": 1},
        ))

    def test_creation_basis_survives_metric_extraction(self):
        _, _, _, _, stats = _extract_snapshot_metrics({
            "kopia_snapshot_id": "one", "size_bytes": 40,
            "storage_stats_basis": "creation_session_data_v1",
            "new_original_content_bytes": 0, "new_packed_content_bytes": 0,
        })
        self.assertEqual(stats["storage_stats_basis"], "creation_session_data_v1")
        self.assertEqual(stats["new_original_content_bytes"], 0)

    def test_serializer_distinguishes_old_new_unavailable_and_mixed(self):
        old = SimpleNamespace(status="available", new_original_content_bytes=10,
                              new_packed_content_bytes=5, stats={}, size_bytes=40)
        new = SimpleNamespace(status="available", new_original_content_bytes=0,
                              new_packed_content_bytes=0,
                              stats={"storage_stats_basis": "creation_session_data_v1"}, size_bytes=40)
        serializer = BackupSourceSnapshotListSerializer()
        def obj(pk, rows):
            return SimpleNamespace(pk=pk, total_size_bytes=40,
                                   directories=SimpleNamespace(all=lambda: rows))
        self.assertEqual(serializer.get_storage_stats_basis(obj(1, [old])), "source_history")
        self.assertEqual(serializer.get_storage_stats_basis(obj(2, [new])), "creation_session_data_v1")
        self.assertEqual(serializer.get_storage_stats_basis(obj(3, [old, new])), "mixed")
        self.assertIsNone(serializer.get_compression_savings_ratio(obj(3, [old, new])))
        self.assertIsNone(serializer.get_compression_savings_ratio(obj(2, [new])))

    def test_stale_or_duplicate_sequence_does_not_replace_backup_sample(self):
        from apps.node.services.internal.task import _should_apply_progress_update
        task = SimpleNamespace(correlation_type="protection.backup", kind="backup.snapshot.create")
        existing = {"phase": "kopia_transfer", "kopia_phase": "processing", "progress_sequence": 10, "processed_bytes": 40}
        for sequence in (9, 10):
            self.assertFalse(_should_apply_progress_update(
                task=task, incoming={**existing, "progress_sequence": sequence}, existing=existing,
            ))
        self.assertTrue(_should_apply_progress_update(
            task=task, incoming={**existing, "progress_sequence": 11}, existing=existing,
        ))

    def test_completed_lane_reads_persisted_final_counters(self):
        from unittest.mock import patch
        from apps.protection.services.progress.backup_runtime import _lane_from_directory
        directory = SimpleNamespace(
            id=1, status="available", size_bytes=40, display_name="", source_path="/data",
            last_progress_snapshot={}, last_progress_sample={},
            stats={"backup_run_counters": {
                "progress_schema_version": 2, "processed_entry_count": 8,
                "hashed_count": 3, "cached_count": 5,
                "hashed_bytes": 12, "cached_bytes": 28, "uploaded_bytes": 4,
            }},
        )
        with patch("apps.protection.services.progress.backup_runtime._reference_bytes_for_directory", return_value=0):
            lane = _lane_from_directory(directory)
        self.assertEqual(lane["progress"]["processed_entry_count"], 8)
        self.assertEqual(lane["progress"]["hashed_bytes"], 12)
        self.assertEqual(lane["progress"]["cached_bytes"], 28)

    def test_runtime_poll_reads_without_saving_task_or_projection(self):
        from unittest.mock import Mock, patch

        from apps.protection.api.views.backup_task_runtime import BackupTaskRuntimeView

        task = SimpleNamespace(
            status="running", started_at=None, finished_at=None, progress=25,
            save=Mock(),
        )
        payload = {"transfer_progress": {"processed_entry_count": 8}}
        module = "apps.protection.api.views.backup_task_runtime"
        with (
            patch(f"{module}.require_org", return_value=SimpleNamespace(id=1)),
            patch(f"{module}.Task.objects") as manager,
            patch(f"{module}.build_backup_kopia_progress", return_value=payload) as build,
        ):
            manager.filter.return_value.first.return_value = task
            response = BackupTaskRuntimeView().get(SimpleNamespace(), "task-one")
        build.assert_called_once_with(task=task)
        task.save.assert_not_called()
        self.assertEqual(response.data["status"], "running")
        self.assertEqual(response.data["progress"], 25)
        self.assertEqual(response.data["transfer_progress"], payload["transfer_progress"])

    def test_alive_skips_projection_except_when_backup_is_reattached(self):
        from unittest.mock import patch

        from apps.node.ws.uplink import _handle_task_progress

        message = SimpleNamespace(task_id="task-one", progress={}, is_alive=True)
        for status in ("running", "timeout"):
            task = SimpleNamespace(
                kind="backup.snapshot.create", status=status,
                _progress_update_applied=False,
            )
            with (
                patch("apps.node.ws.uplink.record_task_progress", return_value=task),
                patch("apps.protection.services.backup_orchestrator.reattach_backup_node_task", return_value=task),
                patch("apps.protection.services.backup_orchestrator.maybe_trigger_backup_advance") as advance,
                patch("apps.restore.services.restore_progress.maybe_trigger_restore_progress"),
            ):
                _handle_task_progress(node_id=1, message=message)
            if status == "timeout":
                advance.assert_called_once_with(node_task=task)
            else:
                advance.assert_not_called()
