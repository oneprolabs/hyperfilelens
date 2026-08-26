from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import TestCase

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.services.internal.task import complete_task as complete_node_task
from apps.restore.models import RestoreRecord
from apps.source.constants import PipelineStep, ResourceType, SelectableSourceKind
from apps.source.models import SourceBackupPipelineEntry, SourceResource
from apps.source.services.internal.source_pipeline import (
    ensure_pipeline_entry,
    reconcile_pipeline_projections,
    revert_backup_flow_sources,
    sync_pipeline_projection_with_retry,
)
from apps.task.models import Task, TaskResource
from apps.task.services.interface import complete_task, create_task, start_task


class SourcePipelineProjectionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="pipeline-projection", name="Pipeline Projection"
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="agent-display",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            connection_ip_address="198.51.100.10",
            metadata={"inventory": {"hostname": "agent-reported"}},
        )

    @mock.patch("apps.source.services.internal.source_pipeline.time.sleep")
    @mock.patch("apps.source.services.internal.source_pipeline.sync_pipeline_projection")
    def test_projection_retries_transient_database_deadlock(
        self, sync_projection, sleep
    ):
        expected = object()
        sync_projection.side_effect = [
            OperationalError("deadlock detected"),
            expected,
        ]

        result = sync_pipeline_projection_with_retry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )

        self.assertIs(result, expected)
        self.assertEqual(sync_projection.call_count, 2)
        sleep.assert_called_once_with(0.05)

    def test_agent_projection_uses_hostname_and_connection_ip_fallback(self):
        entry = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )
        self.assertEqual(entry.source_name, "agent-display")
        self.assertEqual(entry.source_hostname, "agent-reported")
        self.assertEqual(entry.source_ip, "198.51.100.10")
        self.assertEqual(entry.source_status, Node.Status.ACTIVE)
        self.assertEqual(entry.source_availability, Node.Availability.ONLINE)
        self.assertEqual(entry.created_at, self.agent.created_at)

    def test_upgrade_failure_refreshes_agent_pipeline_status_without_changing_availability(
        self,
    ):
        entry = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="agent.upgrade",
            status=NodeTask.Status.PENDING,
            watchdog_deadline_at=self.agent.created_at,
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.agent.id}",
        )

        complete_node_task(
            task_id=task.id,
            node_id=self.agent.id,
            status=NodeTask.Status.FAILED,
            error="upgrade package verification failed",
        )

        self.agent.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(self.agent.status, Node.Status.UPGRADE_FAILED)
        self.assertEqual(self.agent.availability, Node.Availability.ONLINE)
        self.assertEqual(entry.source_status, Node.Status.UPGRADE_FAILED)
        self.assertEqual(entry.source_availability, Node.Availability.ONLINE)

    def test_nas_without_proxy_projects_empty_identity_and_offline(self):
        source = SourceResource.objects.create(
            organization=self.org,
            name="nas-without-proxy",
            resource_type=ResourceType.NAS,
            availability="online",
        )
        entry = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.NAS,
            ref_id=source.id,
        )
        self.assertEqual(entry.source_hostname, "")
        self.assertEqual(entry.source_ip, "")
        self.assertEqual(entry.source_availability, "offline")

    def test_deleted_entry_is_revived_with_refreshed_projection(self):
        entry = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )
        entry.soft_delete()
        self.agent.name = "renamed-agent"
        self.agent.save(update_fields=["name", "updated_at"])

        revived = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )

        self.assertEqual(revived.pk, entry.pk)
        self.assertFalse(revived.is_deleted)
        self.assertEqual(revived.source_name, "renamed-agent")

    def test_revert_to_step_one_keeps_explicit_pipeline_entry(self):
        entry = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
            step=PipelineStep.CONFIG,
        )
        entry.step = PipelineStep.CONFIG
        entry.save(update_fields=["step", "updated_at"])
        updated = revert_backup_flow_sources(
            organization_id=self.org.id,
            ids=[f"agent:{self.agent.id}"],
            target_step=PipelineStep.SOURCE_POOL,
        )
        self.assertEqual(updated, [f"agent:{self.agent.id}"])
        entry.refresh_from_db()
        self.assertEqual(entry.step, PipelineStep.SOURCE_POOL)
        self.assertFalse(entry.is_deleted)

    def test_newer_task_projection_is_not_overwritten_by_old_terminal_event(self):
        first = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="older backup",
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                    "is_primary": True,
                }
            ],
        )
        second = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="newer backup",
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                    "is_primary": True,
                }
            ],
        )

        complete_task(
            task_uuid=first.task_uuid,
            organization_id=self.org.id,
            status=Task.Status.SUCCESS,
        )

        entry = SourceBackupPipelineEntry.objects.get(
            organization=self.org,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )
        self.assertEqual(entry.last_backup_task_id, second.id)
        self.assertEqual(entry.last_backup_status, "queued")

        start_task(task_uuid=second.task_uuid, organization_id=self.org.id)
        entry.refresh_from_db()
        self.assertEqual(entry.last_backup_task_id, second.id)
        self.assertEqual(entry.last_backup_status, "running")

    def test_legacy_insight_restore_is_not_projected_as_user_restore(self):
        user_restore = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="User restore",
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                    "is_primary": True,
                }
            ],
        )
        insight_restore = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Legacy insight restore",
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                    "is_primary": True,
                }
            ],
        )
        RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=self.org.id,
            target_execution_node_id=self.agent.id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key="pipeline-legacy-insight",
            workspace_binding_id=100,
            restore_uid="pipeline-legacy-insight",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=insight_restore.id,
            task_uuid=insight_restore.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=self.agent.id,
            source_snapshot_id=100,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=self.agent.id,
            target_path="/tmp/insight",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )

        entry = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )

        self.assertEqual(entry.last_restore_task_id, user_restore.id)
        self.assertEqual(entry.last_restore_status, "queued")

    def test_classified_insight_task_recomputes_stale_restore_projection(self):
        user_restore = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="User restore",
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                    "is_primary": True,
                }
            ],
        )
        insight_restore = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Legacy insight restore",
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                    "is_primary": True,
                }
            ],
        )
        entry = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )
        self.assertEqual(entry.last_restore_task_id, insight_restore.id)

        RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=self.org.id,
            target_execution_node_id=self.agent.id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key="pipeline-classified-insight",
            workspace_binding_id=101,
            restore_uid="pipeline-classified-insight",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=insight_restore.id,
            task_uuid=insight_restore.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=self.agent.id,
            source_snapshot_id=101,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=self.agent.id,
            target_path="/tmp/insight-classified",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )
        insight_restore.task_type = Task.Type.INSIGHT_WORKSPACE_RESTORE
        insight_restore.save(update_fields=["task_type", "updated_at"])
        complete_task(
            task_uuid=insight_restore.task_uuid,
            organization_id=self.org.id,
            status=Task.Status.FAILED,
        )

        entry.refresh_from_db()
        self.assertEqual(entry.last_restore_task_id, user_restore.id)
        self.assertEqual(entry.last_restore_status, "queued")

    def test_proxy_change_fans_out_to_bound_nas_projection(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-before",
            role=Node.Role.PROXY,
            ip_address="198.51.100.20",
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        nas = SourceResource.objects.create(
            organization=self.org,
            name="nas-through-proxy",
            resource_type=ResourceType.NAS,
            bound_node=proxy,
            availability="online",
        )
        proxy.name = "proxy-after"
        proxy.ip_address = "198.51.100.21"
        proxy.save(update_fields=["name", "ip_address", "updated_at"])

        entry = SourceBackupPipelineEntry.objects.get(
            organization=self.org,
            source_kind=SelectableSourceKind.NAS,
            ref_id=nas.id,
        )
        self.assertEqual(entry.source_hostname, "proxy-after")
        self.assertEqual(entry.source_ip, "198.51.100.21")

    def test_reconciliation_repairs_deleted_projection(self):
        SourceBackupPipelineEntry.objects.create(
            organization=self.org,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
            is_deleted=True,
        )

        result = reconcile_pipeline_projections(limit=10)

        entry = SourceBackupPipelineEntry.objects.get(
            organization=self.org,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=self.agent.id,
        )
        self.assertGreaterEqual(result["repaired"], 1)
        self.assertFalse(entry.is_deleted)


class RebuildSourcePipelineCommandTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="pipeline-rebuild", name="Pipeline Rebuild"
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="rebuild-agent",
            role=Node.Role.AGENT,
        )

    def test_dry_run_then_apply_is_idempotent(self):
        dry_run = StringIO()
        call_command(
            "rebuild_source_backup_pipeline",
            organization_id=self.org.id,
            stdout=dry_run,
        )
        self.assertIn("missing=1", dry_run.getvalue())
        self.assertFalse(SourceBackupPipelineEntry.objects.exists())

        applied = StringIO()
        call_command(
            "rebuild_source_backup_pipeline",
            organization_id=self.org.id,
            apply=True,
            stdout=applied,
        )
        self.assertTrue(
            SourceBackupPipelineEntry.objects.filter(
                organization=self.org, source_kind="agent", ref_id=self.agent.id
            ).exists()
        )

        second = StringIO()
        call_command(
            "rebuild_source_backup_pipeline",
            organization_id=self.org.id,
            apply=True,
            stdout=second,
        )
        self.assertIn("created=0", second.getvalue())
        self.assertIn("updated=0", second.getvalue())

    def test_apply_quarantines_stale_rows_and_reports_missing_proxy(self):
        stale = SourceBackupPipelineEntry.objects.create(
            organization=self.org,
            source_kind=SelectableSourceKind.NAS,
            ref_id=999999,
        )
        SourceResource.objects.create(
            organization=self.org,
            name="unbound-nas",
            resource_type=ResourceType.NAS,
        )

        output = StringIO()
        call_command(
            "rebuild_source_backup_pipeline",
            organization_id=self.org.id,
            apply=True,
            stdout=output,
        )

        stale.refresh_from_db()
        self.assertTrue(stale.is_deleted)
        self.assertIn("quarantined=1", output.getvalue())
        self.assertIn("nas_without_proxy=1", output.getvalue())

    def test_rejects_invalid_batch_size(self):
        with self.assertRaises(CommandError):
            call_command("rebuild_source_backup_pipeline", batch_size=0)
