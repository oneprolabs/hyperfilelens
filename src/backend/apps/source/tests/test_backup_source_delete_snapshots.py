from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.constants import AuditResult
from apps.audit.models import AuditLog
from apps.audit.services.interface import write_audit_log as persist_audit_log
from apps.iam.models import Membership, Organization
from apps.node import agent_paths
from apps.node.models import Node, NodeTask
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupSourceSnapshotDirectory,
)
from apps.protection.services.backup_source_snapshot import create_source_snapshot
from apps.protection.services.snapshot_delete import (
    create_snapshot_delete_task,
    fail_snapshot_delete_task,
    reconcile_snapshot_delete_tasks,
)
from apps.source.models import BackupSourceRepositoryPurgePending, SourceResource
from apps.source.services.internal.backup_source_delete import (
    BackupSourceDeleteFailed,
    _create_source_unregister_task,
    _enqueue_repository_purge_pending,
    _merge_unregister_checkpoint,
    _prepare_delete_batch,
    _repository_purge_idempotency_key,
    _resolve_context,
    _snapshot_delete_for_unregister,
    _snapshot_delete_owned_by_unregister_attempt,
    delete_backup_sources,
    run_source_unregister_task,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from apps.task.models import Task
from apps.task.services.interface import start_task


MOUNTS_ROOT = agent_paths.agent_mounts_dir()


def custom_mount(leaf: str) -> str:
    return f"{MOUNTS_ROOT}/custom/{leaf}"


class BackupSourceDeleteSnapshotTaskTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="nas-delete-snap@test.local",
            email="nas-delete-snap@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="nas-delete-snap-org", name="NAS Delete Snap Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.proxy = Node.objects.create(
            organization=self.org,
            name="proxy-delete-snap",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        self.resource = SourceResource.objects.create(
            organization_id=self.org.id,
            name="nas-delete-snap",
            resource_type="nas",
            config={
                "protocol": "nfs",
                "server": "192.168.7.61",
                "export_path": "/data/nfs_backup",
                "path": custom_mount("nfs-delete-snap"),
            },
            bound_node=self.proxy,
            availability="online",
            mount_status="mounted",
            mount_point=custom_mount("nfs-delete-snap"),
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="s3-delete-snap-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="delete-snap-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "cn-test-1",
                "prefix": "kopia/delete",
                "access_key_id": "ak",
                "secret_access_key": "sk",
                "kopia_password": "123456",
                "use_tls": False,
            },
        )
        reserve_repository_location(self.repository)
        mark_repository_location_owned(self.repository)
        mark_repository_location_ownership_verified(self.repository)
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="NAS delete snap config",
            source_type="nas",
            source_ref_id=self.resource.id,
            repository_id=self.repository.id,
        )
        self.directory = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data",
            display_name="data",
        )
        self.backup_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="NAS delete snap backup",
        )
        self.snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="nas",
            source_ref_id=self.resource.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=self.backup_task.id,
            task_uuid=self.backup_task.task_uuid,
            idempotency_key="nas-delete-snap",
        )
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=self.directory.id,
            source_path="/data",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-delete-fail",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        self.client.force_authenticate(user=self.user)

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def _create_unregister_parent(self) -> Task:
        return _create_source_unregister_task(
            org=self.org,
            selectable_id=f"nas:{self.resource.id}",
            force=False,
        )

    def _create_agent_unregister_with_snapshot_delete(
        self,
        *,
        attempt_offset: int,
        force: bool = True,
    ) -> tuple[Node, Task, Task, NodeTask]:
        agent = Node.objects.create(
            organization=self.org,
            name=f"agent-with-snapshot-delete-{attempt_offset}",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        parent = _create_source_unregister_task(
            org=self.org,
            selectable_id=f"agent:{agent.id}",
            force=force,
        )
        child = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SNAPSHOT_DELETE,
            display_name="Snapshot delete during source unregister",
            status=Task.Status.RUNNING,
            request_payload={
                "source_unregister_task_id": parent.id,
                "source_unregister_attempt": parent.retry_count + attempt_offset,
            },
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=agent,
            kind="snapshot.delete",
            correlation_type="protection.snapshot_delete",
            correlation_id=str(child.task_uuid),
            parent_task=child,
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        return agent, parent, child, node_task

    def test_checkpoint_uses_latest_snapshot_child_state(self):
        previous = {
            "result": "waiting",
            "cleanup_complete": True,
            "snapshot_cleanup_tasks": [
                {"task_id": 17, "task_uuid": "child-17", "status": Task.Status.PENDING}
            ],
            "sources": [],
        }
        current = {
            "result": "success",
            "cleanup_complete": True,
            "snapshot_cleanup_tasks": [
                {"task_id": 17, "task_uuid": "child-17", "status": Task.Status.SUCCESS}
            ],
            "sources": [],
        }

        merged = _merge_unregister_checkpoint(previous, current)

        self.assertEqual(len(merged["snapshot_cleanup_tasks"]), 1)
        self.assertEqual(
            merged["snapshot_cleanup_tasks"][0]["status"],
            Task.Status.SUCCESS,
        )

    def test_checkpoint_deduplicates_failure_after_source_identity_is_known(self):
        previous = {
            "result": "waiting",
            "cleanup_complete": False,
            "cleanup_failures": [
                {
                    "source_id": "",
                    "code": "agent_offline",
                    "detail": "Agent is offline.",
                },
            ],
            "sources": [
                {
                    "source_id": f"nas:{self.resource.id}",
                    "source_name": self.resource.name,
                    "cleanup_complete": False,
                    "cleanup_failures": [],
                }
            ],
        }
        current = {
            "result": "partial_success",
            "cleanup_complete": False,
            "cleanup_failures": [
                {
                    "source_id": f"nas:{self.resource.id}",
                    "code": "agent_offline",
                    "detail": "Agent  is offline.",
                },
            ],
            "sources": previous["sources"],
        }

        merged = _merge_unregister_checkpoint(previous, current)

        self.assertEqual(len(merged["cleanup_failures"]), 1)
        self.assertEqual(
            merged["cleanup_failures"][0]["source_id"],
            f"nas:{self.resource.id}",
        )

    def test_snapshot_terminal_failure_queues_current_unregister_attempt(self):
        parent = self._create_unregister_parent()
        child = create_snapshot_delete_task(
            source_snapshot=self.snapshot,
            source_unregister_task=parent,
        )

        with (
            patch(
                "apps.source.tasks.source_unregister.queue_source_unregister_task"
            ) as queue_parent,
            self.captureOnCommitCallbacks(execute=True),
        ):
            fail_snapshot_delete_task(
                task=child,
                source_snapshot=self.snapshot,
                error_code="SNAPSHOT_DELETE_FAILED",
                error_message="physical cleanup failed",
            )

        queue_parent.assert_called_once_with(
            task_id=parent.id,
            countdown_seconds=1,
        )

    def test_unregister_snapshot_failure_is_not_retried_by_generic_reconciler(self):
        parent = self._create_unregister_parent()
        child = create_snapshot_delete_task(
            source_snapshot=self.snapshot,
            source_unregister_task=parent,
        )
        now = timezone.now()
        Task.objects.filter(pk=child.pk).update(
            status=Task.Status.FAILED,
            finished_at=now,
            error_code="SNAPSHOT_DELETE_FAILED",
            error_message="physical cleanup failed",
        )
        type(self.snapshot).objects.filter(pk=self.snapshot.pk).update(
            status=self.snapshot.Status.DELETE_FAILED,
        )

        with patch(
            "apps.protection.services.snapshot_delete.queue_snapshot_delete_task"
        ) as queue_child:
            result = reconcile_snapshot_delete_tasks(
                now=now + timezone.timedelta(hours=3),
            )

        child.refresh_from_db()
        self.assertEqual(result["retried_failed"], 0)
        self.assertEqual(child.status, Task.Status.FAILED)
        queue_child.assert_not_called()

    def test_new_unregister_attempt_explicitly_retries_failed_snapshot_child(self):
        parent = self._create_unregister_parent()
        child = create_snapshot_delete_task(
            source_snapshot=self.snapshot,
            source_unregister_task=parent,
        )
        Task.objects.filter(pk=child.pk).update(
            status=Task.Status.FAILED,
            finished_at=timezone.now(),
            error_code="SNAPSHOT_DELETE_FAILED",
            error_message="physical cleanup failed",
        )
        type(self.snapshot).objects.filter(pk=self.snapshot.pk).update(
            status=self.snapshot.Status.DELETE_FAILED,
        )
        parent.retry_count = 1
        parent.save(update_fields=["retry_count", "updated_at"])

        with patch(
            "apps.protection.services.snapshot_delete.queue_snapshot_delete_task"
        ):
            state, error, payload = _snapshot_delete_for_unregister(
                source_snapshot=self.snapshot,
                unregister_task=parent,
            )

        child.refresh_from_db()
        self.assertIsNone(state)
        self.assertIsNone(error)
        self.assertEqual(payload["task_id"], child.id)
        self.assertEqual(child.status, Task.Status.PENDING)
        self.assertEqual(
            child.request_payload["source_unregister_attempt"],
            1,
        )

    def test_unregister_preflight_ignores_snapshot_delete_owned_by_current_attempt(
        self,
    ):
        agent, parent, _child, _node_task = (
            self._create_agent_unregister_with_snapshot_delete(
                attempt_offset=0,
            )
        )

        prepared = _prepare_delete_batch(
            org=self.org,
            ids=[f"agent:{agent.id}"],
            force=True,
            executing_task_uuid=str(parent.task_uuid),
        )

        self.assertEqual([item[0].agent_node.id for item in prepared], [agent.id])

    def test_unregister_preflight_keeps_snapshot_delete_from_other_attempt_blocking(
        self,
    ):
        agent, parent, _child, _node_task = (
            self._create_agent_unregister_with_snapshot_delete(
                attempt_offset=1,
            )
        )

        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            _prepare_delete_batch(
                org=self.org,
                ids=[f"agent:{agent.id}"],
                force=True,
                executing_task_uuid=str(parent.task_uuid),
            )

        self.assertEqual(raised.exception.reasons[0].code, "node_workload_active")

    def test_strict_running_unregister_resumes_around_owned_snapshot_children(self):
        agent, parent, running_child, running_node_task = (
            self._create_agent_unregister_with_snapshot_delete(
                attempt_offset=0,
                force=False,
            )
        )
        completed_child = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SNAPSHOT_DELETE,
            display_name="Completed owned snapshot delete",
            status=Task.Status.SUCCESS,
            request_payload={
                "source_unregister_task_id": parent.id,
                "source_unregister_attempt": parent.retry_count,
            },
        )
        NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=agent,
            kind="snapshot.delete",
            correlation_type="protection.snapshot_delete",
            correlation_id=str(completed_child.task_uuid),
            parent_task=completed_child,
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        parent = start_task(
            task_uuid=parent.task_uuid,
            organization_id=self.org.id,
        )
        parent.result_payload = {
            "result": "waiting",
            "snapshot_cleanup_tasks": [],
        }
        parent.save(update_fields=["result_payload", "updated_at"])

        with (
            patch(
                "apps.source.services.internal.backup_source_delete.agent_connection_status",
                return_value="online",
            ),
            patch(
                "apps.source.services.internal.backup_source_delete._execute_source_unregister_work",
                return_value={"result": "waiting", "status": Task.Status.RUNNING},
            ) as execute_unregister,
        ):
            waiting = run_source_unregister_task(
                organization_id=self.org.id,
                task_uuid=str(parent.task_uuid),
            )

            parent.refresh_from_db()
            self.assertEqual(waiting["result"], "waiting")
            self.assertEqual(parent.status, Task.Status.RUNNING)
            self.assertEqual(parent.result_payload["result"], "waiting")
            self.assertFalse(execute_unregister.call_args.kwargs["force"])

            Task.objects.filter(id=running_child.id).update(
                status=Task.Status.SUCCESS,
                finished_at=timezone.now(),
            )
            NodeTask.objects.filter(id=running_node_task.id).update(
                status=NodeTask.Status.SUCCESS,
            )
            execute_unregister.return_value = {
                "result": "continued",
                "status": Task.Status.RUNNING,
            }

            continued = run_source_unregister_task(
                organization_id=self.org.id,
                task_uuid=str(parent.task_uuid),
            )

        self.assertEqual(continued["result"], "continued")
        self.assertEqual(execute_unregister.call_count, 2)

    def test_unregister_snapshot_ownership_requires_both_exact_markers(self):
        parent = self._create_unregister_parent()
        child = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SNAPSHOT_DELETE,
            display_name="Snapshot delete ownership check",
            status=Task.Status.RUNNING,
        )
        incomplete_or_stale_payloads = [
            {},
            {"source_unregister_task_id": parent.id},
            {"source_unregister_attempt": parent.retry_count},
            {
                "source_unregister_task_id": parent.id + 1,
                "source_unregister_attempt": parent.retry_count,
            },
            {
                "source_unregister_task_id": parent.id,
                "source_unregister_attempt": parent.retry_count + 1,
            },
        ]

        for payload in incomplete_or_stale_payloads:
            child.request_payload = payload
            self.assertFalse(
                _snapshot_delete_owned_by_unregister_attempt(
                    product_task=child,
                    unregister_task=parent,
                )
            )

        child.request_payload = {
            "source_unregister_task_id": parent.id,
            "source_unregister_attempt": parent.retry_count,
        }
        self.assertTrue(
            _snapshot_delete_owned_by_unregister_attempt(
                product_task=child,
                unregister_task=parent,
            )
        )

    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        SOURCE_UNREGISTER_EAGER=True,
    )
    @patch("apps.protection.services.snapshot_delete.run_agent_task_async")
    def test_bulk_delete_keeps_failed_snapshot_remove_task(self, mock_run_agent_task_sync):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(id="node-delete-fail", status="failed", last_error="kopia delete failed"),
            result={
                "deleted_count": 0,
                "failed_count": 1,
                "results": [
                    {
                        "kopia_snapshot_id": "kopia-delete-fail",
                        "status": "failed",
                        "error_message": "snapshot not found",
                    }
                ],
            },
            ok=False,
            timed_out=False,
        )

        nas_key = f"nas:{self.resource.id}"
        response = self.client.post(
            "/api/v1/source/backup-selectable/bulk-delete/",
            {"ids": [nas_key], "force": False, "confirmation": "DEREGISTER"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.resource.refresh_from_db()
        self.assertFalse(self.resource.is_deleted)
        failed_tasks = Task.objects.filter(
            organization_id=self.org.id,
            task_type=Task.Type.SNAPSHOT_DELETE,
            status=Task.Status.FAILED,
        )
        self.assertEqual(failed_tasks.count(), 1)
        self.assertIn("kopia delete failed", failed_tasks.first().error_message or "")

    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        SOURCE_UNREGISTER_EAGER=True,
    )
    @patch(
        "apps.source.services.internal.backup_source_delete.agent_connection_status",
        return_value="online",
    )
    @patch("apps.protection.services.snapshot_delete.run_agent_task_async")
    def test_strict_online_agent_keeps_source_when_snapshot_cleanup_fails(
        self,
        mock_run_agent_task_sync,
        _agent_status,
    ):
        agent = Node.objects.create(
            organization=self.org,
            name="agent-delete-snap",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        self.config.source_type = "agent"
        self.config.source_ref_id = agent.id
        self.config.save(update_fields=["source_type", "source_ref_id"])
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="node-delete-fail",
                status="failed",
                last_error="kopia delete failed",
            ),
            result={"deleted_count": 0, "failed_count": 1, "results": []},
            ok=False,
            timed_out=False,
        )

        response = self.client.post(
            "/api/v1/source/backup-selectable/bulk-delete/",
            {
                "ids": [f"agent:{agent.id}"],
                "force": False,
                "confirmation": "DEREGISTER",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        agent.refresh_from_db()
        self.assertFalse(agent.is_deleted)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())
        self.assertFalse(
            BackupSourceRepositoryPurgePending.objects.filter(
                organization_id=self.org.id,
                source_kind="agent",
                source_ref_id=agent.id,
            ).exists()
        )

    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource",
        return_value={"success": True},
    )
    @patch(
        "apps.source.services.internal.backup_source_delete._snapshot_delete_strict",
        side_effect=RuntimeError("snapshot cleanup transport failed"),
    )
    def test_force_snapshot_exception_records_per_source_residue_and_partial_audit(
        self,
        _snapshot_delete,
        _unmount_resource,
    ):
        result = delete_backup_sources(
            org=self.org,
            ids=[f"nas:{self.resource.id}"],
            force=True,
            user=self.user,
        )

        pending = BackupSourceRepositoryPurgePending.objects.get(
            organization_id=self.org.id,
            source_kind="nas",
            source_ref_id=self.resource.id,
        )
        source_result = result["sources"][0]
        self.assertEqual(result["result"], "partial_success")
        self.assertFalse(result["cleanup_complete"])
        self.assertFalse(source_result["cleanup_complete"])
        self.assertEqual(
            source_result["cleanup_failures"][0]["code"],
            "repository_cleanup_required",
        )
        self.assertEqual(
            source_result["retained_resources"],
            [f"repository_cleanup_record:{pending.id}"],
        )
        audit = AuditLog.objects.filter(
            organization_id=self.org.id,
            resource_type="backup_source",
        ).latest("id")
        self.assertEqual(audit.result, AuditResult.PARTIAL)

    @patch(
        "apps.source.services.internal.backup_source_delete._soft_delete_identity",
        side_effect=RuntimeError("database finalization failed"),
    )
    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource",
        return_value={"success": True},
    )
    @patch(
        "apps.source.services.internal.backup_source_delete._snapshot_delete_strict",
        side_effect=RuntimeError("snapshot cleanup transport failed"),
    )
    def test_repository_purge_pending_is_idempotent_across_redelivery(
        self,
        _snapshot_delete,
        _unmount_resource,
        _soft_delete,
    ):
        legacy_pending = BackupSourceRepositoryPurgePending.objects.create(
            organization_id=self.org.id,
            source_kind="nas",
            source_ref_id=self.resource.id,
            repository_id=self.repository.id,
            payload={
                "source_snapshot_ids": [self.snapshot.id],
                "kopia_snapshot_ids": ["kopia-delete-fail"],
                "error": "legacy cleanup failure",
            },
            last_error="legacy cleanup failure",
        )

        with self.assertRaises(RuntimeError):
            delete_backup_sources(
                org=self.org,
                ids=[f"nas:{self.resource.id}"],
                force=True,
                user=self.user,
            )
        unregister_task = Task.objects.get(task_type=Task.Type.SOURCE_UNREGISTER)
        first_pending = BackupSourceRepositoryPurgePending.objects.get(
            organization_id=self.org.id,
            source_ref_id=self.resource.id,
        )
        self.assertEqual(first_pending.id, legacy_pending.id)

        with self.assertRaises(RuntimeError):
            run_source_unregister_task(
                organization_id=self.org.id,
                task_uuid=str(unregister_task.task_uuid),
            )

        pending_rows = BackupSourceRepositoryPurgePending.objects.filter(
            organization_id=self.org.id,
            source_ref_id=self.resource.id,
        )
        self.assertEqual(pending_rows.count(), 1)
        self.assertEqual(pending_rows.get().id, first_pending.id)
        self.assertTrue(pending_rows.get().idempotency_key)

    def test_repository_purge_pending_merges_keyed_and_legacy_rows(self):
        key = _repository_purge_idempotency_key(
            organization_id=self.org.id,
            source_kind="nas",
            source_ref_id=self.resource.id,
            repository_id=self.repository.id,
            snapshot_ids=[self.snapshot.id],
        )
        common = {
            "organization_id": self.org.id,
            "source_kind": "nas",
            "source_ref_id": self.resource.id,
            "repository_id": self.repository.id,
        }
        canonical = BackupSourceRepositoryPurgePending.objects.create(
            **common,
            idempotency_key=key,
            payload={
                "source_snapshot_ids": [self.snapshot.id],
                "kopia_snapshot_ids": ["canonical-snapshot"],
            },
            retry_count=1,
        )
        BackupSourceRepositoryPurgePending.objects.create(
            **common,
            payload={
                "source_snapshot_ids": [self.snapshot.id],
                "kopia_snapshot_ids": ["legacy-snapshot"],
            },
            retry_count=3,
        )
        ctx = _resolve_context(
            organization_id=self.org.id,
            selectable_id=f"nas:{self.resource.id}",
        )
        self.assertIsNotNone(ctx)

        pending_id = _enqueue_repository_purge_pending(
            organization_id=self.org.id,
            ctx=ctx,
            repository_id=self.repository.id,
            snapshot_ids=[self.snapshot.id],
            kopia_snapshot_ids=["current-snapshot"],
            error="current failure",
        )

        rows = BackupSourceRepositoryPurgePending.objects.filter(**common)
        self.assertEqual(pending_id, canonical.id)
        self.assertEqual(rows.count(), 1)
        pending = rows.get()
        self.assertEqual(pending.retry_count, 3)
        self.assertEqual(
            pending.payload["kopia_snapshot_ids"],
            ["canonical-snapshot", "legacy-snapshot", "current-snapshot"],
        )
        self.assertEqual(pending.last_error, "current failure")

    def test_repository_purge_pending_is_idempotent_when_repository_is_missing(self):
        ctx = _resolve_context(
            organization_id=self.org.id,
            selectable_id=f"nas:{self.resource.id}",
        )
        self.assertIsNotNone(ctx)
        missing_repository_id = self.repository.id + 1000

        first_id = _enqueue_repository_purge_pending(
            organization_id=self.org.id,
            ctx=ctx,
            repository_id=missing_repository_id,
            snapshot_ids=[self.snapshot.id],
            kopia_snapshot_ids=["missing-repository-snapshot"],
            error="repository record missing",
        )
        second_id = _enqueue_repository_purge_pending(
            organization_id=self.org.id,
            ctx=ctx,
            repository_id=missing_repository_id,
            snapshot_ids=[self.snapshot.id],
            kopia_snapshot_ids=["missing-repository-snapshot"],
            error="repository record still missing",
        )

        self.assertEqual(second_id, first_id)
        self.assertEqual(
            BackupSourceRepositoryPurgePending.objects.filter(
                organization_id=self.org.id,
                repository_id=missing_repository_id,
            ).count(),
            1,
        )

    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource",
        return_value={"success": True},
    )
    def test_final_audit_failure_rolls_back_strict_control_plane_delete(
        self,
        _unmount_resource,
    ):
        self.snapshot.status = self.snapshot.Status.DELETED
        self.snapshot.save(update_fields=["status", "updated_at"])

        def fail_final_audit(**kwargs):
            if kwargs.get("resource_type") == "backup_source":
                raise RuntimeError("audit database unavailable")
            return persist_audit_log(**kwargs)

        with (
            patch(
                "apps.source.services.internal.backup_source_delete.write_audit_log",
                side_effect=fail_final_audit,
            ),
            self.assertRaises(RuntimeError),
        ):
            delete_backup_sources(
                org=self.org,
                ids=[f"nas:{self.resource.id}"],
                force=False,
                user=self.user,
            )

        unregister_task = Task.objects.get(task_type=Task.Type.SOURCE_UNREGISTER)
        self.resource.refresh_from_db()
        self.assertEqual(unregister_task.status, Task.Status.RUNNING)
        self.assertFalse(self.resource.is_deleted)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())

    def test_pending_idempotency_migration_backfills_and_deduplicates(self):
        common = {
            "organization_id": self.org.id,
            "source_kind": "nas",
            "source_ref_id": self.resource.id,
            "repository_id": self.repository.id,
        }
        first = BackupSourceRepositoryPurgePending.objects.create(
            **common,
            payload={
                "source_snapshot_ids": [self.snapshot.id],
                "kopia_snapshot_ids": ["snapshot-a"],
            },
            retry_count=1,
            last_error="first failure",
        )
        BackupSourceRepositoryPurgePending.objects.create(
            **common,
            payload={
                "source_snapshot_ids": [self.snapshot.id],
                "kopia_snapshot_ids": ["snapshot-b"],
            },
            retry_count=2,
            last_error="latest failure",
        )
        malformed = BackupSourceRepositoryPurgePending.objects.create(
            organization_id=self.org.id,
            source_kind="nas",
            source_ref_id=self.resource.id + 1000,
            repository_id=self.repository.id,
            payload={"source_snapshot_ids": ["not-an-id"]},
            last_error="malformed legacy payload",
        )
        migration = import_module(
            "apps.source.migrations.0009_repository_purge_pending_idempotency_key"
        )

        migration.backfill_pending_keys(django_apps, None)

        pending = BackupSourceRepositoryPurgePending.objects.get(**common)
        self.assertEqual(pending.id, first.id)
        self.assertTrue(pending.idempotency_key)
        self.assertEqual(pending.retry_count, 2)
        self.assertEqual(pending.last_error, "latest failure")
        self.assertEqual(
            pending.payload["kopia_snapshot_ids"],
            ["snapshot-a", "snapshot-b"],
        )
        malformed.refresh_from_db()
        self.assertIsNone(malformed.idempotency_key)

    @patch(
        "apps.source.services.internal.backup_source_delete._snapshot_delete_strict",
        side_effect=RuntimeError("snapshot cleanup transport failed"),
    )
    def test_strict_snapshot_exception_fails_task_and_keeps_source(
        self,
        _snapshot_delete,
    ):
        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"nas:{self.resource.id}"],
                force=False,
                user=self.user,
            )

        unregister_task = Task.objects.get(task_type=Task.Type.SOURCE_UNREGISTER)
        self.resource.refresh_from_db()
        self.assertEqual(
            raised.exception.reasons[0].code,
            "repository_snapshot_delete_failed",
        )
        self.assertEqual(unregister_task.status, Task.Status.FAILED)
        self.assertFalse(self.resource.is_deleted)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())

    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource",
        side_effect=RuntimeError("proxy dispatch failed"),
    )
    def test_force_nas_unmount_exception_records_residue_and_continues(
        self,
        _unmount_resource,
    ):
        self.snapshot.status = self.snapshot.Status.DELETED
        self.snapshot.save(update_fields=["status", "updated_at"])

        result = delete_backup_sources(
            org=self.org,
            ids=[f"nas:{self.resource.id}"],
            force=True,
            user=self.user,
        )

        self.resource.refresh_from_db()
        self.assertEqual(result["result"], "partial_success")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(
            result["cleanup_failures"][0]["code"],
            "nas_umount_failed",
        )
        self.assertEqual(
            result["retained_resources"],
            [f"source_nas_mount:{self.resource.id}"],
        )
        self.assertTrue(self.resource.is_deleted)

    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource",
        return_value={
            "success": True,
            "cleanup_complete": False,
            "retained_resources": [
                "nas_mount_reference",
                "nas_mount_directory",
            ],
            "warnings": ["The NAS mount was lazily detached."],
        },
    )
    def test_force_nas_unmount_namespaces_agent_residue(self, _unmount_resource):
        self.snapshot.status = self.snapshot.Status.DELETED
        self.snapshot.save(update_fields=["status", "updated_at"])

        result = delete_backup_sources(
            org=self.org,
            ids=[f"nas:{self.resource.id}"],
            force=True,
            user=self.user,
        )

        self.assertEqual(result["result"], "partial_success")
        self.assertIn(
            f"source_nas_mount:{self.resource.id}",
            result["retained_resources"],
        )
        self.assertIn(
            f"source_nas_mount_directory:{self.resource.id}",
            result["retained_resources"],
        )

    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource",
        return_value={
            "success": True,
            "cleanup_complete": False,
            "retained_resources": ["nas_mount_reference"],
            "warnings": ["The NAS mount still has local references."],
        },
    )
    def test_strict_nas_unmount_rejects_success_with_residue(
        self,
        _unmount_resource,
    ):
        self.snapshot.status = self.snapshot.Status.DELETED
        self.snapshot.save(update_fields=["status", "updated_at"])

        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"nas:{self.resource.id}"],
                force=False,
                user=self.user,
            )

        self.resource.refresh_from_db()
        self.assertEqual(raised.exception.reasons[0].code, "nas_umount_retained")
        self.assertFalse(self.resource.is_deleted)

    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource"
    )
    def test_force_nas_unmount_merges_compensating_task_residue(
        self,
        unmount_resource,
    ):
        self.snapshot.status = self.snapshot.Status.DELETED
        self.snapshot.save(update_fields=["status", "updated_at"])

        def complete_with_compensation_residue(**_kwargs):
            NodeTask.objects.create(
                organization=self.org,
                requesting_organization_id=self.org.id,
                node=self.proxy,
                kind="nas.unmount",
                correlation_type="source.unmount",
                correlation_id=str(self.resource.id),
                status=NodeTask.Status.SUCCESS,
                result={
                    "cleanup_complete": False,
                    "retained_resources": ["nas_mount_reference"],
                    "warnings": ["The NAS mount was lazily detached."],
                },
                watchdog_deadline_at=timezone.now(),
            )
            return {"success": True, "cleanup_complete": True}

        unmount_resource.side_effect = complete_with_compensation_residue

        result = delete_backup_sources(
            org=self.org,
            ids=[f"nas:{self.resource.id}"],
            force=True,
            user=self.user,
        )

        self.assertEqual(result["result"], "partial_success")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(
            result["retained_resources"],
            [f"source_nas_mount:{self.resource.id}"],
        )

    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource",
        return_value={
            "success": False,
            "message": "cleanup mount directory: directory not empty",
        },
    )
    def test_force_nas_directory_failure_uses_stable_residue_id(
        self,
        _unmount_resource,
    ):
        self.snapshot.status = self.snapshot.Status.DELETED
        self.snapshot.save(update_fields=["status", "updated_at"])

        result = delete_backup_sources(
            org=self.org,
            ids=[f"nas:{self.resource.id}"],
            force=True,
            user=self.user,
        )

        self.assertEqual(result["result"], "partial_success")
        self.assertEqual(
            result["retained_resources"],
            [f"source_nas_mount_directory:{self.resource.id}"],
        )

    @patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource",
        side_effect=RuntimeError("proxy dispatch failed"),
    )
    def test_strict_nas_unmount_exception_fails_task_and_keeps_source(
        self,
        _unmount_resource,
    ):
        self.snapshot.status = self.snapshot.Status.DELETED
        self.snapshot.save(update_fields=["status", "updated_at"])

        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(
                org=self.org,
                ids=[f"nas:{self.resource.id}"],
                force=False,
                user=self.user,
            )

        unregister_task = Task.objects.get(task_type=Task.Type.SOURCE_UNREGISTER)
        self.resource.refresh_from_db()
        self.assertEqual(raised.exception.reasons[0].code, "nas_umount_failed")
        self.assertEqual(unregister_task.status, Task.Status.FAILED)
        self.assertFalse(self.resource.is_deleted)
        self.assertTrue(BackupConfig.objects.filter(pk=self.config.id).exists())

    def test_delete_preflight_ignores_existing_snapshots_when_repo_online(self):
        nas_key = f"nas:{self.resource.id}"
        response = self.client.post(
            "/api/v1/source/backup-selectable/delete-preflight/",
            {"ids": [nas_key]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["strict_may_fail"])
        self.assertEqual(response.data["risks"], [])

    def test_delete_preflight_flags_offline_repository(self):
        self.repository.health = Repository.Health.OFFLINE
        self.repository.save(update_fields=["health"])
        nas_key = f"nas:{self.resource.id}"
        response = self.client.post(
            "/api/v1/source/backup-selectable/delete-preflight/",
            {"ids": [nas_key]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["strict_may_fail"])
        self.assertTrue(any(row["code"] == "repository_unreachable" for row in response.data["risks"]))
