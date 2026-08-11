from django.test import TestCase

from apps.iam.models import Organization
from apps.node.models import Node
from apps.source.models import SourceBackupPipelineEntry
from apps.source.services.internal.source_pipeline import (
    ensure_pipeline_entry,
    force_set_pipeline_steps,
    set_pipeline_steps,
)
from apps.task.models import Task, TaskResource
from apps.task.services.interface import create_task, start_task


class SourcePipelineOperationFenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="source-pipeline-fence",
            name="Source Pipeline Fence",
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="pipeline-fence-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        self.entry = SourceBackupPipelineEntry.objects.create(
            organization=self.org,
            source_kind="agent",
            ref_id=self.agent.id,
            step=1,
        )
        self.operation = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_RESET,
            display_name="Reset source configuration",
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": self.agent.id,
                    "is_primary": True,
                }
            ],
        )
        start_task(
            task_uuid=self.operation.task_uuid,
            organization_id=self.org.id,
        )

    def test_active_operation_blocks_normal_pipeline_upserts(self):
        selectable_id = f"agent:{self.agent.id}"

        updated = set_pipeline_steps(
            organization_id=self.org.id,
            ids=[selectable_id],
            step=2,
        )
        ensured = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind="agent",
            ref_id=self.agent.id,
            step=3,
        )

        self.entry.refresh_from_db()
        self.assertEqual(updated, [])
        self.assertEqual(ensured.id, self.entry.id)
        self.assertEqual(self.entry.step, 1)

    def test_blocked_deregistration_still_owns_the_pipeline_fence(self):
        self.operation.task_type = Task.Type.SOURCE_UNREGISTER
        self.operation.status = Task.Status.BLOCKED
        self.operation.save(update_fields=["task_type", "status", "updated_at"])

        updated = set_pipeline_steps(
            organization_id=self.org.id,
            ids=[f"agent:{self.agent.id}"],
            step=2,
        )

        self.entry.refresh_from_db()
        self.assertEqual(updated, [])
        self.assertEqual(self.entry.step, 1)

    def test_operation_owner_can_finalize_its_pipeline_change(self):
        selectable_id = f"agent:{self.agent.id}"

        updated = force_set_pipeline_steps(
            organization_id=self.org.id,
            ids=[selectable_id],
            step=2,
            operation_task_uuid=str(self.operation.task_uuid),
        )

        self.entry.refresh_from_db()
        self.assertEqual(updated, [selectable_id])
        self.assertEqual(self.entry.step, 2)
