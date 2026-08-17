from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.lens_bridge.services import snapshot_scope_tasks


class SnapshotScopeTaskNormalizationTests(TestCase):
    def test_rejects_parent_path_traversal(self):
        with self.assertRaises(ValidationError):
            snapshot_scope_tasks._clean_relative_path("reports/../secrets")

    def test_normalizes_browse_entries_for_the_insight_client(self):
        task = SimpleNamespace(
            result={
                "entries": [
                    {
                        "name": "reports",
                        "path": r"finance\reports",
                        "type": "directory",
                        "size": 0,
                    },
                    {
                        "name": "summary.pdf",
                        "path": "finance/summary.pdf",
                        "type": "file",
                        "size_bytes": 42,
                        "downloadable": False,
                    },
                ]
            }
        )

        rows = snapshot_scope_tasks.normalized_browse_entries(task)

        self.assertEqual(rows[0]["type"], "dir")
        self.assertEqual(rows[0]["path"], "finance/reports")
        self.assertTrue(rows[0]["downloadable"])
        self.assertEqual(rows[1]["size_bytes"], 42)
        self.assertFalse(rows[1]["downloadable"])

    def test_scope_summary_rejects_negative_agent_values(self):
        task = SimpleNamespace(
            result={"path_type": "file", "file_count": 1, "size_bytes": -1}
        )

        with self.assertRaisesRegex(RuntimeError, "invalid Insight scope summary"):
            snapshot_scope_tasks.resolved_scope_summary(task)

    def test_scope_summary_rejects_invalid_file_count(self):
        task = SimpleNamespace(
            result={"path_type": "file", "file_count": 0, "size_bytes": 0}
        )

        with self.assertRaisesRegex(RuntimeError, "invalid Insight scope summary"):
            snapshot_scope_tasks.resolved_scope_summary(task)

    def test_scope_summary_rejects_fractional_values(self):
        task = SimpleNamespace(
            result={"path_type": "file", "file_count": 1.5, "size_bytes": 0}
        )

        with self.assertRaisesRegex(RuntimeError, "invalid Insight scope summary"):
            snapshot_scope_tasks.resolved_scope_summary(task)

    def test_scope_summary_rejects_values_outside_database_range(self):
        task = SimpleNamespace(
            result={"path_type": "file", "file_count": 1, "size_bytes": 2**63}
        )

        with self.assertRaisesRegex(RuntimeError, "invalid Insight scope summary"):
            snapshot_scope_tasks.resolved_scope_summary(task)

    def test_browse_normalization_discards_unsafe_paths_and_tolerates_bad_size(self):
        task = SimpleNamespace(
            result={
                "entries": [
                    {"name": "secret", "path": "../secret", "type": "file"},
                    {
                        "name": "report.pdf",
                        "path": "reports/report.pdf",
                        "type": "file",
                        "size_bytes": "not-a-number",
                    },
                ]
            }
        )

        rows = snapshot_scope_tasks.normalized_browse_entries(task)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], "reports/report.pdf")
        self.assertEqual(rows[0]["size_bytes"], 0)

    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.get_node_task_by_correlation"
    )
    def test_recovers_scope_task_by_correlation(self, get_task):
        expected_task = SimpleNamespace(kind="lens.snapshot.scope.resolve")
        organization = SimpleNamespace(id=5)
        get_task.return_value = expected_task

        task = snapshot_scope_tasks.scope_task_for_correlation(
            organization=organization,
            correlation_id="chat:9:scope:0:dispatch-token",
        )

        self.assertIs(task, expected_task)
        get_task.assert_called_once_with(
            org=organization,
            correlation_type=snapshot_scope_tasks.SCOPE_CORRELATION_TYPE,
            correlation_id="chat:9:scope:0:dispatch-token",
        )

    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.get_node_task_by_correlation"
    )
    def test_does_not_recover_another_task_kind(self, get_task):
        get_task.return_value = SimpleNamespace(kind="snapshot.browse")

        task = snapshot_scope_tasks.scope_task_for_correlation(
            organization=SimpleNamespace(id=5),
            correlation_id="chat:9:scope:0:dispatch-token",
        )

        self.assertIsNone(task)

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.get_node_task_for_org")
    def test_scope_reference_requires_matching_kind_type_and_correlation(
        self,
        get_task,
    ):
        organization = SimpleNamespace(id=5)
        get_task.return_value = SimpleNamespace(
            kind="lens.snapshot.scope.resolve",
            correlation_type=snapshot_scope_tasks.SCOPE_CORRELATION_TYPE,
            correlation_id="chat:9:scope:0:other-token",
        )

        task = snapshot_scope_tasks.scope_task_for_reference(
            organization=organization,
            task_id="task-1",
            correlation_id="chat:9:scope:0:dispatch-token",
        )

        self.assertIsNone(task)
        get_task.assert_called_once_with(org=organization, task_id="task-1")

    @patch("apps.lens_bridge.services.snapshot_scope_tasks._directory_for_org")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload",
        side_effect=DjangoValidationError(
            {"repository_id": "Repository is not available for read operations."}
        ),
    )
    def test_repository_domain_error_is_exposed_as_snapshot_validation(
        self,
        _lock_repositories,
        directory_for_org,
    ):
        directory_for_org.return_value = SimpleNamespace(repository_id=7)

        with self.assertRaises(ValidationError) as raised:
            snapshot_scope_tasks.dispatch_snapshot_browse(
                organization_id=5,
                directory_id=31,
                path="reports",
                limit=100,
                correlation_id="user:9:browse",
            )

        self.assertIn("directory_id", raised.exception.detail)

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.run_agent_task_async")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "resolve_snapshot_repository_reader"
    )
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._directory_for_org")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload"
    )
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.repository_uses_bound_proxy",
        return_value=True,
    )
    def test_scope_resolution_uses_async_agent_dispatch(
        self,
        _uses_proxy,
        lock_repositories,
        directory_for_org,
        resolve_reader,
        run_async,
    ):
        directory_for_org.return_value = SimpleNamespace(
            id=31,
            repository_id=7,
            kopia_snapshot_id="kopia-1",
            source_snapshot=SimpleNamespace(source_type="host", source_ref_id=9),
        )
        lock_repositories.return_value = [SimpleNamespace(id=7)]
        resolve_reader.return_value = SimpleNamespace(
            node=SimpleNamespace(id=12),
            repository_payload={"type": "s3"},
        )
        expected_task = SimpleNamespace(id="task-1")
        run_async.return_value = SimpleNamespace(task=expected_task)

        task = snapshot_scope_tasks.dispatch_scope_resolution(
            organization_id=5,
            directory_id=31,
            path="reports",
            correlation_id="chat:9:scope:0",
        )

        self.assertIs(task, expected_task)
        run_async.assert_called_once()
        kwargs = run_async.call_args.kwargs
        self.assertEqual(kwargs["kind"], "lens.snapshot.scope.resolve")
        self.assertEqual(kwargs["payload"]["path"], "reports")
        self.assertNotIn("repository", kwargs["persisted_payload"])

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.run_agent_task_async")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "resolve_snapshot_repository_reader"
    )
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._directory_for_org")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload"
    )
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.repository_uses_bound_proxy",
        return_value=True,
    )
    def test_root_file_scope_dispatches_trusted_snapshot_metadata(
        self,
        _uses_proxy,
        lock_repositories,
        directory_for_org,
        resolve_reader,
        run_async,
    ):
        directory = SimpleNamespace(
            id=31,
            repository_id=7,
            kopia_snapshot_id="kopia-file-1",
            path_type="file",
            size_bytes=42,
            source_snapshot=SimpleNamespace(source_type="host", source_ref_id=9),
        )
        directory_for_org.return_value = directory
        lock_repositories.return_value = [SimpleNamespace(id=7)]
        resolve_reader.return_value = SimpleNamespace(
            node=SimpleNamespace(id=12),
            repository_payload={"type": "s3"},
        )
        run_async.return_value = SimpleNamespace(task=SimpleNamespace(id="task-file"))

        snapshot_scope_tasks.dispatch_scope_resolution(
            organization_id=5,
            directory_id=31,
            path="",
            correlation_id="chat:9:scope:0",
        )

        kwargs = run_async.call_args.kwargs
        self.assertEqual(kwargs["payload"]["root_path_type"], "file")
        self.assertEqual(kwargs["persisted_payload"]["root_size_bytes"], 42)
