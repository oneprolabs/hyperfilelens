from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase

from apps.node.models import NodeTask
from apps.restore.models import RestoreRecordItem
from apps.restore.services.reconciliation import (
    _candidate_terminal_node_tasks,
    _projection_needs_replay,
    reconcile_restore_node_task_projections,
)
from apps.restore.signals import (
    _project_cancelled_restore_item,
    sync_restore_record_from_node_task,
)
from apps.task.models import Task


class RestoreProjectionReconciliationTests(SimpleTestCase):
    @patch("apps.restore.signals._project_cancelled_restore_item")
    @patch("apps.restore.signals._sync_restore_item")
    @patch("apps.restore.signals.Task.objects")
    @patch("apps.restore.signals.RestoreRecordItem.objects")
    def test_cancelled_product_task_repairs_projection_not_late_result(
        self,
        item_objects,
        task_objects,
        sync_item,
        project_cancelled_item,
    ):
        record = SimpleNamespace(
            organization_id=11,
            task_uuid="task-uuid",
            target_execution_organization_id=22,
            target_execution_node_id=7,
        )
        item_objects.select_related.return_value.filter.return_value.first.return_value = SimpleNamespace(
            restore_record=record
        )
        product_task = SimpleNamespace(status=Task.Status.CANCELLED)
        task_objects.select_for_update.return_value.filter.return_value.first.return_value = product_task
        node_task = SimpleNamespace(
            id="node-task-id",
            correlation_type="restore.record",
            correlation_id="task-uuid",
            organization_id=22,
            node_id=7,
            payload={"restore_record_item_id": 31},
            status=NodeTask.Status.SUCCESS,
        )

        sync_restore_record_from_node_task.__wrapped__(NodeTask, node_task)

        sync_item.assert_not_called()
        project_cancelled_item.assert_called_once_with(
            record=record,
            item_id=31,
            node_task=node_task,
            product_task=product_task,
        )
        item_objects.select_for_update.assert_not_called()

    @patch("apps.restore.signals.timezone.now", return_value="projected-at")
    @patch("apps.restore.signals.append_restore_item_terminal_event")
    @patch("apps.restore.signals.RestoreRecordItem.objects")
    def test_cancelled_projection_converges_running_item_once(
        self,
        item_objects,
        append_event,
        _now,
    ):
        record = SimpleNamespace(organization_id=11)
        item = SimpleNamespace(
            id=31,
            status=RestoreRecordItem.Status.RUNNING,
            node_task_id="node-task-id",
            error_code="",
            error_message="",
            terminal_projection_at=None,
            save=MagicMock(),
        )
        item_objects.select_for_update.return_value.filter.return_value.first.return_value = item
        product_task = SimpleNamespace(error_message="Stopped from console")
        node_task = SimpleNamespace(id="late-success-id")

        _project_cancelled_restore_item(
            record=record,
            item_id=item.id,
            node_task=node_task,
            product_task=product_task,
        )

        self.assertEqual(item.status, RestoreRecordItem.Status.CANCELLED)
        self.assertEqual(item.error_code, "TASK_CANCELLED")
        self.assertEqual(item.error_message, "Stopped from console")
        self.assertEqual(item.terminal_projection_at, "projected-at")
        append_event.assert_called_once_with(
            task=product_task,
            item=item,
            node_task_id="node-task-id",
            previous_status=RestoreRecordItem.Status.RUNNING,
        )
        self.assertEqual(item.save.call_count, 2)

    @patch("apps.restore.signals.timezone.now", return_value="projected-at")
    @patch("apps.restore.signals.append_restore_item_terminal_event")
    @patch("apps.restore.signals.RestoreRecordItem.objects")
    def test_cancelled_projection_preserves_terminal_item(
        self,
        item_objects,
        append_event,
        _now,
    ):
        record = SimpleNamespace(organization_id=11)
        item = SimpleNamespace(
            id=31,
            status=RestoreRecordItem.Status.FAILED,
            node_task_id="node-task-id",
            error_code="",
            error_message="",
            terminal_projection_at=None,
            save=MagicMock(),
        )
        item_objects.select_for_update.return_value.filter.return_value.first.return_value = item
        product_task = SimpleNamespace(error_message="Stopped from console")
        node_task = SimpleNamespace(id="late-success-id")

        _project_cancelled_restore_item(
            record=record,
            item_id=item.id,
            node_task=node_task,
            product_task=product_task,
        )

        self.assertEqual(item.status, RestoreRecordItem.Status.FAILED)
        self.assertEqual(item.terminal_projection_at, "projected-at")
        append_event.assert_called_once()
        item.save.assert_called_once_with(
            update_fields=["terminal_projection_at", "updated_at"]
        )

    @patch("apps.restore.services.reconciliation._resume_stranded_insight_restore_items")
    @patch("apps.restore.services.reconciliation.sync_restore_record_from_node_task")
    @patch(
        "apps.restore.services.reconciliation._classify_terminal_legacy_insight_tasks",
        return_value=(1, 0),
    )
    @patch("apps.restore.services.reconciliation._projection_needs_replay")
    @patch("apps.restore.services.reconciliation._candidate_terminal_node_tasks")
    def test_replays_only_incomplete_durable_projections(
        self, candidates, needs_replay, classify, projector, resume_insight
    ):
        first = SimpleNamespace(id="first")
        second = SimpleNamespace(id="second")
        candidates.return_value = [first, second]
        needs_replay.side_effect = (True, False)

        result = reconcile_restore_node_task_projections(limit=20)

        self.assertEqual(
            result,
            {
                "candidates": 2,
                "replayed": 1,
                "replay_failed": 0,
                "classified": 1,
                "classification_failed": 0,
            },
        )
        classify.assert_called_once_with(limit=20)
        resume_insight.assert_called_once_with(limit=20)
        self.assertEqual(projector.call_args_list, [call(NodeTask, first)])

    @patch("apps.restore.services.reconciliation._resume_stranded_insight_restore_items")
    @patch("apps.restore.services.reconciliation.logger.exception")
    @patch(
        "apps.restore.services.reconciliation._classify_terminal_legacy_insight_tasks",
        return_value=(2, 1),
    )
    @patch(
        "apps.restore.services.reconciliation._projection_needs_replay",
        return_value=True,
    )
    @patch("apps.restore.services.reconciliation._candidate_terminal_node_tasks")
    @patch("apps.restore.services.reconciliation.sync_restore_record_from_node_task")
    def test_one_projection_failure_does_not_block_remaining_reconciliation(
        self,
        projector,
        candidates,
        _needs_replay,
        classify,
        log_exception,
        resume_insight,
    ):
        first = SimpleNamespace(id="first")
        second = SimpleNamespace(id="second")
        candidates.return_value = [first, second]
        projector.side_effect = (RuntimeError("projection unavailable"), None)

        result = reconcile_restore_node_task_projections(limit=20)

        self.assertEqual(
            result,
            {
                "candidates": 2,
                "replayed": 1,
                "replay_failed": 1,
                "classified": 2,
                "classification_failed": 1,
            },
        )
        self.assertEqual(
            projector.call_args_list,
            [call(NodeTask, first), call(NodeTask, second)],
        )
        classify.assert_called_once_with(limit=20)
        resume_insight.assert_called_once_with(limit=20)
        log_exception.assert_called_once()

    @patch("apps.restore.services.reconciliation._resume_stranded_insight_restore_items")
    @patch("apps.restore.services.reconciliation.logger.exception")
    @patch(
        "apps.restore.services.reconciliation._classify_terminal_legacy_insight_tasks",
        return_value=(0, 0),
    )
    @patch("apps.restore.services.reconciliation._projection_needs_replay")
    @patch("apps.restore.services.reconciliation._candidate_terminal_node_tasks")
    @patch("apps.restore.services.reconciliation.sync_restore_record_from_node_task")
    def test_one_replay_check_failure_does_not_block_remaining_reconciliation(
        self,
        projector,
        candidates,
        needs_replay,
        _classify,
        log_exception,
        resume_insight,
    ):
        first = SimpleNamespace(id="first")
        second = SimpleNamespace(id="second")
        candidates.return_value = [first, second]
        needs_replay.side_effect = (RuntimeError("relation unavailable"), True)

        result = reconcile_restore_node_task_projections(limit=20)

        self.assertEqual(result["replayed"], 1)
        self.assertEqual(result["replay_failed"], 1)
        projector.assert_called_once_with(NodeTask, second)
        resume_insight.assert_called_once_with(limit=20)
        log_exception.assert_called_once()

    @patch("apps.restore.services.reconciliation.NodeTask.objects")
    @patch("apps.restore.services.reconciliation.RestoreRecordItem.objects")
    def test_candidates_start_from_unprojected_items_not_latest_tasks(
        self, item_objects, node_objects
    ):
        item_queryset = MagicMock()
        item_objects.filter.return_value = item_queryset
        item_queryset.order_by.return_value.values.return_value = MagicMock()
        node = SimpleNamespace(id="node-1")
        node_objects.filter.return_value.order_by.return_value.__getitem__.return_value = [
            node
        ]

        result = _candidate_terminal_node_tasks(limit=1)

        self.assertEqual(result, [node])
        item_objects.filter.assert_called_once_with(
            node_task_id__isnull=False,
            terminal_projection_at__isnull=True,
        )
        item_queryset.order_by.assert_called_once_with()
        item_queryset.order_by.return_value.values.assert_called_once_with(
            "node_task_id"
        )

    @patch("apps.restore.services.reconciliation.Task.objects")
    @patch("apps.restore.services.reconciliation.RestoreRecordItem.objects")
    def test_repairs_item_without_terminal_projection(self, item_objects, task_objects):
        record = SimpleNamespace(organization_id=11, task_uuid="task-uuid")
        item = SimpleNamespace(
            id=31,
            terminal_projection_at=None,
            restore_record=record,
        )
        item_objects.select_related.return_value.filter.return_value.first.return_value = item
        task_objects.filter.return_value.first.return_value = SimpleNamespace(
            status=Task.Status.SUCCESS
        )
        node_task = SimpleNamespace(
            correlation_type="restore.record",
            payload={"restore_record_item_id": item.id},
            id="node-task-id",
        )

        self.assertTrue(_projection_needs_replay(node_task=node_task))

    @patch("apps.restore.services.reconciliation.Task.objects")
    @patch("apps.restore.services.reconciliation.RestoreRecordItem.objects")
    def test_skips_projected_item_while_another_item_is_active(
        self, item_objects, task_objects
    ):
        record = SimpleNamespace(organization_id=11, task_uuid="task-uuid")
        item = SimpleNamespace(
            id=31,
            terminal_projection_at=object(),
            restore_record=record,
        )
        item_objects.select_related.return_value.filter.return_value.first.return_value = item
        item_objects.filter.return_value.exists.return_value = True
        task_objects.filter.return_value.first.return_value = SimpleNamespace(
            status=Task.Status.RUNNING
        )
        node_task = SimpleNamespace(
            correlation_type="restore.record",
            payload={"restore_record_item_id": item.id},
            id="node-task-id",
        )

        self.assertFalse(_projection_needs_replay(node_task=node_task))

    @patch("apps.restore.services.reconciliation.Task.objects")
    @patch("apps.restore.services.reconciliation.RestoreRecordItem.objects")
    def test_finalizes_product_task_when_all_items_are_terminal(
        self, item_objects, task_objects
    ):
        record = SimpleNamespace(organization_id=11, task_uuid="task-uuid")
        item = SimpleNamespace(
            id=31,
            terminal_projection_at=object(),
            restore_record=record,
        )
        item_objects.select_related.return_value.filter.return_value.first.return_value = item
        item_objects.filter.return_value.exists.return_value = False
        task_objects.filter.return_value.first.return_value = SimpleNamespace(
            status=Task.Status.RUNNING
        )
        node_task = SimpleNamespace(
            correlation_type="restore.record",
            payload={"restore_record_item_id": item.id},
            id="node-task-id",
        )

        self.assertTrue(_projection_needs_replay(node_task=node_task))

    @patch("apps.restore.services.reconciliation.Task.objects")
    @patch("apps.restore.services.reconciliation.RestoreRecordItem.objects")
    @patch("apps.restore.services.reconciliation.RestoreRecord.objects")
    def test_repository_server_replay_finalizes_all_terminal_items(
        self, record_objects, item_objects, task_objects
    ):
        record = SimpleNamespace(organization_id=11, task_uuid="task-uuid")
        record_objects.filter.return_value.first.return_value = record
        task_objects.filter.return_value.first.return_value = SimpleNamespace(
            status=Task.Status.RUNNING
        )
        item_objects.filter.return_value.exists.return_value = False
        node_task = SimpleNamespace(
            correlation_type="restore.repository_server",
            correlation_id="task-uuid",
        )

        self.assertTrue(_projection_needs_replay(node_task=node_task))
