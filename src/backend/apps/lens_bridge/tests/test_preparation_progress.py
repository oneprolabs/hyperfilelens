from types import SimpleNamespace
from unittest.mock import patch

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.lens_bridge.api.serializers import LensSessionLinkSerializer
from apps.lens_bridge.models import LensGatewayLink, LensKnowledgeSource, LensSessionLink, LensWorkspaceBinding
from apps.node.models import Node
from apps.lens_bridge.services.preparation_progress import (
    assistant_preparation_state,
    preparation_progress_for_session,
    restore_progress_view,
)
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.task.models import Task


def progress_payload(**overrides):
    return {
        "orchestration_phase": "transferring",
        "aggregate": {
            "percent": 62,
            "bytes_done": 620,
            "bytes_total": 1000,
            "bytes_total_known": True,
        },
        "transfer_progress": {"eta_seconds": 40},
        "lanes": [{"status": "running", "eta_seconds": 40}],
        **overrides,
    }


class PreparationProgressTests(SimpleTestCase):
    def test_assistant_state_distinguishes_waiting_configuring_and_authorization(self):
        link = self.link()
        link.provision_phase = "converting"
        link.knowledge_source.sync_state_json = {"conversion": {"status": "SUCCESS"}}
        self.assertEqual(assistant_preparation_state(link), "waiting")
        link.provision_phase = "creating_assistant"
        self.assertEqual(assistant_preparation_state(link), "waiting")
        link.provision_claim_token = "live-claim"
        self.assertEqual(assistant_preparation_state(link), "configuring")
        for phase in ("granting_assistant", "creating_session"):
            link.provision_phase = phase
            self.assertEqual(assistant_preparation_state(link), "opening_session")
        link.provision_state_json = {"source_lens_transient": {"count": 1}}
        self.assertEqual(assistant_preparation_state(link), "retrying")
        link.provision_state_json = {}
        link.knowledge_source.sync_state_json["source_lens_transient"] = {"count": 1}
        self.assertEqual(assistant_preparation_state(link), "retrying")

    def test_assistant_waiting_state_requires_completed_conversion_in_same_tenant(self):
        link = self.link()
        link.provision_phase = "converting"
        link.knowledge_source.sync_state_json = {"conversion": {"status": "STARTED"}}
        self.assertIsNone(assistant_preparation_state(link))
        link.knowledge_source.sync_state_json["conversion"]["status"] = "SUCCESS"
        link.knowledge_source.organization_id = 4
        self.assertIsNone(assistant_preparation_state(link))

    def test_safe_metrics_only(self):
        payload = progress_payload()
        payload["target_path"] = "/private/workspace"
        payload["task_id"] = "secret"
        self.assertEqual(restore_progress_view(payload, status="running"), {
            "status": "running", "phase": "transferring",
            "progress_percent": 62, "bytes_done": 620,
            "bytes_total": 1000, "eta_seconds": 40,
        })

    def test_unknown_or_reference_total_hides_percentage_and_eta(self):
        for key in ("bytes_total_known", "bytes_total_reference"):
            payload = progress_payload()
            payload["aggregate"][key] = key == "bytes_total_reference"
            view = restore_progress_view(payload, status="running")
            self.assertIsNone(view["progress_percent"])
            self.assertIsNone(view["bytes_total"])
            self.assertIsNone(view["eta_seconds"])

    def test_unstarted_or_stale_lane_hides_eta(self):
        for row in ({"status": "pending"}, {"status": "running", "eta_seconds": None}):
            payload = progress_payload()
            payload["lanes"].append(row)
            self.assertIsNone(restore_progress_view(payload, status="running")["eta_seconds"])

    def test_queue_finalizing_and_failure_do_not_claim_remaining_time(self):
        for phase, status in (
            ("queued", "pending"), ("estimating", "running"),
            ("finalizing", "running"), ("failed", "failed"),
        ):
            view = restore_progress_view(progress_payload(orchestration_phase=phase), status=status)
            self.assertIsNone(view["eta_seconds"])
            if phase != "finalizing":
                self.assertIsNone(view["progress_percent"])

    def test_100_percent_is_not_success_until_task_confirms_completion(self):
        payload = progress_payload()
        payload["aggregate"]["percent"] = 100
        self.assertEqual(restore_progress_view(payload, status="running")["progress_percent"], 99)
        self.assertEqual(restore_progress_view(payload, status="success")["progress_percent"], 100)

    def test_invalid_progress_is_never_published(self):
        for value in ("bad", float("inf"), float("nan"), -1, True):
            payload = progress_payload()
            payload["aggregate"]["percent"] = value
            payload["transfer_progress"]["eta_seconds"] = value
            view = restore_progress_view(payload, status="running")
            self.assertIsNone(view["progress_percent"])
            self.assertIsNone(view["eta_seconds"])

    def link(self, **overrides):
        return SimpleNamespace(
            lifecycle_status="provisioning", provision_phase="restoring",
            provision_state_json={}, organization_id=3,
            knowledge_source=SimpleNamespace(
                pk=7, organization_id=3,
                sync_state_json={"restore_record_id": 11, "snapshot_id_used": 21},
            ),
            **overrides,
        )

    @patch("apps.lens_bridge.services.preparation_progress.RestoreRecord.objects.filter")
    def test_no_restore_queries_for_ready_or_reused_chats(self, records):
        link = self.link()
        link.lifecycle_status = "ready"
        self.assertIsNone(preparation_progress_for_session(link))
        link.lifecycle_status = "provisioning"
        link.provision_phase = "creating_session"
        link.provision_state_json = {"reuse_existing_resources": {"source_session_id": 9}}
        self.assertTrue(preparation_progress_for_session(link)["reused_data"])
        records.assert_not_called()

    @patch("apps.lens_bridge.services.preparation_progress.RestoreRecord.objects.filter")
    def test_nonreused_chat_keeps_restore_summary(self, records):
        records.return_value.first.return_value = None
        result = preparation_progress_for_session(self.link())
        self.assertFalse(result["reused_data"])
        records.assert_called_once()

    @patch("apps.lens_bridge.services.preparation_progress.RestoreRecord.objects.filter")
    def test_foreign_knowledge_source_is_rejected(self, records):
        link = self.link()
        link.knowledge_source.organization_id = 4
        self.assertIsNone(preparation_progress_for_session(link)["restore"])
        records.assert_not_called()

    @patch("apps.lens_bridge.services.preparation_progress._restore_metrics_are_fresh", return_value=True)
    @patch("apps.lens_bridge.services.preparation_progress.build_restore_kopia_progress")
    @patch("apps.lens_bridge.services.preparation_progress.Task.objects.filter")
    @patch("apps.lens_bridge.services.preparation_progress.RestoreRecord.objects.filter")
    def test_restore_query_is_bound_to_tenant_snapshot_and_workspace(self, records, tasks, build, _fresh):
        records.return_value.first.return_value = SimpleNamespace(task_id=19, task_uuid="task-uuid")
        tasks.return_value.first.return_value = SimpleNamespace(status="running")
        build.return_value = progress_payload()
        view = preparation_progress_for_session(self.link())
        self.assertEqual(view["restore"]["progress_percent"], 62)
        kwargs = records.call_args.kwargs
        self.assertEqual(kwargs["organization_id"], 3)
        self.assertEqual(kwargs["source_snapshot_id"], 21)
        self.assertEqual(kwargs["purpose"], RestoreRecord.Purpose.LENS_WORKSPACE)
        binding_query = str(kwargs["workspace_binding_id__in"].query)
        self.assertIn('"knowledge_source_id" = 7', binding_query)
        self.assertIn('"organization_id" = 3', binding_query)
        tasks.assert_called_once_with(pk=19, organization_id=3, task_uuid="task-uuid")

    @patch("apps.lens_bridge.services.preparation_progress.build_restore_kopia_progress")
    @patch("apps.lens_bridge.services.preparation_progress.RestoreRecord.objects.filter")
    def test_unowned_restore_is_not_read(self, records, build):
        records.return_value.first.return_value = None
        self.assertIsNone(preparation_progress_for_session(self.link())["restore"])
        build.assert_not_called()

    def test_workspace_filter_compiles_as_a_subquery_without_database_access(self):
        bindings = LensWorkspaceBinding.objects.filter(
            knowledge_source_id=7, organization_id=3,
        ).values_list("pk", flat=True)
        query = RestoreRecord.objects.filter(
            organization_id=3, workspace_binding_id__in=bindings,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
        )
        self.assertIn('"workspace_binding_id" IN (SELECT', str(query.query))

    @patch("apps.lens_bridge.services.preparation_progress.preparation_progress_for_session")
    def test_serializer_exposes_progress_summary(self, service):
        service.return_value = {"reused_data": False, "restore": None}
        self.assertIn("preparation_progress", LensSessionLinkSerializer().fields)
        self.assertEqual(
            LensSessionLinkSerializer().get_preparation_progress(self.link()),
            service.return_value,
        )


class PreparationProgressIntegrationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="preparation-tenant", name="Tenant")
        platform = Organization.objects.create(key="preparation-platform", name="Platform")
        user = get_user_model().objects.create_user(username="preparation@test.example")
        node = Node.objects.create(organization=platform, name="Gateway", role=Node.Role.GATEWAY)
        gateway = LensGatewayLink.objects.create(
            organization=platform, gateway=node,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )
        self.ks = LensKnowledgeSource.objects.create(
            organization=self.org, name="Data", gateway=node,
            gateway_link=gateway, created_by=user,
        )
        binding = LensWorkspaceBinding.objects.create(
            organization=self.org, knowledge_source=self.ks, gateway_link=gateway,
            execution_organization_id=platform.id, execution_node_id=node.id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
            workspace_root="/workspace", relative_path="tenants/data",
        )
        self.link = LensSessionLink.objects.create(
            organization=self.org, hfl_user=user, knowledge_source=self.ks,
            gateway_link=gateway, lifecycle_status="provisioning", provision_phase="restoring",
        )
        self.task = Task.objects.create(
            organization_id=self.org.id, task_type=Task.Type.RESTORE,
            display_name="Prepare data", status=Task.Status.RUNNING,
        )
        self.record = RestoreRecord.objects.create(
            organization_id=self.org.id, requesting_organization_id=self.org.id,
            target_execution_organization_id=platform.id, target_execution_node_id=node.id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE, workspace_binding_id=binding.id,
            idempotency_key="prepare-data", restore_uid="prepare-data",
            source_mode="manual", task_id=self.task.id, task_uuid=self.task.task_uuid,
            source_type="agent", source_ref_id=1, source_snapshot_id=21,
            target_type="agent", target_ref_id=node.id, target_path="/workspace",
            scope="paths", conflict_mode="overwrite",
        )
        self.ks.sync_state_json = {"restore_record_id": self.record.id, "snapshot_id_used": 21}
        self.ks.save(update_fields=["sync_state_json"])
        self.item = RestoreRecordItem.objects.create(
            organization_id=self.org.id, restore_record=self.record,
            source_snapshot_directory_id=1, backup_config_dir_id=1,
            repository_id=1, kopia_snapshot_id="snapshot", source_path="/reports",
            target_path="/workspace", selected_paths=["report.pdf"],
            conflict_mode="overwrite", status="running",
            last_progress_snapshot={
                "phase": "kopia_transfer", "kopia_phase": "restoring",
                "bytes_done": 620_000_000, "bytes_total": 1_000_000_000,
                "bytes_total_known": True, "percent": 62,
                "speed_bps": 10_000_000,
            },
            last_progress_sample={"sampled_at": timezone.now().isoformat()},
        )

    def test_real_restore_metrics_are_read_without_mutating_tasks(self):
        view = preparation_progress_for_session(self.link)["restore"]
        self.assertEqual(view["progress_percent"], 62)
        self.assertEqual(view["bytes_done"], 620_000_000)
        self.assertEqual(view["bytes_total"], 1_000_000_000)
        self.assertIsNotNone(view["eta_seconds"])
        self.item.refresh_from_db()
        self.task.refresh_from_db()
        self.assertNotIn("processing_speed_bps", self.item.last_progress_snapshot)
        self.assertIsNone(self.task.result_payload)

    def test_finished_restore_remains_available_during_conversion(self):
        self.task.status = Task.Status.SUCCESS
        self.task.save(update_fields=["status"])
        self.item.status = RestoreRecordItem.Status.SUCCESS
        self.item.result_payload = {
            "restore_scope_summary": {"complete": True, "size_bytes": 630_000_000, "total_count": 1},
        }
        self.item.save(update_fields=["status", "result_payload"])
        self.link.provision_phase = "converting"
        view = preparation_progress_for_session(self.link)["restore"]
        self.assertEqual(view["status"], "success")
        self.assertEqual(view["bytes_done"], 630_000_000)
        self.assertIsNone(view["eta_seconds"])

    def test_active_restore_is_not_reported_as_a_completed_later_step(self):
        self.link.provision_phase = "creating_assistant"
        self.assertIsNone(preparation_progress_for_session(self.link)["restore"])

    def test_expired_restore_metrics_do_not_show_eta(self):
        self.item.last_progress_sample = {
            "sampled_at": (timezone.now() - timedelta(seconds=60)).isoformat(),
        }
        self.item.save(update_fields=["last_progress_sample"])
        self.assertIsNone(preparation_progress_for_session(self.link)["restore"]["eta_seconds"])

    def test_invalid_restore_sample_timestamp_does_not_break_chat_listing(self):
        for value in ("invalid", "2026-02-30T00:00:00Z"):
            self.item.last_progress_sample = {"sampled_at": value}
            self.item.save(update_fields=["last_progress_sample"])
            self.assertIsNone(preparation_progress_for_session(self.link)["restore"]["eta_seconds"])

    def test_mismatched_tenant_snapshot_and_workspace_are_not_exposed(self):
        original = {
            "organization_id": self.org.id,
            "source_snapshot_id": 21,
            "workspace_binding_id": self.record.workspace_binding_id,
        }
        for field, bad_value in (
            ("organization_id", self.org.id + 100),
            ("source_snapshot_id", 99),
            ("workspace_binding_id", self.record.workspace_binding_id + 100),
        ):
            RestoreRecord.objects.filter(pk=self.record.id).update(**{**original, field: bad_value})
            self.assertIsNone(preparation_progress_for_session(self.link)["restore"])

    def test_mismatched_task_tenant_is_not_exposed(self):
        self.task.organization_id += 100
        self.task.save(update_fields=["organization_id"])
        self.assertIsNone(preparation_progress_for_session(self.link)["restore"])
