from types import SimpleNamespace
from unittest.mock import patch

from common.errors import AppError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.lens_bridge.services import snapshot_scope_tasks
from apps.node.models import NodeTask


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

    def test_maps_only_known_special_content_failure_to_product_message(self):
        task = SimpleNamespace(
            result={"error_code": "INSIGHT_UNSUPPORTED_CONTENT_TYPE"}
        )

        message = snapshot_scope_tasks.snapshot_task_error(
            task,
            default="generic failure",
        )

        self.assertEqual(
            message,
            snapshot_scope_tasks.UNSUPPORTED_CONTENT_MESSAGE,
        )

    def test_does_not_expose_unknown_agent_failure(self):
        task = SimpleNamespace(result={"error_code": "INTERNAL_PATH_LEAK"})

        message = snapshot_scope_tasks.snapshot_task_error(
            task,
            default="generic failure",
        )

        self.assertEqual(message, "generic failure")

    def test_terminal_failure_contract_maps_timeout_and_known_agent_codes(self):
        timeout = SimpleNamespace(status=NodeTask.Status.TIMEOUT, result={}, payload={})
        missing_path = SimpleNamespace(
            status=NodeTask.Status.FAILED,
            result={"error_code": "INSIGHT_SNAPSHOT_PATH_NOT_FOUND"},
            payload={},
        )

        timeout_failure = snapshot_scope_tasks.snapshot_task_failure(
            timeout,
            default="generic failure",
        )
        path_failure = snapshot_scope_tasks.snapshot_task_failure(
            missing_path,
            default="generic failure",
        )

        self.assertEqual(timeout_failure.code, "INSIGHT.SNAPSHOT_BROWSE_TIMEOUT")
        self.assertTrue(timeout_failure.retryable)
        self.assertEqual(path_failure.code, "INSIGHT.SNAPSHOT_PATH_NOT_FOUND")
        self.assertFalse(path_failure.retryable)

    def test_rejects_preloaded_snapshot_directory_from_another_org(self):
        directory = SimpleNamespace(
            id=31,
            organization_id=6,
            source_snapshot_id=71,
        )

        with self.assertRaises(ValidationError) as raised:
            snapshot_scope_tasks.dispatch_snapshot_operation(
                organization_id=5,
                directory_id=31,
                backup_source_snapshot_id=71,
                gateway_link_id=17,
                requesting_user_id=9,
                path="reports",
                kind="lens.snapshot.browse",
                correlation_type=snapshot_scope_tasks.BROWSE_CORRELATION_TYPE,
                correlation_id="user:9:browse",
                directory=directory,
            )

        self.assertIn("directory_id", raised.exception.detail)

    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "get_node_task_by_correlation_for_requesting_org"
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
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "get_node_task_by_correlation_for_requesting_org"
    )
    def test_does_not_recover_another_task_kind(self, get_task):
        get_task.return_value = SimpleNamespace(kind="snapshot.browse")

        task = snapshot_scope_tasks.scope_task_for_correlation(
            organization=SimpleNamespace(id=5),
            correlation_id="chat:9:scope:0:dispatch-token",
        )

        self.assertIsNone(task)

    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "get_node_task_for_requesting_org"
    )
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
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._gateway_reader_context")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload",
        side_effect=DjangoValidationError(
            {"repository_id": "Repository is not available for read operations."}
        ),
    )
    def test_repository_domain_error_is_exposed_as_snapshot_validation(
        self,
        _lock_repositories,
        gateway_context,
        directory_for_org,
    ):
        directory_for_org.return_value = SimpleNamespace(
            id=31,
            organization_id=5,
            repository_id=7,
            source_snapshot_id=71,
        )
        gateway_context.return_value = SimpleNamespace()

        with self.assertRaises(AppError) as raised:
            snapshot_scope_tasks.dispatch_snapshot_browse(
                organization_id=5,
                directory_id=31,
                backup_source_snapshot_id=71,
                gateway_link_id=17,
                requesting_user_id=9,
                path="reports",
                limit=100,
                correlation_id="user:9:browse",
            )

        self.assertEqual(raised.exception.code, "INSIGHT.REPOSITORY_UNAVAILABLE")

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.run_agent_task_async")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "resolve_snapshot_repository_reader"
    )
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._directory_for_org")
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._gateway_reader_context")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload"
    )
    def test_direct_repository_browse_runs_on_selected_platform_gateway(
        self,
        lock_repositories,
        gateway_context,
        directory_for_org,
        resolve_reader,
        run_async,
    ):
        directory_for_org.return_value = SimpleNamespace(
            id=31,
            organization_id=5,
            repository_id=7,
            source_snapshot_id=71,
            kopia_snapshot_id="kopia-1",
            source_snapshot=SimpleNamespace(source_type="host", source_ref_id=9),
        )
        gateway = SimpleNamespace(
            id=17,
            organization_id=20,
            metadata={"inventory": {"capabilities": ["snapshot_browse_v1"]}},
        )
        gateway_context.return_value = SimpleNamespace(
            gateway=gateway,
            execution_organization=SimpleNamespace(id=20),
        )
        lock_repositories.return_value = [SimpleNamespace(id=7)]
        resolve_reader.return_value = SimpleNamespace(
            node=gateway,
            repository_payload={"type": "s3"},
            mode="fallback_node",
        )
        expected_task = SimpleNamespace(id="task-1")
        run_async.return_value = SimpleNamespace(task=expected_task)

        task = snapshot_scope_tasks.dispatch_snapshot_browse(
            organization_id=5,
            directory_id=31,
            backup_source_snapshot_id=71,
            gateway_link_id=17,
            requesting_user_id=9,
            path="reports",
            limit=100,
            correlation_id="user:9:browse",
        )

        self.assertIs(task, expected_task)
        kwargs = run_async.call_args.kwargs
        self.assertEqual(kwargs["organization_id"], 20)
        self.assertEqual(kwargs["node_id"], 17)
        self.assertEqual(kwargs["requesting_organization_id"], 5)
        self.assertEqual(kwargs["persisted_payload"]["reader_mode"], "fallback_node")
        self.assertNotIn("repository", kwargs["persisted_payload"])

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.run_agent_task_async")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "resolve_snapshot_repository_reader"
    )
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._directory_for_org")
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._gateway_reader_context")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload"
    )
    def test_rejects_untrusted_repository_reader_identity(
        self,
        lock_repositories,
        gateway_context,
        directory_for_org,
        resolve_reader,
        run_async,
    ):
        directory_for_org.return_value = SimpleNamespace(
            id=31,
            organization_id=5,
            repository_id=7,
            source_snapshot_id=71,
            kopia_snapshot_id="kopia-1",
            source_snapshot=SimpleNamespace(source_type="host", source_ref_id=9),
        )
        gateway_context.return_value = SimpleNamespace(
            gateway=SimpleNamespace(id=17),
            execution_organization=SimpleNamespace(id=20),
        )
        lock_repositories.return_value = [SimpleNamespace(id=7)]

        invalid_accesses = (
            SimpleNamespace(
                node=SimpleNamespace(id=18, organization_id=20),
                repository_payload={"type": "s3"},
                mode="fallback_node",
            ),
            SimpleNamespace(
                node=SimpleNamespace(id=17, organization_id=20),
                repository_payload={"type": "s3"},
                mode="unexpected",
            ),
        )
        for access in invalid_accesses:
            with self.subTest(mode=access.mode, node_id=access.node.id):
                resolve_reader.return_value = access
                with self.assertRaises(ValidationError) as raised:
                    snapshot_scope_tasks.dispatch_snapshot_browse(
                        organization_id=5,
                        directory_id=31,
                        backup_source_snapshot_id=71,
                        gateway_link_id=17,
                        requesting_user_id=9,
                        path="reports",
                        limit=100,
                        correlation_id="user:9:browse",
                    )
                self.assertIn("gateway_link_id", raised.exception.detail)
        run_async.assert_not_called()

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.run_agent_task_async")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "resolve_snapshot_repository_reader"
    )
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._directory_for_org")
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._gateway_reader_context")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload"
    )
    def test_rejects_repository_reader_without_browse_capability(
        self,
        lock_repositories,
        gateway_context,
        directory_for_org,
        resolve_reader,
        run_async,
    ):
        directory_for_org.return_value = SimpleNamespace(
            id=31,
            organization_id=5,
            repository_id=7,
            source_snapshot_id=71,
            kopia_snapshot_id="kopia-1",
            source_snapshot=SimpleNamespace(source_type="host", source_ref_id=9),
        )
        gateway = SimpleNamespace(id=17, organization_id=5, metadata={})
        gateway_context.return_value = SimpleNamespace(
            gateway=gateway,
            execution_organization=SimpleNamespace(id=5),
        )
        lock_repositories.return_value = [SimpleNamespace(id=7)]
        resolve_reader.return_value = SimpleNamespace(
            node=gateway,
            repository_payload={"type": "s3"},
            mode="fallback_node",
        )

        with self.assertRaises(AppError) as raised:
            snapshot_scope_tasks.dispatch_snapshot_browse(
                organization_id=5,
                directory_id=31,
                backup_source_snapshot_id=71,
                gateway_link_id=17,
                requesting_user_id=9,
                path="reports",
                limit=100,
                correlation_id="user:9:browse",
            )

        self.assertEqual(
            raised.exception.code,
            "INSIGHT.REPOSITORY_READER_UPGRADE_REQUIRED",
        )
        run_async.assert_not_called()

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.run_agent_task_async")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "resolve_snapshot_repository_reader"
    )
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._directory_for_org")
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._gateway_reader_context")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload"
    )
    def test_scope_resolution_uses_async_agent_dispatch(
        self,
        lock_repositories,
        gateway_context,
        directory_for_org,
        resolve_reader,
        run_async,
    ):
        directory_for_org.return_value = SimpleNamespace(
            id=31,
            organization_id=5,
            repository_id=7,
            source_snapshot_id=71,
            kopia_snapshot_id="kopia-1",
            source_snapshot=SimpleNamespace(source_type="host", source_ref_id=9),
        )
        gateway_context.return_value = SimpleNamespace(
            gateway=SimpleNamespace(id=17),
            execution_organization=SimpleNamespace(id=5),
        )
        lock_repositories.return_value = [SimpleNamespace(id=7)]
        resolve_reader.return_value = SimpleNamespace(
            node=SimpleNamespace(
                id=12,
                organization_id=5,
                metadata={
                    "inventory": {"capabilities": ["snapshot_scope_resolve_v1"]}
                },
            ),
            repository_payload={"type": "s3"},
            mode="bound_proxy",
        )
        expected_task = SimpleNamespace(id="task-1")
        run_async.return_value = SimpleNamespace(task=expected_task)

        task = snapshot_scope_tasks.dispatch_scope_resolution(
            organization_id=5,
            directory_id=31,
            backup_source_snapshot_id=71,
            gateway_link_id=17,
            requesting_user_id=9,
            path="reports",
            correlation_id="chat:9:scope:0",
        )

        self.assertIs(task, expected_task)
        run_async.assert_called_once()
        kwargs = run_async.call_args.kwargs
        self.assertEqual(kwargs["kind"], "lens.snapshot.scope.resolve")
        self.assertEqual(kwargs["payload"]["path"], "reports")
        self.assertNotIn("repository", kwargs["persisted_payload"])
        self.assertEqual(kwargs["organization_id"], 5)
        self.assertEqual(kwargs["requesting_organization_id"], 5)
        self.assertEqual(
            resolve_reader.call_args.kwargs["fallback_node"].id,
            17,
        )

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.run_agent_task_async")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks."
        "resolve_snapshot_repository_reader"
    )
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._directory_for_org")
    @patch("apps.lens_bridge.services.snapshot_scope_tasks._gateway_reader_context")
    @patch(
        "apps.lens_bridge.services.snapshot_scope_tasks.lock_repositories_for_workload"
    )
    def test_root_file_scope_dispatches_trusted_snapshot_metadata(
        self,
        lock_repositories,
        gateway_context,
        directory_for_org,
        resolve_reader,
        run_async,
    ):
        directory = SimpleNamespace(
            id=31,
            organization_id=5,
            repository_id=7,
            source_snapshot_id=71,
            kopia_snapshot_id="kopia-file-1",
            path_type="file",
            size_bytes=42,
            source_snapshot=SimpleNamespace(source_type="host", source_ref_id=9),
        )
        directory_for_org.return_value = directory
        gateway_context.return_value = SimpleNamespace(
            gateway=SimpleNamespace(id=17),
            execution_organization=SimpleNamespace(id=5),
        )
        lock_repositories.return_value = [SimpleNamespace(id=7)]
        resolve_reader.return_value = SimpleNamespace(
            node=SimpleNamespace(
                id=12,
                organization_id=5,
                metadata={
                    "inventory": {"capabilities": ["snapshot_scope_resolve_v1"]}
                },
            ),
            repository_payload={"type": "s3"},
            mode="bound_proxy",
        )
        run_async.return_value = SimpleNamespace(task=SimpleNamespace(id="task-file"))

        snapshot_scope_tasks.dispatch_scope_resolution(
            organization_id=5,
            directory_id=31,
            backup_source_snapshot_id=71,
            gateway_link_id=17,
            requesting_user_id=9,
            path="",
            correlation_id="chat:9:scope:0",
        )

        kwargs = run_async.call_args.kwargs
        self.assertEqual(kwargs["payload"]["root_path_type"], "file")
        self.assertEqual(kwargs["persisted_payload"]["root_size_bytes"], 42)
