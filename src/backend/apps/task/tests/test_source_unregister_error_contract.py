"""Source deregistration errors identify each source once and explain protection."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.task.error_contract import task_error_contract


class SourceUnregisterErrorContractTests(SimpleTestCase):
    def _contract(self, *, reasons=None, resources=None, task_type="source_unregister"):
        task = SimpleNamespace(
            task_type=task_type,
            status="failed",
            result_payload={
                "reasons": reasons or [],
                "hint": "Try Force Cleanup.",
            },
            error_code="SOURCE_UNREGISTER_FAILED",
            error_message="Backup source was not deleted.",
            current_step="cleanup_direct_nas_repositories",
            task_uuid="unregister-task",
        )
        return task_error_contract(task, resources or [])

    def _resource(self, subtype, resource_id=406):
        return SimpleNamespace(
            resource_type="backup_source",
            resource_subtype=subtype,
            resource_id=resource_id,
        )

    def test_historical_numeric_and_typed_source_are_merged(self):
        contract = self._contract(
            resources=[self._resource("agent")],
            reasons=[{
                "code": "snapshot_in_use",
                "source_id": "agent:406",
                "source_name": "jlb-154",
                "detail": "Snapshot is protected.",
            }],
        )
        self.assertEqual(contract["entities"], [{
            "id": "agent:406", "type": "source", "name": "jlb-154",
            "error": "Snapshot is protected.",
        }])
        self.assertEqual(contract["suggestions"][0]["code"], "resolve_snapshot_usage")
        self.assertIn("cannot bypass", contract["suggestions"][0]["detail"])
        self.assertNotIn("Try Force Cleanup", str(contract["suggestions"]))

    def test_same_numeric_id_with_different_source_types_is_not_merged(self):
        contract = self._contract(
            resources=[self._resource("agent"), self._resource("nas")],
        )
        self.assertEqual(
            [entity["id"] for entity in contract["entities"]],
            ["agent:406", "nas:406"],
        )

    def test_merge_preserves_multiple_snapshot_errors_and_safe_metadata(self):
        contract = self._contract(
            resources=[self._resource("agent")],
            reasons=[
                {
                    "code": "snapshot_in_use",
                    "source_id": "agent:406",
                    "source_name": "jlb-154",
                    "detail": f"Snapshot #{snapshot_id} is protected.",
                    "snapshot_id": snapshot_id,
                    "consumers": [{"type": "chat", "status": "failed"}],
                }
                for snapshot_id in (790, 791)
            ],
        )
        self.assertEqual(len(contract["entities"]), 1)
        self.assertIn("#790", contract["entities"][0]["error"])
        self.assertIn("#791", contract["entities"][0]["error"])
        self.assertEqual(contract["reasons"][0]["snapshot_id"], 790)
        self.assertEqual(
            contract["reasons"][0]["consumers"],
            [{"type": "chat", "status": "failed"}],
        )

    def test_other_task_types_keep_existing_resource_identity(self):
        contract = self._contract(
            task_type="backup", resources=[self._resource("agent")],
        )
        self.assertEqual(contract["entities"][0]["id"], 406)

    def test_offline_unregister_uses_deregistration_guidance(self):
        contract = self._contract(reasons=[{
            "code": "agent_offline",
            "detail": 'Agent "test-host" is offline — remote uninstall is required in strict mode.',
        }])
        self.assertEqual(contract["reasons"][0]["code"], "agent_offline")
        self.assertEqual(contract["suggestions"][0]["code"], "review_source_unregister")
        self.assertNotIn("credentials", str(contract["suggestions"]))

    def test_historical_generic_suggestion_gets_deregistration_code(self):
        task = SimpleNamespace(
            task_type="source_unregister",
            status="failed",
            result_payload={
                "suggestions": [{"code": "review_task", "detail": "Old generic hint"}],
            },
            error_code="SOURCE_UNREGISTER_PREFLIGHT_FAILED",
            error_message="Backup source was not deleted.",
            current_step="cleanup_source_endpoint",
            task_uuid="historical-task",
        )
        contract = task_error_contract(task)
        self.assertEqual(contract["suggestions"], [{
            "code": "review_source_unregister", "detail": "review_source_unregister",
        }])

    def test_consumer_projection_ignores_private_fields_and_invalid_entries(self):
        contract = self._contract(reasons=[{
            "code": "snapshot_in_use",
            "detail": "Snapshot is protected.",
            "snapshot_id": 790,
            "consumers": [
                None, "invalid",
                {"type": "chat", "status": "failed", "title": "Private title",
                 "consumer_id": "71", "session_url": "/private-chat/71"},
            ],
        }])
        self.assertEqual(contract["reasons"][0]["consumers"], [
            {"type": "chat", "status": "failed"},
        ])
        self.assertNotIn("Private title", str(contract["reasons"]))
        self.assertNotIn("consumer_id", str(contract["reasons"]))
