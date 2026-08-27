from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.agent_paths import restore_repository_mount_point
from apps.node.services.capabilities import NAS_MOUNT_LIFECYCLE_CAPABILITY
from apps.restore import conf
from apps.restore.models import DirectNASMount, DirectNASMountLease, RestoreRecord
from apps.restore.services import direct_nas_mounts
from apps.storage.repositories.models import Repository
from apps.task.models import Task


class DirectNASMountLifecycleTests(TestCase):
    def setUp(self) -> None:
        self.organization = Organization.objects.create(
            key="direct-nas-mount-tests",
            name="Direct NAS mount tests",
        )
        self.gateway = Node.objects.create(
            organization=self.organization,
            name="Gateway 1",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": [NAS_MOUNT_LIFECYCLE_CAPABILITY]}},
        )
        self.other_gateway = Node.objects.create(
            organization=self.organization,
            name="Gateway 2",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={"inventory": {"capabilities": [NAS_MOUNT_LIFECYCLE_CAPABILITY]}},
        )
        self.repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="Direct NAS",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
        )
        self._sequence = 0

    def _record(
        self,
        *,
        node_id: int | None = None,
        status: str = Task.Status.RUNNING,
    ) -> RestoreRecord:
        self._sequence += 1
        resolved_node_id = node_id or self.gateway.id
        task = Task.objects.create(
            organization_id=self.organization.id,
            task_type=Task.Type.INSIGHT_WORKSPACE_RESTORE,
            display_name=f"Chat restore {self._sequence}",
            status=status,
        )
        return RestoreRecord.objects.create(
            organization_id=self.organization.id,
            requesting_organization_id=self.organization.id,
            target_execution_organization_id=self.organization.id,
            target_execution_node_id=resolved_node_id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key=f"chat-{self._sequence}",
            workspace_binding_id=1000 + self._sequence,
            restore_uid=f"restore-{self._sequence}",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=301,
            source_snapshot_id=401,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=resolved_node_id,
            target_path=f"/chat/{self._sequence}",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )

    @staticmethod
    def _payload(mount_point: str) -> dict:
        return {"nas": {"mount_point": mount_point}}

    def _acquire(
        self,
        record: RestoreRecord,
        *,
        repository: Repository | None = None,
        node_id: int | None = None,
        mount_point: str | None = None,
        access_mode: str = "fallback_node",
    ) -> DirectNASMountLease | None:
        return direct_nas_mounts.acquire_for_restore(
            record=record,
            repository=repository or self.repository,
            reader_node_id=node_id or record.target_execution_node_id,
            access_mode=access_mode,
            repository_payload=self._payload(
                mount_point
                or restore_repository_mount_point(
                    (repository or self.repository).id,
                    node_id=node_id or record.target_execution_node_id,
                )
            ),
        )

    def test_same_gateway_repository_and_mount_share_one_aggregate(self) -> None:
        first = self._acquire(self._record())
        second = self._acquire(self._record())

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.mount_id, second.mount_id)
        self.assertEqual(DirectNASMount.objects.count(), 1)
        self.assertEqual(DirectNASMountLease.objects.count(), 2)

    def test_mount_preserves_restore_requesting_organization(self) -> None:
        requesting_organization = Organization.objects.create(
            key="direct-nas-requesting-org",
            name="Direct NAS requesting org",
        )
        record = self._record()
        record.requesting_organization_id = requesting_organization.id
        record.save(update_fields=["requesting_organization_id", "updated_at"])

        lease = self._acquire(record)

        self.assertIsNotNone(lease)
        self.assertEqual(
            lease.mount.requesting_organization_id,
            requesting_organization.id,
        )

    def test_different_gateways_have_independent_mount_aggregates(self) -> None:
        first = self._acquire(
            self._record(node_id=self.gateway.id),
            mount_point=restore_repository_mount_point(
                self.repository.id, node_id=self.gateway.id
            ),
        )
        second = self._acquire(
            self._record(node_id=self.other_gateway.id),
            mount_point=restore_repository_mount_point(
                self.repository.id, node_id=self.other_gateway.id
            ),
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.mount_id, second.mount_id)
        self.assertEqual(DirectNASMount.objects.count(), 2)

    def test_user_data_restore_is_included_but_non_nas_and_proxy_are_excluded(
        self,
    ) -> None:
        user_record = self._record()
        user_record.purpose = RestoreRecord.Purpose.USER_DATA
        user_record.workspace_binding_id = None
        user_record.idempotency_key = ""
        user_record.save(
            update_fields=[
                "purpose",
                "workspace_binding_id",
                "idempotency_key",
                "updated_at",
            ]
        )
        self.assertIsNotNone(self._acquire(user_record))

        chat_record = self._record()
        s3 = Repository.objects.create(
            organization_id=self.organization.id,
            name="S3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
        )
        self.assertIsNone(self._acquire(chat_record, repository=s3))

        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = 999
        self.repository.save(
            update_fields=["bind_node_type", "bind_node_id", "updated_at"]
        )
        self.assertIsNone(
            self._acquire(
                self._record(),
                repository=self.repository,
                access_mode="bound_proxy",
            )
        )
        self.assertEqual(DirectNASMount.objects.count(), 1)

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_last_release_dispatches_one_shared_unmount(self, dispatch) -> None:
        dispatch.return_value = SimpleNamespace(task=SimpleNamespace(id=uuid.uuid4()))
        first_record = self._record()
        second_record = self._record()
        first = self._acquire(first_record)
        second = self._acquire(second_record)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

        direct_nas_mounts.release_for_record(record=first_record)
        self.assertIsNone(DirectNASMount.objects.get().cleanup_after)
        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 0)

        direct_nas_mounts.release_for_record(record=second_record)
        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 1)
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.kwargs["node_id"], self.gateway.id)
        self.assertEqual(dispatch.call_args.kwargs["kind"], "nas.unmount")
        self.assertEqual(
            dispatch.call_args.kwargs["payload"],
            {
                "mount_point": restore_repository_mount_point(
                    self.repository.id, node_id=self.gateway.id
                )
            },
        )
        self.assertEqual(
            DirectNASMountLease.objects.filter(
                status=DirectNASMountLease.Status.CLEANUP_PENDING
            ).count(),
            2,
        )

    @patch.object(conf, "DIRECT_NAS_USER_DATA_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_user_data_restore_release_dispatches_unmount(self, dispatch) -> None:
        dispatch.return_value = SimpleNamespace(task=SimpleNamespace(id=uuid.uuid4()))
        record = self._record()
        record.purpose = RestoreRecord.Purpose.USER_DATA
        record.workspace_binding_id = None
        record.idempotency_key = ""
        record.save(
            update_fields=[
                "purpose",
                "workspace_binding_id",
                "idempotency_key",
                "updated_at",
            ]
        )
        self.assertIsNotNone(self._acquire(record))

        direct_nas_mounts.release_for_record(record=record)

        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 1)
        self.assertEqual(dispatch.call_args.kwargs["kind"], "nas.unmount")
        self.assertEqual(
            dispatch.call_args.kwargs["payload"]["mount_point"],
            restore_repository_mount_point(self.repository.id, node_id=self.gateway.id),
        )

        cleanup_task_id = dispatch.return_value.task.id
        NodeTask.objects.create(
            id=cleanup_task_id,
            organization=self.organization,
            requesting_organization_id=self.organization.id,
            node=self.gateway,
            kind="nas.unmount",
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
        )
        self.assertEqual(
            direct_nas_mounts._reconcile_cleanup_tasks(limit=10),
            (1, 0),
        )
        self.assertFalse(DirectNASMount.objects.exists())
        self.assertFalse(DirectNASMountLease.objects.exists())

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_different_gateways_dispatch_independent_unmounts(self, dispatch) -> None:
        dispatch.side_effect = [
            SimpleNamespace(task=SimpleNamespace(id=uuid.uuid4())),
            SimpleNamespace(task=SimpleNamespace(id=uuid.uuid4())),
        ]
        first_record = self._record(node_id=self.gateway.id)
        second_record = self._record(node_id=self.other_gateway.id)
        self._acquire(
            first_record,
            mount_point=restore_repository_mount_point(
                self.repository.id, node_id=self.gateway.id
            ),
        )
        self._acquire(
            second_record,
            mount_point=restore_repository_mount_point(
                self.repository.id, node_id=self.other_gateway.id
            ),
        )
        direct_nas_mounts.release_for_record(record=first_record)
        direct_nas_mounts.release_for_record(record=second_record)

        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 2)
        self.assertEqual(
            {call.kwargs["node_id"] for call in dispatch.call_args_list},
            {self.gateway.id, self.other_gateway.id},
        )

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_new_lease_preserves_single_in_flight_cleanup(self, dispatch) -> None:
        cleanup_task_id = uuid.uuid4()
        dispatch.return_value = SimpleNamespace(
            task=SimpleNamespace(id=cleanup_task_id)
        )
        first_record = self._record()
        self._acquire(first_record)
        direct_nas_mounts.release_for_record(record=first_record)
        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 1)

        second_record = self._record()
        second = self._acquire(second_record)
        self.assertIsNotNone(second)
        mount = second.mount
        mount.refresh_from_db()
        self.assertEqual(mount.cleanup_node_task_id, cleanup_task_id)
        self.assertIsNone(mount.cleanup_after)
        self.assertEqual(second.status, DirectNASMountLease.Status.ACTIVE)
        self.assertEqual(
            DirectNASMountLease.objects.filter(
                mount=mount,
                cleanup_node_task_id=cleanup_task_id,
                status=DirectNASMountLease.Status.CLEANUP_PENDING,
            ).count(),
            1,
        )

        dispatch.assert_called_once()
        NodeTask.objects.create(
            id=cleanup_task_id,
            organization=self.organization,
            requesting_organization_id=self.organization.id,
            node=self.gateway,
            kind="nas.unmount",
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
        )

        completed, retried = direct_nas_mounts._reconcile_cleanup_tasks(limit=10)

        self.assertEqual((completed, retried), (1, 0))
        mount.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(mount.cleanup_node_task_id)
        self.assertEqual(second.status, DirectNASMountLease.Status.ACTIVE)
        self.assertEqual(DirectNASMountLease.objects.filter(mount=mount).count(), 1)

        direct_nas_mounts.release_for_record(record=second_record)
        mount.refresh_from_db()
        self.assertIsNotNone(mount.cleanup_after)

    def test_new_lease_cancels_cleanup_during_grace_period(self) -> None:
        first_record = self._record()
        first = self._acquire(first_record)
        self.assertIsNotNone(first)
        direct_nas_mounts.release_for_record(record=first_record)
        first.mount.refresh_from_db()
        self.assertIsNotNone(first.mount.cleanup_after)

        second_record = self._record()
        second = self._acquire(second_record)

        self.assertIsNotNone(second)
        second.mount.refresh_from_db()
        self.assertEqual(second.mount_id, first.mount_id)
        self.assertIsNone(second.mount.cleanup_after)
        self.assertEqual(second.mount.last_error, "")

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    def test_reconciler_releases_terminal_orphan(self) -> None:
        record = self._record(status=Task.Status.FAILED)
        lease = self._acquire(record)
        self.assertIsNotNone(lease)

        released = direct_nas_mounts._release_terminal_record_leases(limit=10)

        self.assertEqual(released, 1)
        lease.refresh_from_db()
        lease.mount.refresh_from_db()
        self.assertEqual(lease.status, DirectNASMountLease.Status.RELEASED)
        self.assertLessEqual(lease.mount.cleanup_after, timezone.now())

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    def test_reconciler_releases_lease_when_product_task_is_missing(self) -> None:
        record = self._record()
        lease = self._acquire(record)
        self.assertIsNotNone(lease)
        Task.objects.filter(pk=record.task_id).delete()

        released = direct_nas_mounts._release_terminal_record_leases(limit=10)

        self.assertEqual(released, 1)
        lease.refresh_from_db()
        lease.mount.refresh_from_db()
        self.assertEqual(lease.status, DirectNASMountLease.Status.RELEASED)
        self.assertLessEqual(lease.mount.cleanup_after, timezone.now())

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_restore_record_delete_keeps_mount_cleanup_authoritative(
        self, dispatch
    ) -> None:
        cleanup_task_id = uuid.uuid4()
        dispatch.return_value = SimpleNamespace(
            task=SimpleNamespace(id=cleanup_task_id)
        )
        record = self._record()
        lease = self._acquire(record)
        self.assertIsNotNone(lease)
        mount_id = lease.mount_id
        direct_nas_mounts.release_for_record(record=record)
        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 1)

        record.delete()
        self.assertFalse(DirectNASMountLease.objects.filter(mount_id=mount_id).exists())
        NodeTask.objects.create(
            id=cleanup_task_id,
            organization=self.organization,
            requesting_organization_id=self.organization.id,
            node=self.gateway,
            kind="nas.unmount",
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
        )

        self.assertEqual(
            direct_nas_mounts._reconcile_cleanup_tasks(limit=10),
            (1, 0),
        )
        self.assertFalse(DirectNASMount.objects.filter(pk=mount_id).exists())

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_orphan_mount_without_leases_is_scheduled_and_unmounted(
        self, dispatch
    ) -> None:
        dispatch.return_value = SimpleNamespace(task=SimpleNamespace(id=uuid.uuid4()))
        record = self._record()
        lease = self._acquire(record)
        self.assertIsNotNone(lease)
        mount_id = lease.mount_id
        record.delete()

        self.assertEqual(direct_nas_mounts._schedule_unreferenced_mounts(limit=10), 1)
        mount = DirectNASMount.objects.get(pk=mount_id)
        self.assertLessEqual(mount.cleanup_after, timezone.now())
        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 1)
        dispatch.assert_called_once_with(
            organization_id=self.organization.id,
            node_id=self.gateway.id,
            kind="nas.unmount",
            payload={
                "mount_point": restore_repository_mount_point(
                    self.repository.id, node_id=self.gateway.id
                )
            },
            correlation_type="restore.direct_nas_mount_cleanup",
            correlation_id=f"mount:{mount_id}",
            requesting_organization_id=self.organization.id,
        )

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS", 30)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_old_agent_defers_automatic_unmount_until_upgrade(self, dispatch) -> None:
        self.gateway.metadata = {"inventory": {"capabilities": []}}
        self.gateway.save(update_fields=["metadata", "updated_at"])
        record = self._record()
        lease = self._acquire(record)
        self.assertIsNotNone(lease)
        direct_nas_mounts.release_for_record(record=record)
        before = timezone.now()

        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 0)

        dispatch.assert_not_called()
        lease.mount.refresh_from_db()
        self.assertGreater(lease.mount.cleanup_after, before)
        self.assertIn("Upgrade", lease.mount.last_error)

        self.gateway.metadata = {
            "inventory": {"capabilities": [NAS_MOUNT_LIFECYCLE_CAPABILITY]}
        }
        self.gateway.save(update_fields=["metadata", "updated_at"])
        lease.mount.cleanup_after = timezone.now()
        lease.mount.save(update_fields=["cleanup_after", "updated_at"])
        dispatch.return_value = SimpleNamespace(task=SimpleNamespace(id=uuid.uuid4()))

        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 1)
        dispatch.assert_called_once()

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS", 30)
    def test_failed_cleanup_is_retried_after_backoff(self) -> None:
        record = self._record()
        lease = self._acquire(record)
        self.assertIsNotNone(lease)
        cleanup_task_id = uuid.uuid4()
        now = timezone.now()
        lease.status = DirectNASMountLease.Status.CLEANUP_PENDING
        lease.cleanup_node_task_id = cleanup_task_id
        lease.save(
            update_fields=[
                "status",
                "cleanup_node_task_id",
                "updated_at",
            ]
        )
        mount = lease.mount
        mount.cleanup_node_task_id = cleanup_task_id
        mount.cleanup_after = None
        mount.save(
            update_fields=["cleanup_node_task_id", "cleanup_after", "updated_at"]
        )
        NodeTask.objects.create(
            id=cleanup_task_id,
            organization=self.organization,
            requesting_organization_id=self.organization.id,
            node=self.gateway,
            kind="nas.unmount",
            status=NodeTask.Status.FAILED,
            watchdog_deadline_at=now,
            last_error="mount is busy",
        )

        completed, retried = direct_nas_mounts._reconcile_cleanup_tasks(limit=10)

        self.assertEqual((completed, retried), (0, 1))
        lease.refresh_from_db()
        mount.refresh_from_db()
        self.assertEqual(lease.status, DirectNASMountLease.Status.RELEASED)
        self.assertIsNone(mount.cleanup_node_task_id)
        self.assertGreater(mount.cleanup_after, now)
        self.assertEqual(mount.last_error, "mount is busy")

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS", 30)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_cleanup_dispatch_failure_uses_backoff(self, dispatch) -> None:
        dispatch.side_effect = RuntimeError("gateway is offline")
        record = self._record()
        lease = self._acquire(record)
        self.assertIsNotNone(lease)
        direct_nas_mounts.release_for_record(record=record)
        before = timezone.now()

        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 0)

        mount = lease.mount
        mount.refresh_from_db()
        self.assertIsNone(mount.cleanup_node_task_id)
        self.assertGreater(mount.cleanup_after, before)
        self.assertEqual(mount.last_error, "gateway is offline")

    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0)
    @patch.object(conf, "DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS", 30)
    @patch("apps.restore.services.direct_nas_mounts.run_agent_task_async")
    def test_missing_cleanup_task_is_recovered(self, dispatch) -> None:
        cleanup_task_id = uuid.uuid4()
        dispatch.return_value = SimpleNamespace(
            task=SimpleNamespace(id=cleanup_task_id)
        )
        record = self._record()
        lease = self._acquire(record)
        self.assertIsNotNone(lease)
        direct_nas_mounts.release_for_record(record=record)
        self.assertEqual(direct_nas_mounts._dispatch_due_cleanups(limit=10), 1)

        completed, retried = direct_nas_mounts._reconcile_cleanup_tasks(limit=10)

        self.assertEqual((completed, retried), (0, 1))
        lease.refresh_from_db()
        lease.mount.refresh_from_db()
        self.assertEqual(lease.status, DirectNASMountLease.Status.RELEASED)
        self.assertIsNone(lease.mount.cleanup_node_task_id)
        self.assertGreater(lease.mount.cleanup_after, timezone.now())
