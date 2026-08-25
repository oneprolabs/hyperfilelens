from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.source.models import SourceResource
from apps.source.services.internal.availability import (
    confirmed_agent_failure,
    project_node_availability,
)
from apps.source.services.internal.connection import best_effort_unmount_on_proxy
from apps.source.services.interface import (
    bind_node,
    mount_resource,
    test_resource_connection,
    update_source_resource,
)
from apps.source.tasks.connection_probe import (
    SOURCE_CONNECTION_PROBE_CORRELATION_TYPE,
    project_source_connection_probe,
    queue_source_availability_probes_for_proxy,
    reconcile_source_availability,
    reconcile_stale_source_connection_probes,
    run_source_resource_capacity_probe,
)
from apps.task.models import Task


class SourceConnectionProbeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="source-connection-probe-org",
            name="Source Connection Probe Org",
        )
        self.proxy = Node.objects.create(
            organization=self.org,
            name="source-connection-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        self.probe_token = uuid4()
        self.resource = SourceResource.objects.create(
            organization=self.org,
            name="source-connection-nas",
            resource_type="nas",
            config={
                "protocol": "nfs",
                "server": "192.0.2.20",
                "export_path": "/source",
            },
            bound_node=self.proxy,
            connection_test_status="pending",
            connection_probe_token=self.probe_token,
        )

    def _probe_node_task(self) -> NodeTask:
        return NodeTask.objects.create(
            organization=self.org,
            node=self.proxy,
            kind="nas.test",
            correlation_type=SOURCE_CONNECTION_PROBE_CORRELATION_TYPE,
            correlation_id=str(self.resource.id),
            payload={
                "source_resource_id": self.resource.id,
                "connection_probe_token": str(self.probe_token),
                "expected_bound_node_id": self.proxy.id,
                "nas": {
                    "resource_id": self.resource.id,
                    "protocol": "nfs",
                    "mount_point": self.resource.effective_mount_point(),
                },
            },
            status=NodeTask.Status.RUNNING,
            accepted_at=timezone.now(),
            watchdog_deadline_at=timezone.now() + timedelta(minutes=3),
        )

    @staticmethod
    def _dispatch_handle(task: NodeTask) -> SimpleNamespace:
        return SimpleNamespace(task=task, task_id=str(task.id))

    @mock.patch(
        "apps.source.tasks.connection_probe.dispatch_nas_agent_task_async"
    )
    def test_probe_applies_capacity_for_current_source_revision(self, dispatch):
        node_task = self._probe_node_task()
        dispatch.return_value = self._dispatch_handle(node_task)

        result = run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )
        self.assertEqual(result["status"], "dispatched")
        dispatched_payload = dispatch.call_args.kwargs["payload"]
        self.assertTrue(dispatched_payload["cleanup_after_test"])
        self.assertIn("/mounts/validations/", dispatched_payload["mount_point"])
        self.assertNotEqual(
            dispatched_payload["mount_point"],
            self.resource.effective_mount_point(),
        )
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.connection_test_status, "running")
        self.assertIsNone(self.resource.last_connection_test)

        node_task.status = NodeTask.Status.SUCCESS
        node_task.result = {
            "mount_point": "/opt/hyperfilelens-agent/mounts/custom/source",
            "mount_status": "unmounted",
            "cleanup_status": "success",
            "space_info": {
                "total_bytes": 1000,
                "used_bytes": 400,
                "free_bytes": 600,
            },
        }
        node_task.save(update_fields=["status", "result", "updated_at"])
        self.assertTrue(project_source_connection_probe(node_task=node_task))

        self.resource.refresh_from_db()
        self.assertEqual(self.resource.total_size, 1000)
        self.assertEqual(self.resource.used_size, 400)
        self.assertEqual(self.resource.free_size, 600)
        self.assertEqual(self.resource.mount_status, "unmounted")
        self.assertEqual(self.resource.mount_point, "")
        self.assertIsNotNone(self.resource.last_connection_test)
        self.assertEqual(self.resource.availability, "online")
        self.assertIsNotNone(self.resource.availability_updated_at)

    @mock.patch(
        "apps.source.tasks.connection_probe.dispatch_nas_agent_task_async",
        side_effect=RuntimeError("broker unavailable"),
    )
    def test_dispatch_failure_releases_probe_claim_for_retry(self, _dispatch):
        with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
            run_source_resource_capacity_probe(
                resource_id=self.resource.id,
                probe_token=str(self.probe_token),
                expected_bound_node_id=self.proxy.id,
            )

        self.resource.refresh_from_db()
        self.assertEqual(self.resource.connection_test_status, "pending")
        self.assertEqual(self.resource.connection_probe_token, self.probe_token)

    @mock.patch(
        "apps.source.tasks.connection_probe.best_effort_unmount_on_proxy"
    )
    @mock.patch(
        "apps.source.tasks.connection_probe.dispatch_nas_agent_task_async"
    )
    def test_probe_discards_result_after_source_edit(
        self,
        dispatch,
        best_effort_unmount,
    ):
        node_task = self._probe_node_task()
        dispatch.return_value = self._dispatch_handle(node_task)
        run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )
        dispatched_payload = dispatch.call_args.kwargs["payload"]
        node_task.payload = {
            **node_task.payload,
            **dispatched_payload,
            "nas": dispatched_payload,
        }
        node_task.save(update_fields=["payload", "updated_at"])
        update_source_resource(
            resource=SourceResource.objects.get(pk=self.resource.id),
            user=None,
            description="edited while probe was running",
        )
        node_task.status = NodeTask.Status.SUCCESS
        node_task.result = {"space_info": {"total_bytes": 1000}}
        node_task.save(update_fields=["status", "result", "updated_at"])

        self.resource.refresh_from_db()
        self.assertFalse(project_source_connection_probe(node_task=node_task))
        self.assertEqual(self.resource.total_size, 0)
        self.assertIsNone(self.resource.last_connection_test)
        self.assertEqual(self.resource.status, "active")
        best_effort_unmount.assert_called_once_with(
            resource=mock.ANY,
            node_id=self.proxy.id,
            force=True,
            wait=False,
            payload_override=mock.ANY,
        )
        cleanup_payload = best_effort_unmount.call_args.kwargs["payload_override"]
        self.assertEqual(
            cleanup_payload["mount_point"],
            dispatched_payload["mount_point"],
        )

    @mock.patch(
        "apps.source.tasks.connection_probe.best_effort_unmount_on_proxy"
    )
    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_probe_discards_result_after_source_delete(
        self,
        dispatch,
        best_effort_unmount,
    ):
        node_task = self._probe_node_task()
        dispatch.return_value = self._dispatch_handle(node_task)
        run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )
        SourceResource.objects.get(pk=self.resource.id).soft_delete()
        node_task.status = NodeTask.Status.SUCCESS
        node_task.save(update_fields=["status", "updated_at"])
        self.assertFalse(project_source_connection_probe(node_task=node_task))

        deleted = SourceResource.all_objects.get(pk=self.resource.id)
        self.assertTrue(deleted.is_deleted)
        self.assertEqual(deleted.total_size, 0)
        best_effort_unmount.assert_called_once_with(
            resource=mock.ANY,
            node_id=self.proxy.id,
            force=True,
            wait=False,
        )

    @mock.patch(
        "apps.source.tasks.connection_probe.best_effort_unmount_on_proxy"
    )
    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_probe_success_racing_removal_is_compensated(
        self,
        dispatch,
        best_effort_unmount,
    ):
        node_task = self._probe_node_task()
        dispatch.return_value = self._dispatch_handle(node_task)
        run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )
        SourceResource.objects.filter(pk=self.resource.id).update(status="removing")
        node_task.status = NodeTask.Status.SUCCESS
        node_task.save(update_fields=["status", "updated_at"])
        self.assertFalse(project_source_connection_probe(node_task=node_task))
        best_effort_unmount.assert_called_once_with(
            resource=mock.ANY,
            node_id=self.proxy.id,
            force=True,
            wait=False,
        )

    @mock.patch(
        "apps.source.tasks.connection_probe.best_effort_unmount_on_proxy"
    )
    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_probe_failure_racing_removal_is_compensated(
        self,
        dispatch,
        best_effort_unmount,
    ):
        node_task = self._probe_node_task()
        dispatch.return_value = self._dispatch_handle(node_task)
        run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )
        SourceResource.objects.filter(pk=self.resource.id).update(status="removing")
        node_task.status = NodeTask.Status.FAILED
        node_task.last_error = "Capacity read failed"
        node_task.save(update_fields=["status", "last_error", "updated_at"])
        self.assertFalse(project_source_connection_probe(node_task=node_task))
        best_effort_unmount.assert_called_once_with(
            resource=mock.ANY,
            node_id=self.proxy.id,
            force=True,
            wait=False,
        )

    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_probe_skips_remove_failed_source(self, dispatch):
        SourceResource.objects.filter(pk=self.resource.id).update(
            status="remove_failed",
        )

        result = run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )

        self.assertEqual(result, {"status": "skipped", "reason": "source_removing"})
        dispatch.assert_not_called()

    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_probe_claim_cannot_overwrite_removing_fence(self, dispatch):
        from apps.source.tasks import connection_probe

        original_target = connection_probe._probe_target

        def fence_after_read(**kwargs):
            resource, reason = original_target(**kwargs)
            SourceResource.objects.filter(pk=self.resource.id).update(
                status="removing",
            )
            return resource, reason

        with mock.patch(
            "apps.source.tasks.connection_probe._probe_target",
            side_effect=fence_after_read,
        ):
            result = run_source_resource_capacity_probe(
                resource_id=self.resource.id,
                probe_token=str(self.probe_token),
                expected_bound_node_id=self.proxy.id,
            )

        self.assertEqual(result, {"status": "skipped", "reason": "source_removing"})
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.status, "removing")
        dispatch.assert_not_called()

    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_probe_claim_reports_source_change_race(self, dispatch):
        from apps.source.tasks import connection_probe

        original_target = connection_probe._probe_target
        calls = 0

        def change_after_read(**kwargs):
            nonlocal calls
            calls += 1
            resource, reason = original_target(**kwargs)
            if calls == 1:
                SourceResource.objects.filter(pk=self.resource.id).update(
                    connection_probe_token=uuid4(),
                )
            return resource, reason

        with mock.patch(
            "apps.source.tasks.connection_probe._probe_target",
            side_effect=change_after_read,
        ):
            result = run_source_resource_capacity_probe(
                resource_id=self.resource.id,
                probe_token=str(self.probe_token),
                expected_bound_node_id=self.proxy.id,
            )

        self.assertEqual(result, {"status": "skipped", "reason": "source_changed"})
        dispatch.assert_not_called()

    @mock.patch("apps.source.services.interface.run_connection_test")
    def test_manual_probe_does_not_cross_removal_fence(self, run_test):
        SourceResource.objects.filter(pk=self.resource.id).update(
            status="remove_failed",
        )

        result = test_resource_connection(resource=self.resource)

        self.assertFalse(result["success"])
        self.assertIn("being removed", result["message"])
        run_test.assert_not_called()

    @mock.patch("apps.source.services.interface.best_effort_unmount_on_proxy")
    @mock.patch("apps.source.services.interface.run_connection_test")
    def test_manual_probe_failure_racing_removal_is_compensated(
        self,
        run_test,
        best_effort_unmount,
    ):
        SourceResource.objects.filter(pk=self.resource.id).update(
            connection_test_status="idle",
            connection_probe_token=None,
        )

        def remove_source(**_kwargs):
            SourceResource.objects.filter(pk=self.resource.id).update(
                status="removing",
            )
            return {"success": False, "message": "Capacity read failed"}

        run_test.side_effect = remove_source

        result = test_resource_connection(resource=self.resource)

        self.assertFalse(result["success"])
        self.assertTrue(result["stale"])
        best_effort_unmount.assert_called_once_with(
            resource=self.resource,
            node_id=self.proxy.id,
            force=True,
            payload_override=mock.ANY,
        )

    @mock.patch("apps.source.services.interface.run_connection_test")
    def test_manual_probe_does_not_overlap_active_probe(self, run_test):
        result = test_resource_connection(resource=self.resource)

        self.assertFalse(result["success"])
        self.assertIn("already running", result["message"])
        run_test.assert_not_called()

    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_manual_mount_does_not_cross_removal_fence(self, dispatch_task):
        SourceResource.objects.filter(pk=self.resource.id).update(
            status="remove_failed",
        )

        result = mount_resource(resource=self.resource)

        self.assertFalse(result["success"])
        self.assertIn("being removed", result["message"])
        dispatch_task.assert_not_called()

    @mock.patch(
        "apps.source.services.internal.connection.best_effort_unmount_on_proxy"
    )
    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_mount_success_racing_removal_is_compensated(
        self,
        dispatch_task,
        best_effort_unmount,
    ):
        def fence_during_mount(**_kwargs):
            SourceResource.objects.filter(pk=self.resource.id).update(
                status="removing",
            )
            return SimpleNamespace(timed_out=False, ok=True, result={})

        dispatch_task.side_effect = fence_during_mount

        result = mount_resource(resource=self.resource)

        self.assertFalse(result["success"])
        self.assertTrue(result["stale"])
        best_effort_unmount.assert_called_once_with(
            resource=self.resource,
            node_id=self.proxy.id,
            force=True,
        )

    @mock.patch(
        "apps.source.services.internal.connection.best_effort_unmount_on_proxy"
    )
    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_mount_failure_racing_removal_is_compensated(
        self,
        dispatch_task,
        best_effort_unmount,
    ):
        def fence_during_mount(**_kwargs):
            SourceResource.objects.filter(pk=self.resource.id).update(
                status="removing",
            )
            return SimpleNamespace(
                timed_out=False,
                ok=False,
                result={},
                task=SimpleNamespace(last_error="Capacity read failed", status="failed"),
                stream_message=None,
            )

        dispatch_task.side_effect = fence_during_mount

        result = mount_resource(resource=self.resource)

        self.assertFalse(result["success"])
        best_effort_unmount.assert_called_once_with(
            resource=self.resource,
            node_id=self.proxy.id,
            force=True,
        )

    @mock.patch(
        "apps.source.services.internal.connection.best_effort_unmount_on_proxy"
    )
    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_mount_timeout_racing_removal_is_compensated(
        self,
        dispatch_task,
        best_effort_unmount,
    ):
        def fence_during_mount(**_kwargs):
            Task.objects.create(
                organization_id=self.org.id,
                task_type=Task.Type.SOURCE_UNREGISTER,
                status=Task.Status.RUNNING,
                request_payload={
                    "source_ids": [f"nas:{self.resource.id}"],
                    "force": False,
                },
            )
            SourceResource.objects.filter(pk=self.resource.id).update(
                status="removing",
            )
            return SimpleNamespace(timed_out=True, ok=False)

        dispatch_task.side_effect = fence_during_mount

        result = mount_resource(resource=self.resource)

        self.assertFalse(result["success"])
        best_effort_unmount.assert_called_once_with(
            resource=self.resource,
            node_id=self.proxy.id,
            force=False,
        )

    @mock.patch(
        "apps.source.services.internal.connection.best_effort_unmount_on_proxy"
    )
    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_mount_timeout_without_removal_does_not_force_cleanup(
        self,
        dispatch_task,
        best_effort_unmount,
    ):
        dispatch_task.return_value = SimpleNamespace(timed_out=True, ok=False)

        result = mount_resource(resource=self.resource)

        self.assertFalse(result["success"])
        best_effort_unmount.assert_not_called()
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.mount_status, "error")

    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_removal_compensation_requests_force_cleanup(self, dispatch_task):
        dispatch_task.return_value = SimpleNamespace(timed_out=False, ok=True)

        best_effort_unmount_on_proxy(
            resource=self.resource,
            node_id=self.proxy.id,
            force=True,
        )

        self.assertTrue(dispatch_task.call_args.kwargs["payload"]["force_cleanup"])

    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    @mock.patch(
        "apps.source.services.internal.connection.dispatch_nas_agent_task_async"
    )
    def test_removal_compensation_can_release_worker_after_dispatch(
        self,
        dispatch_async,
        dispatch_sync,
    ):
        dispatch_async.return_value = SimpleNamespace(task_id="unmount-task")

        result = best_effort_unmount_on_proxy(
            resource=self.resource,
            node_id=self.proxy.id,
            force=True,
            wait=False,
        )

        self.assertEqual(
            result,
            {"success": True, "queued": True, "node_task_id": "unmount-task"},
        )
        self.assertTrue(dispatch_async.call_args.kwargs["payload"]["force_cleanup"])
        dispatch_sync.assert_not_called()

    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_regular_proxy_cleanup_does_not_request_force_cleanup(self, dispatch_task):
        dispatch_task.return_value = SimpleNamespace(timed_out=False, ok=True)

        best_effort_unmount_on_proxy(
            resource=self.resource,
            node_id=self.proxy.id,
        )

        self.assertNotIn("force_cleanup", dispatch_task.call_args.kwargs["payload"])

    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_strict_removal_compensation_does_not_request_force_cleanup(
        self,
        dispatch_task,
    ):
        Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            status=Task.Status.RUNNING,
            request_payload={
                "source_ids": [f"nas:{self.resource.id}"],
                "force": False,
            },
        )
        SourceResource.objects.filter(pk=self.resource.id).update(status="removing")
        dispatch_task.return_value = SimpleNamespace(
            timed_out=False,
            ok=True,
            result={"cleanup_complete": True},
        )

        best_effort_unmount_on_proxy(
            resource=self.resource,
            node_id=self.proxy.id,
            force=True,
        )

        self.assertNotIn("force_cleanup", dispatch_task.call_args.kwargs["payload"])

    @mock.patch("apps.source.services.internal.connection.dispatch_nas_agent_task")
    def test_compensating_force_cleanup_returns_retained_resources(self, dispatch_task):
        dispatch_task.return_value = SimpleNamespace(
            timed_out=False,
            ok=True,
            result={
                "cleanup_complete": False,
                "retained_resources": ["nas_mount_reference"],
                "warnings": ["The NAS mount was lazily detached."],
            },
        )

        result = best_effort_unmount_on_proxy(
            resource=self.resource,
            node_id=self.proxy.id,
            force=True,
        )

        self.assertTrue(result["success"])
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(
            result["retained_resources"],
            ["nas_mount_reference"],
        )

    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_probe_skips_when_proxy_is_offline(self, dispatch):
        self.proxy.availability = Node.Availability.OFFLINE
        self.proxy.save(update_fields=["availability", "updated_at"])

        result = run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )

        self.assertEqual(result, {"status": "failed", "reason": "proxy_offline"})
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.connection_test_status, "failed")
        dispatch.assert_not_called()

    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_inconclusive_probe_failure_retains_availability(self, dispatch):
        SourceResource.objects.filter(pk=self.resource.id).update(
            availability="online",
        )
        node_task = self._probe_node_task()
        dispatch.return_value = self._dispatch_handle(node_task)

        run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )
        node_task.status = NodeTask.Status.TIMEOUT
        node_task.last_error = "Connection test timed out"
        node_task.save(update_fields=["status", "last_error", "updated_at"])
        project_source_connection_probe(node_task=node_task)

        self.resource.refresh_from_db()
        self.assertEqual(self.resource.availability, "online")

    @mock.patch("apps.source.tasks.connection_probe.dispatch_nas_agent_task_async")
    def test_confirmed_agent_failure_marks_availability_offline(self, dispatch):
        SourceResource.objects.filter(pk=self.resource.id).update(
            availability="online",
        )
        node_task = self._probe_node_task()
        dispatch.return_value = self._dispatch_handle(node_task)

        run_source_resource_capacity_probe(
            resource_id=self.resource.id,
            probe_token=str(self.probe_token),
            expected_bound_node_id=self.proxy.id,
        )
        node_task.status = NodeTask.Status.FAILED
        node_task.last_error = "Proxy agent could not access the NAS export."
        node_task.save(update_fields=["status", "last_error", "updated_at"])
        project_source_connection_probe(node_task=node_task)

        self.resource.refresh_from_db()
        self.assertEqual(self.resource.availability, "offline")

    def test_only_accepted_agent_failure_is_conclusive(self):
        accepted_failure = SimpleNamespace(
            timed_out=False,
            task=SimpleNamespace(
                status="failed",
                accepted_at=timezone.now(),
            ),
        )
        delivery_failure = SimpleNamespace(
            timed_out=False,
            task=SimpleNamespace(status="failed", accepted_at=None),
        )
        timeout = SimpleNamespace(
            timed_out=True,
            task=SimpleNamespace(
                status="failed",
                accepted_at=timezone.now(),
            ),
        )

        self.assertTrue(confirmed_agent_failure(accepted_failure))
        self.assertFalse(confirmed_agent_failure(delivery_failure))
        self.assertFalse(confirmed_agent_failure(timeout))

    def test_reconcile_fails_stale_probe_and_clears_token(self):
        SourceResource.objects.filter(pk=self.resource.id).update(
            updated_at=timezone.now() - timedelta(minutes=20),
        )

        result = reconcile_stale_source_connection_probes()

        self.resource.refresh_from_db()
        self.assertEqual(result, {"stale": 1, "failed": 1})
        self.assertEqual(self.resource.connection_test_status, "failed")
        self.assertIsNone(self.resource.connection_probe_token)
        self.assertEqual(self.resource.status, "error")

    @mock.patch("apps.source.tasks.connection_probe.cancel_agent_task")
    def test_reconcile_cancels_stale_probe_node_task(self, cancel_task):
        node_task = self._probe_node_task()
        SourceResource.objects.filter(pk=self.resource.id).update(
            availability="online",
            availability_updated_at=timezone.now() - timedelta(minutes=20),
            updated_at=timezone.now() - timedelta(minutes=20),
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = reconcile_stale_source_connection_probes()

        self.assertEqual(result, {"stale": 1, "failed": 1})
        cancel_task.assert_called_once_with(
            task_id=node_task.id,
            reason="Automatic NAS connection probe expired",
        )
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.availability, "online")

    @mock.patch(
        "apps.source.tasks.connection_probe.cancel_agent_task",
        side_effect=RuntimeError("redis unavailable"),
    )
    def test_stale_probe_reconcile_survives_cancel_delivery_failure(
        self,
        cancel_task,
    ):
        node_task = self._probe_node_task()
        SourceResource.objects.filter(pk=self.resource.id).update(
            updated_at=timezone.now() - timedelta(minutes=20),
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = reconcile_stale_source_connection_probes()

        self.assertEqual(result, {"stale": 1, "failed": 1})
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.connection_test_status, "failed")
        self.assertIsNone(self.resource.connection_probe_token)
        cancel_task.assert_called_once_with(
            task_id=node_task.id,
            reason="Automatic NAS connection probe expired",
        )

    @mock.patch(
        "apps.source.tasks.connection_probe.queue_source_resource_capacity_probe"
    )
    def test_availability_reconcile_expires_and_queues_refresh(self, queue_probe):
        observed_at = timezone.now() - timedelta(minutes=16)
        SourceResource.objects.filter(pk=self.resource.id).update(
            availability="online",
            availability_updated_at=observed_at,
            connection_test_status="idle",
            connection_probe_token=None,
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = reconcile_source_availability(limit=100)

        self.resource.refresh_from_db()
        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(self.resource.availability, "offline")
        self.assertEqual(self.resource.connection_test_status, "pending")
        queue_probe.assert_called_once()

    @mock.patch(
        "apps.source.tasks.connection_probe.queue_source_resource_capacity_probe"
    )
    def test_availability_reconcile_does_not_overlap_active_node_task(
        self, queue_probe
    ):
        observed_at = timezone.now() - timedelta(minutes=16)
        SourceResource.objects.filter(pk=self.resource.id).update(
            availability="online",
            availability_updated_at=observed_at,
            connection_test_status="idle",
            connection_probe_token=None,
        )
        self._probe_node_task()

        with self.captureOnCommitCallbacks(execute=True):
            result = reconcile_source_availability(limit=100)

        self.resource.refresh_from_db()
        self.assertEqual(result["queued"], 0)
        self.assertEqual(self.resource.connection_test_status, "idle")
        queue_probe.assert_not_called()

    def test_availability_reconcile_marks_source_offline_with_proxy(self):
        SourceResource.objects.filter(pk=self.resource.id).update(
            availability="online",
        )
        self.proxy.availability = Node.Availability.OFFLINE
        self.proxy.save(
            update_fields=["availability", "availability_updated_at", "updated_at"]
        )

        result = reconcile_source_availability(limit=100)

        self.resource.refresh_from_db()
        self.assertEqual(result["proxy_offline"], 1)
        self.assertEqual(self.resource.availability, "offline")

    def test_availability_reconcile_does_not_repeat_stale_proxy_offline_rows(self):
        observed_at = timezone.now() - timedelta(minutes=16)
        SourceResource.objects.filter(pk=self.resource.id).update(
            availability="offline",
            availability_updated_at=observed_at,
        )
        Node.objects.filter(pk=self.proxy.id).update(
            availability=Node.Availability.OFFLINE,
            availability_updated_at=observed_at,
        )

        result = reconcile_source_availability(limit=100)

        self.resource.refresh_from_db()
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["proxy_offline"], 0)
        self.assertEqual(self.resource.availability_updated_at, observed_at)

    @mock.patch(
        "apps.source.tasks.connection_probe.queue_source_resource_capacity_probe"
    )
    def test_proxy_recovery_queues_fresh_probe_without_marking_online(
        self,
        queue_probe,
    ):
        self.resource.connection_test_status = "idle"
        self.resource.connection_probe_token = None
        self.resource.save(
            update_fields=[
                "connection_test_status",
                "connection_probe_token",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = queue_source_availability_probes_for_proxy(
                proxy_id=self.proxy.id,
            )

        self.resource.refresh_from_db()
        self.assertEqual(result["queued"], 1)
        self.assertEqual(self.resource.availability, "offline")
        self.assertEqual(self.resource.connection_test_status, "pending")
        queue_probe.assert_called_once()

    @mock.patch(
        "apps.source.tasks.connection_probe."
        "queue_source_availability_probes_for_proxy_task.apply_async"
    )
    def test_proxy_recovery_dispatches_probe_batch_asynchronously(self, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            project_node_availability(
                node_id=self.proxy.id,
                transitioned=True,
            )

        dispatch.assert_called_once_with(
            kwargs={"proxy_id": self.proxy.id},
            queue="source.remote-io",
        )

    @mock.patch("apps.source.services.interface.schedule_remount_after_proxy_change")
    def test_bind_node_cancels_active_probe_immediately(self, schedule_remount):
        replacement = Node.objects.create(
            organization=self.org,
            name="source-connection-replacement-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )

        result = bind_node(resource=self.resource, node_id=replacement.id)

        self.resource.refresh_from_db()
        self.assertTrue(result["success"])
        self.assertEqual(self.resource.bound_node_id, replacement.id)
        self.assertEqual(self.resource.connection_test_status, "idle")
        self.assertIsNone(self.resource.connection_probe_token)
        schedule_remount.assert_called_once()

    @mock.patch("apps.source.services.interface.best_effort_unmount_on_proxy")
    @mock.patch("apps.source.services.interface.run_connection_test")
    def test_manual_probe_discards_result_after_source_edit(
        self,
        run_test,
        best_effort_unmount,
    ):
        SourceResource.objects.filter(pk=self.resource.id).update(
            connection_test_status="idle",
            connection_probe_token=None,
        )

        def edit_source(**_kwargs):
            update_source_resource(
                resource=SourceResource.objects.get(pk=self.resource.id),
                user=None,
                description="edited during manual probe",
            )
            return {"success": True, "message": "Connection test successful"}

        run_test.side_effect = edit_source

        result = test_resource_connection(resource=self.resource)

        self.resource.refresh_from_db()
        self.assertTrue(result["stale"])
        self.assertEqual(self.resource.description, "edited during manual probe")
        self.assertEqual(self.resource.connection_test_status, "idle")
        self.assertIsNone(self.resource.last_connection_test)
        self.assertTrue(run_test.call_args.kwargs["cleanup_after_test"])
        self.assertIn(
            "/mounts/validations/",
            run_test.call_args.kwargs["mount_point_override"],
        )
        best_effort_unmount.assert_called_once_with(
            resource=mock.ANY,
            node_id=self.proxy.id,
            force=True,
            payload_override=mock.ANY,
        )
