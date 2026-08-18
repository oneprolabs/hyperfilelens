from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.lens_bridge.services import managed_datasource, sl_client


class ManagedDatasourceTests(SimpleTestCase):
    datasource_uuid = uuid.UUID("39ff860b-866a-4c9c-a2c8-d142156c3a76")

    def _knowledge_source(self):
        knowledge_source = MagicMock()
        knowledge_source.id = 42
        knowledge_source.sl_lensnode_uuid = uuid.UUID(
            "ab38d1f1-2295-4da2-806f-d64f58c6e323"
        )
        knowledge_source.sl_datasource_uuid = None
        knowledge_source.workspace_path_on_lensnode = "/workspace/org-1/data/hfl-ks-42"
        knowledge_source.workspace_binding.workspace_uid = uuid.UUID(
            "5f23a26a-ea7d-4aaf-9a64-6e395fd015af"
        )
        return knowledge_source

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.create_managed_datasource"
    )
    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.list_managed_datasources",
        return_value=[],
    )
    def test_ensure_persists_remote_datasource_uuid(
        self,
        _list_datasources,
        create_datasource,
    ):
        knowledge_source = self._knowledge_source()
        create_datasource.return_value = {
            "uuid": str(self.datasource_uuid),
        }
        sync_state = {}

        result = managed_datasource.ensure_managed_datasource(
            ks=knowledge_source,
            sync_state=sync_state,
        )

        self.assertEqual(result, self.datasource_uuid)
        self.assertEqual(
            knowledge_source.sl_datasource_uuid,
            self.datasource_uuid,
        )
        self.assertEqual(
            sync_state["managed_datasource"]["operation_status"],
            "confirmed",
        )

    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.start_managed_datasource_conversion"
    )
    def test_conversion_persists_partial_failure_as_warning(
        self,
        start_conversion,
        get_task,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        start_conversion.return_value = {
            "task_id": "convert-1",
            "task_execution_id": 9,
            "status": "PENDING",
        }
        get_task.return_value = {
            "task_id": "convert-1",
            "status": "SUCCESS",
            "finished_at": "2026-08-03T04:00:00Z",
            "result": {
                "conversion_summary": {
                    "candidates": 2,
                    "total": 2,
                    "success": 1,
                    "failed": 1,
                    "skipped": 0,
                    "unsupported": 0,
                    "items": [],
                }
            },
            "metadata": {},
        }
        sync_state = {}

        with self.assertRaises(
            managed_datasource.ManagedDatasourcePending
        ):
            managed_datasource.convert_documents(
                ks=knowledge_source,
                sync_state=sync_state,
                conversion={"document": True},
            )

        summary = managed_datasource.convert_documents(
            ks=knowledge_source,
            sync_state=sync_state,
            conversion={"document": True},
        )

        self.assertEqual(summary["success"], 1)
        self.assertIn(
            "CONVERSION_PARTIAL_FAILED",
            sync_state["conversion"]["warnings"],
        )
        self.assertIn(
            "VISUAL_MODEL_NOT_CONFIGURED",
            sync_state["conversion"]["warnings"],
        )

    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    def test_dispatched_revocation_waits_for_lensnode_callback(self, get_task):
        knowledge_source = self._knowledge_source()
        knowledge_source.sync_state_json = {
            "conversion": {"task_id": "convert-1"}
        }
        get_task.return_value = {
            "task_id": "convert-1",
            "status": "REVOKED",
            "updated_at": "2026-08-03T04:00:00Z",
            "metadata": {
                "datasource_conversion_request_id": "request-1",
                "manual_revoked_at": "2026-08-03T04:00:00Z",
            },
        }

        self.assertFalse(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

        get_task.return_value["metadata"]["conversion_summary"] = {}
        self.assertFalse(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

        get_task.return_value["metadata"][
            "lensnode_final_callback_at"
        ] = "2026-08-03T04:00:01Z"
        self.assertTrue(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    def test_partial_summary_before_timeout_does_not_confirm_stop(self, get_task):
        knowledge_source = self._knowledge_source()
        knowledge_source.sync_state_json = {
            "conversion": {"task_id": "convert-1"}
        }
        get_task.return_value = {
            "task_id": "convert-1",
            "status": "FAILURE",
            "updated_at": "2026-08-03T04:00:00Z",
            "metadata": {
                "datasource_conversion_request_id": "request-1",
                "timeout_cancelled_at": "2026-08-03T04:00:00Z",
                "conversion_summary": {"success": 1},
            },
        }

        self.assertFalse(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

        get_task.return_value["metadata"][
            "conversion_stop_acknowledged_at"
        ] = "2026-08-03T04:00:02Z"
        self.assertTrue(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    def test_revocation_before_dispatch_is_already_stopped(self, get_task):
        knowledge_source = self._knowledge_source()
        knowledge_source.sync_state_json = {
            "conversion": {"task_id": "convert-1"}
        }
        get_task.return_value = {
            "task_id": "convert-1",
            "status": "REVOKED",
            "metadata": {"manual_revoked_at": "2026-08-03T04:00:00Z"},
        }

        self.assertTrue(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    def test_audited_manual_confirmation_unblocks_terminal_conversion(self, get_task):
        knowledge_source = self._knowledge_source()
        knowledge_source.sync_state_json = {
            "conversion": {
                "task_id": "convert-1",
                "manual_stop_confirmation": {
                    "confirmed": True,
                    "task_id": "convert-1",
                },
            }
        }
        get_task.return_value = {
            "task_id": "convert-1",
            "status": "REVOKED",
            "metadata": {
                "datasource_conversion_request_id": "request-1",
                "manual_revoked_at": "2026-08-18T01:00:00Z",
            },
        }

        self.assertTrue(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    def test_missing_dispatched_task_does_not_prove_conversion_stopped(
        self,
        get_task,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sync_state_json = {
            "conversion": {"task_id": "convert-1"}
        }
        get_task.return_value = None

        self.assertFalse(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.start_managed_datasource_conversion"
    )
    @patch(
        "apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id",
        return_value=None,
    )
    def test_missing_known_task_is_not_reposted(
        self,
        _get_task,
        start_conversion,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        policy = {"document": True}
        sync_state = {
            "conversion": {
                "task_id": "convert-1",
                "status": "STARTED",
                "policy_fingerprint": (
                    managed_datasource.conversion_policy_fingerprint(policy)
                ),
                "started_at": timezone.now().isoformat(),
            }
        }

        with self.assertRaises(managed_datasource.ManagedDatasourcePending):
            managed_datasource.convert_documents(
                ks=knowledge_source,
                sync_state=sync_state,
                conversion=policy,
            )

        start_conversion.assert_not_called()
        self.assertIn("lookup_missing_at", sync_state["conversion"])

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.start_managed_datasource_conversion"
    )
    @patch(
        "apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id",
        side_effect=sl_client.LensBridgeUnavailable(),
    )
    def test_temporary_poll_failure_is_pending_with_backoff(
        self,
        _get_task,
        start_conversion,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        policy = {"document": True}
        sync_state = {
            "conversion": {
                "task_id": "convert-1",
                "status": "STARTED",
                "policy_fingerprint": (
                    managed_datasource.conversion_policy_fingerprint(policy)
                ),
                "started_at": timezone.now().isoformat(),
            }
        }

        with self.assertRaises(
            managed_datasource.ManagedDatasourcePending
        ) as raised:
            managed_datasource.convert_documents(
                ks=knowledge_source,
                sync_state=sync_state,
                conversion=policy,
            )

        self.assertGreaterEqual(raised.exception.retry_after_seconds, 15)
        self.assertEqual(
            sync_state["conversion"]["last_transient_operation"],
            "poll_conversion",
        )
        start_conversion.assert_not_called()

    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    def test_failed_dispatched_conversion_waits_for_lensnode_callback(
        self,
        get_task,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sync_state_json = {
            "conversion": {"task_id": "convert-1"}
        }
        get_task.return_value = {
            "task_id": "convert-1",
            "status": "FAILURE",
            "metadata": {
                "datasource_conversion_request_id": "request-1",
            },
        }

        self.assertFalse(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

        get_task.return_value["metadata"]["conversion_summary"] = {}
        self.assertTrue(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.start_managed_datasource_conversion"
    )
    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.list_managed_datasource_conversion_tasks"
    )
    def test_lost_start_response_is_recovered_without_duplicate_post(
        self,
        list_tasks,
        start_conversion,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        policy = {"document": True}
        start_conversion.side_effect = sl_client.LensBridgeUnavailable()
        sync_state = {}

        with self.assertRaises(managed_datasource.ManagedDatasourcePending):
            managed_datasource.convert_documents(
                ks=knowledge_source,
                sync_state=sync_state,
                conversion=policy,
            )

        requested_at = sync_state["conversion"]["start_requested_at"]
        list_tasks.return_value = [
            {
                "id": 17,
                "task_id": "recovered-task",
                "status": "STARTED",
                "created_at": requested_at,
                "metadata": {
                    "conversion": policy,
                    "hfl_operation_id": sync_state["conversion"][
                        "operation_id"
                    ],
                },
            }
        ]

        with self.assertRaises(managed_datasource.ManagedDatasourcePending):
            managed_datasource.convert_documents(
                ks=knowledge_source,
                sync_state=sync_state,
                conversion=policy,
            )

        self.assertEqual(start_conversion.call_count, 1)
        self.assertEqual(sync_state["conversion"]["task_id"], "recovered-task")
        self.assertIn("recovered_at", sync_state["conversion"])

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.list_managed_datasource_conversion_tasks"
    )
    def test_operation_recovery_never_adopts_a_policy_only_match(
        self,
        list_tasks,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        policy = {"document": True}
        sync_state = {
            "conversion": {
                "operation_id": "operation-new",
                "status": "STARTING",
                "policy_fingerprint": (
                    managed_datasource.conversion_policy_fingerprint(policy)
                ),
                "start_requested_at": "2026-08-03T04:00:00Z",
            }
        }
        list_tasks.return_value = [
            {
                "task_id": "previous-policy-match",
                "status": "SUCCESS",
                "created_at": "2026-08-03T04:00:01Z",
                "metadata": {"conversion": policy},
            }
        ]

        with patch(
            "apps.lens_bridge.services.managed_datasource."
            "sl_client.start_managed_datasource_conversion"
        ) as start_conversion:
            with self.assertRaises(
                managed_datasource.ManagedDatasourcePending
            ):
                managed_datasource.convert_documents(
                    ks=knowledge_source,
                    sync_state=sync_state,
                    conversion=policy,
                )

        start_conversion.assert_not_called()
        self.assertNotIn("task_id", sync_state["conversion"])

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.start_managed_datasource_conversion"
    )
    def test_deterministic_start_rejection_is_not_retried(self, start_conversion):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        rejection = sl_client.LensBridgeError("invalid policy")
        rejection.status_code = 400
        start_conversion.side_effect = rejection
        sync_state = {}

        with self.assertRaisesRegex(
            managed_datasource.ManagedDatasourceError,
            "rejected",
        ):
            managed_datasource.convert_documents(
                ks=knowledge_source,
                sync_state=sync_state,
                conversion={"document": True},
            )

        self.assertEqual(sync_state["conversion"]["status"], "FAILURE")

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.list_managed_datasource_conversion_tasks",
        return_value=[],
    )
    def test_unresolved_start_blocks_teardown(self, _list_tasks):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        knowledge_source.sync_state_json = {
            "conversion": {
                "status": "STARTING",
                "policy_fingerprint": (
                    managed_datasource.conversion_policy_fingerprint(
                        {"document": True}
                    )
                ),
                "start_requested_at": "2026-08-03T04:00:00Z",
            }
        }

        self.assertFalse(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

        knowledge_source.sync_state_json["conversion"][
            "cancel_probe_confirmed_empty_at"
        ] = "2026-08-03T04:01:00Z"
        self.assertFalse(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.start_managed_datasource_conversion"
    )
    def test_policy_change_cannot_overwrite_active_conversion(
        self,
        start_conversion,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        sync_state = {
            "conversion": {
                "task_id": "convert-1",
                "status": "STARTED",
                "policy_fingerprint": (
                    managed_datasource.conversion_policy_fingerprint(
                        {"document": True}
                    )
                ),
            }
        }

        with self.assertRaisesRegex(
            managed_datasource.ManagedDatasourcePending,
            "previous document conversion",
        ):
            managed_datasource.convert_documents(
                ks=knowledge_source,
                sync_state=sync_state,
                conversion={"document": True, "image": True},
            )

        start_conversion.assert_not_called()
        self.assertEqual(
            sync_state["conversion"]["task_id"],
            "convert-1",
        )

    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    def test_failed_conversion_before_dispatch_is_already_stopped(
        self,
        get_task,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sync_state_json = {
            "conversion": {"task_id": "convert-1"}
        }
        get_task.return_value = {
            "task_id": "convert-1",
            "status": "FAILURE",
            "metadata": {},
        }

        self.assertTrue(
            managed_datasource.conversion_stop_confirmed(knowledge_source)
        )

    @patch(
        "apps.lens_bridge.services.managed_datasource."
        "sl_client.start_managed_datasource_conversion"
    )
    @patch("apps.lens_bridge.services.managed_datasource.sl_client.get_task_by_id")
    def test_failed_conversion_is_reported_without_automatic_restart(
        self,
        get_task,
        start_conversion,
    ):
        knowledge_source = self._knowledge_source()
        knowledge_source.sl_datasource_uuid = self.datasource_uuid
        policy = {"document": True}
        sync_state = {
            "conversion": {
                "task_id": "convert-1",
                "status": "STARTED",
                "policy_fingerprint": (
                    managed_datasource.conversion_policy_fingerprint(policy)
                ),
                "started_at": "2026-08-03T04:00:00+00:00",
            }
        }
        get_task.return_value = {
            "task_id": "convert-1",
            "status": "FAILURE",
            "error": "DATASOURCE_CONVERSION_FAILED",
        }

        with self.assertRaisesRegex(
            managed_datasource.ManagedDatasourceError,
            "DATASOURCE_CONVERSION_FAILED",
        ):
            managed_datasource.convert_documents(
                ks=knowledge_source,
                sync_state=sync_state,
                conversion=policy,
            )

        start_conversion.assert_not_called()
        self.assertEqual(sync_state["conversion"]["status"], "FAILURE")

    def test_unchanged_sidecar_counts_as_readable(self):
        self.assertFalse(
            managed_datasource._all_supported_documents_unreadable(
                {
                    "candidates": 1,
                    "unsupported": 0,
                    "success": 0,
                    "items_truncated": 0,
                    "items": [{"reason": "UNCHANGED"}],
                }
            )
        )

    def test_source_lens_total_detects_all_supported_documents_unreadable(self):
        self.assertTrue(
            managed_datasource._all_supported_documents_unreadable(
                {
                    "total": 2,
                    "unsupported": 0,
                    "success": 0,
                    "skipped": 2,
                    "items_truncated": 0,
                    "items": [
                        {"reason": "NO_EXTRACTABLE_TEXT"},
                        {"reason": "FILE_TOO_LARGE"},
                    ],
                }
            )
        )

    def test_unsupported_plain_text_does_not_count_as_unreadable_document(self):
        self.assertFalse(
            managed_datasource._all_supported_documents_unreadable(
                {
                    "total": 1,
                    "unsupported": 1,
                    "success": 0,
                    "items_truncated": 0,
                    "items": [{"reason": "UNSUPPORTED_TYPE"}],
                }
            )
        )

    def test_native_text_keeps_chat_usable_when_document_conversion_fails(self):
        self.assertFalse(
            managed_datasource._all_supported_documents_unreadable(
                {
                    "total": 2,
                    "unsupported": 1,
                    "success": 0,
                    "skipped": 1,
                    "items_truncated": 0,
                    "items": [
                        {"reason": "UNSUPPORTED_TYPE", "name": "notes.txt"},
                        {"reason": "NO_EXTRACTABLE_TEXT", "name": "scan.pdf"},
                    ],
                }
            )
        )


class ManagedDatasourceSourceLensClientTests(SimpleTestCase):
    @patch("apps.lens_bridge.services.sl_client.request_json")
    def test_conversion_start_sends_stable_operation_headers(self, request_json):
        request_json.return_value = {"task_id": "convert-1"}

        sl_client.start_managed_datasource_conversion(
            datasource_uuid="source-1",
            conversion={"document": True},
            operation_id="operation-1",
        )

        request_json.assert_called_once_with(
            "POST",
            "/api/lens/admin/datasources/source-1/convert/",
            json_body={
                "conversion": {"document": True},
                "force": False,
            },
            extra_headers={
                "Idempotency-Key": "operation-1",
                "X-HFL-Operation-ID": "operation-1",
            },
        )

    @patch("apps.lens_bridge.services.sl_client.request_json")
    def test_conversion_task_list_uses_datasource_route(self, request_json):
        request_json.return_value = {
            "results": [{"task_id": "convert-1"}],
        }

        result = sl_client.list_managed_datasource_conversion_tasks("source-1")

        self.assertEqual(result, [{"task_id": "convert-1"}])
        request_json.assert_called_once_with(
            "GET",
            "/api/lens/admin/datasources/source-1/conversion-tasks/",
            params={"page_size": 100},
        )

    @patch("apps.lens_bridge.services.sl_client.request_json")
    def test_task_lookup_uses_agentcore_execution_route(self, request_json):
        request_json.return_value = {
            "task_id": "convert-1",
            "status": "STARTED",
        }

        result = sl_client.get_task_by_id("convert-1")

        self.assertEqual(result["status"], "STARTED")
        request_json.assert_called_once_with(
            "GET",
            "/api/v1/tasks/executions/by-task-id/convert-1/",
            params={"sync": "false"},
        )
