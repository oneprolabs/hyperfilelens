import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.protection.models import BackupSourceSnapshot
from apps.restore.api.views.restore_record import (
    _datetime_query_param,
    _restore_record_search_fields,
)
from apps.restore.models import RestoreRecord
from apps.restore.selectors.interface import filter_restore_records
from apps.task.models import Task


class RestoreRecordFilterTests(TestCase):
    organization_id = 801

    def _task(self, task_uuid: uuid.UUID, *, status: str):
        return Task.objects.create(
            organization_id=self.organization_id,
            task_uuid=task_uuid,
            task_type=Task.Type.RESTORE,
            display_name=f"Restore {task_uuid}",
            status=status,
        )

    def _snapshot(self, uid: str):
        return BackupSourceSnapshot.objects.create(
            organization_id=self.organization_id,
            snapshot_uid=uid,
            idempotency_key=f"idem-{uid}",
            source_type="agent",
            source_ref_id=11,
            backup_config_id=21,
            repository_id=31,
            task_id=41,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )

    def _record(
        self, uid: str, *, task: Task, snapshot: BackupSourceSnapshot, source_mode: str
    ):
        return RestoreRecord.objects.create(
            organization_id=self.organization_id,
            requesting_organization_id=self.organization_id,
            target_execution_organization_id=self.organization_id,
            target_execution_node_id=51,
            restore_uid=uid,
            source_mode=source_mode,
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=11,
            backup_config_id=21,
            source_snapshot_id=snapshot.id,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=61,
            target_path="/restore",
            scope=RestoreRecord.Scope.SNAPSHOT,
            conflict_mode=RestoreRecord.ConflictMode.SKIP,
        )

    def setUp(self):
        self.now = timezone.now()
        self.success_task = self._task(
            uuid.UUID("11111111-2222-3333-4444-555555555555"),
            status=Task.Status.SUCCESS,
        )
        self.failed_task = self._task(
            uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
            status=Task.Status.FAILED,
        )
        self.target_snapshot = self._snapshot("bss-shared-keyword")
        self.other_snapshot = self._snapshot("bss-other")
        self.plan_record = self._record(
            "rst-plan",
            task=self.success_task,
            snapshot=self.target_snapshot,
            source_mode=RestoreRecord.SourceMode.PLAN,
        )
        self.manual_record = self._record(
            "rst-shared-keyword",
            task=self.failed_task,
            snapshot=self.other_snapshot,
            source_mode=RestoreRecord.SourceMode.MANUAL,
        )
        RestoreRecord.objects.filter(pk=self.plan_record.pk).update(
            created_at=self.now - timedelta(hours=2)
        )
        RestoreRecord.objects.filter(pk=self.manual_record.pk).update(
            created_at=self.now - timedelta(days=2)
        )

    def _filter(self, **kwargs):
        return filter_restore_records(
            RestoreRecord.objects.filter(organization_id=self.organization_id),
            organization_id=self.organization_id,
            **kwargs,
        )

    def test_selected_search_fields_are_or_matched(self):
        result = self._filter(
            search="shared-keyword",
            search_fields=["restore_uid", "snapshot_uid"],
        )

        self.assertCountEqual(
            result.values_list("id", flat=True),
            [self.plan_record.id, self.manual_record.id],
        )

    def test_unselected_search_fields_do_not_match(self):
        result = self._filter(search="shared-keyword", search_fields=["task_uuid"])

        self.assertFalse(result.exists())

    def test_filters_task_uuid_status_source_mode_and_created_range(self):
        result = self._filter(
            search="11111111",
            search_fields=["task_uuid"],
            status=Task.Status.SUCCESS,
            source_mode=RestoreRecord.SourceMode.PLAN,
            created_from=self.now - timedelta(days=1),
            created_to=self.now,
        )

        self.assertEqual(
            list(result.values_list("id", flat=True)), [self.plan_record.id]
        )

    def test_rejects_invalid_search_fields_and_created_datetime(self):
        with self.assertRaises(ValidationError):
            _restore_record_search_fields("restore_uid,unknown")
        with self.assertRaises(ValidationError):
            _restore_record_search_fields(",")
        with self.assertRaises(ValidationError):
            _datetime_query_param("not-a-date", "created_from")
