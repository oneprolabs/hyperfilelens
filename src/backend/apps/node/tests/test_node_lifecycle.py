"""Tests for node lifecycle operations."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.exceptions import NodeLifecycleError
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal.node_lifecycle import (
    _version_matches_target,
    advance_node_lifecycle,
    compute_node_lifecycle,
    preview_batch_operations,
    queue_detached_remove_verification,
    start_node_remove,
    start_node_upgrade,
)
from apps.node.services.internal.task import (
    _RouteState,
    complete_task,
    create_agent_task,
    deliver_agent_task,
    project_node_lifecycle_task,
)
from apps.task.models import Task, TaskResource


class NodeLifecycleTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="lifecycle-org", name="Lifecycle Org")
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="lifecycle@test.local",
            email="lifecycle@test.local",
            password="test-pass",
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-lifecycle",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            version="1.0.0",
            metadata={"capabilities": ["detached_uninstall_v2"]},
        )

    def _create_active_source_unregister(self) -> Task:
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Unregister agent source",
            status=Task.Status.RUNNING,
        )
        TaskResource.objects.create(
            task=task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=self.node.id,
            is_primary=True,
        )
        return task

    def test_lifecycle_failure_projects_operation_specific_node_status(self):
        cases = (
            ("agent.upgrade", Node.Status.UPGRADE_FAILED),
            ("agent.uninstall", Node.Status.DEREGISTRATION_FAILED),
        )
        for kind, expected_status in cases:
            with self.subTest(kind=kind):
                task = NodeTask.objects.create(
                    organization=self.org,
                    node=self.node,
                    kind=kind,
                    status=NodeTask.Status.FAILED,
                    watchdog_deadline_at=timezone.now(),
                    correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
                    correlation_id=f"{kind}:{self.node.id}",
                )

                project_node_lifecycle_task(node_task=task)

                self.node.refresh_from_db()
                self.assertEqual(self.node.status, expected_status)
                self.assertEqual(self.node.availability, Node.Availability.ONLINE)
                self.node.status = Node.Status.ACTIVE
                self.node.save(update_fields=["status", "updated_at"])

    def test_upgrade_is_blocked_by_active_source_unregister(self):
        self._create_active_source_unregister()

        with self.assertRaises(NodeLifecycleError) as raised:
            start_node_upgrade(org=self.org, node=self.node, user=self.user)

        self.assertEqual(raised.exception.code, "source_operation_in_progress")
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())

    def test_force_remove_is_blocked_by_unrelated_source_unregister(self):
        self._create_active_source_unregister()

        with self.assertRaises(NodeLifecycleError) as raised:
            start_node_remove(
                org=self.org,
                node=self.node,
                user=self.user,
                force=True,
            )

        self.assertEqual(raised.exception.code, "source_operation_in_progress")
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_deleted)

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=True)
    def test_strict_remove_requires_reliable_uninstall_capability(self, _routable):
        self.node.metadata = {"capabilities": ["repository_cleanup_v1"]}
        self.node.save(update_fields=["metadata", "updated_at"])

        with self.assertRaises(NodeLifecycleError) as raised:
            start_node_remove(org=self.org, node=self.node, user=self.user)

        self.assertEqual(raised.exception.code, "agent_upgrade_required")
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=True)
    def test_force_remove_old_agent_purges_control_plane_and_records_residue(self, _routable):
        self.node.metadata = {"capabilities": []}
        self.node.save(update_fields=["metadata", "updated_at"])

        result = start_node_remove(
            org=self.org,
            node=self.node,
            user=self.user,
            force=True,
        )

        self.assertEqual(result["state"], "completed")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(result["retained_resources"], ["agent_installation"])
        self.assertEqual(result["cleanup_failures"][0]["code"], "agent_upgrade_required")
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_offline_strict_remove_is_blocked_before_task_creation(self, _routable):
        with self.assertRaises(NodeLifecycleError) as raised:
            start_node_remove(org=self.org, node=self.node, user=self.user)

        self.assertEqual(raised.exception.code, "node_offline")
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_deleted)

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_offline_force_remove_purges_with_residue_summary(self, _routable):
        result = start_node_remove(
            org=self.org,
            node=self.node,
            user=self.user,
            force=True,
        )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["outcome"], "force_cleanup_success")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(result["retained_resources"], ["agent_installation"])
        self.node.refresh_from_db()
        self.assertTrue(self.node.is_deleted)

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_parent_owned_offline_force_remove_defers_control_plane_purge(
        self,
        _routable,
    ):
        result = start_node_remove(
            org=self.org,
            node=self.node,
            user=self.user,
            force=True,
            triggered_by_task_id=123,
        )

        self.assertFalse(result["purged"])
        self.assertTrue(result["control_plane_purge_deferred"])
        self.assertEqual(result["phase"], "awaiting_parent_finalize")
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_deleted)

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_offline_force_gateway_records_agent_and_sidecar_residue(self, _routable):
        self.node.role = NodeRole.GATEWAY
        self.node.save(update_fields=["role", "updated_at"])

        result = start_node_remove(
            org=self.org,
            node=self.node,
            user=self.user,
            force=True,
        )

        self.assertEqual(
            result["retained_resources"],
            ["agent_installation", "lensnode_sidecar"],
        )
        operation = Task.objects.get(task_type=Task.Type.NODE_LIFECYCLE)
        self.assertEqual(operation.status, Task.Status.SUCCESS)
        self.assertEqual(operation.result_payload["result"], "partial_success")
        self.assertFalse(operation.result_payload["cleanup_complete"])
        self.assertEqual(operation.resources.get().resource_id, self.node.id)

    def test_proxy_uninstall_is_projected_to_operations_with_warning_result(self):
        self.node.role = NodeRole.PROXY
        self.node.save(update_fields=["role", "updated_at"])
        node_task = create_agent_task(
            org=self.org,
            node=self.node,
            kind="agent.uninstall",
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
            payload={"force_cleanup": True},
        )

        operation = Task.objects.get(task_type=Task.Type.NODE_LIFECYCLE)
        self.assertEqual(operation.status, Task.Status.PENDING)

        complete_task(
            task_id=node_task.id,
            node_id=self.node.id,
            status=NodeTask.Status.SUCCESS,
            result={
                "cleanup_complete": False,
                "cleanup_failures": [
                    {"code": "agent_offline", "detail": "Agent disconnected."}
                ],
                "retained_resources": ["agent_installation"],
            },
        )

        operation.refresh_from_db()
        self.assertEqual(operation.status, Task.Status.SUCCESS)
        self.assertEqual(operation.result_payload["result"], "partial_success")
        self.assertEqual(
            operation.steps.get(step_name="cleanup_node_endpoint").status,
            "warning",
        )

    def test_proxy_uninstall_delivery_projects_running_state(self):
        self.node.role = NodeRole.PROXY
        self.node.save(update_fields=["role", "updated_at"])
        node_task = create_agent_task(
            org=self.org,
            node=self.node,
            kind="agent.uninstall",
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )

        with (
            patch(
                "apps.node.services.internal.task._node_route_state",
                return_value=_RouteState.ONLINE,
            ),
            patch("apps.node.services.internal.task._send_task_command"),
        ):
            deliver_agent_task(task=node_task)

        operation = Task.objects.get(task_type=Task.Type.NODE_LIFECYCLE)
        self.assertEqual(operation.status, Task.Status.RUNNING)
        self.assertEqual(
            operation.steps.get(step_name="dispatch_agent_uninstall").status,
            "success",
        )
        self.assertEqual(
            operation.steps.get(step_name="cleanup_node_endpoint").status,
            "running",
        )

    def test_proxy_uninstall_delivery_failure_projects_failed_dispatch(self):
        self.node.role = NodeRole.PROXY
        self.node.save(update_fields=["role", "updated_at"])
        node_task = create_agent_task(
            org=self.org,
            node=self.node,
            kind="agent.uninstall",
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )

        with (
            patch(
                "apps.node.services.internal.task._node_route_state",
                return_value=_RouteState.ONLINE,
            ),
            patch(
                "apps.node.services.internal.task._send_task_command",
                side_effect=RuntimeError("dispatch unavailable"),
            ),
        ):
            deliver_agent_task(task=node_task)

        node_task.refresh_from_db()
        operation = Task.objects.get(task_type=Task.Type.NODE_LIFECYCLE)
        self.assertEqual(node_task.status, NodeTask.Status.FAILED)
        self.assertEqual(operation.status, Task.Status.FAILED)
        self.assertEqual(float(operation.progress), 35)
        self.assertIn("dispatch unavailable", operation.error_message)
        self.assertEqual(
            operation.steps.get(step_name="dispatch_agent_uninstall").status,
            "failed",
        )
        self.assertEqual(
            operation.steps.get(step_name="cleanup_node_endpoint").status,
            "skipped",
        )

    def test_late_uninstall_success_refreshes_terminal_operation_once(self):
        self.node.role = NodeRole.PROXY
        self.node.save(update_fields=["role", "updated_at"])
        node_task = create_agent_task(
            org=self.org,
            node=self.node,
            kind="agent.uninstall",
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )
        with (
            patch(
                "apps.node.services.internal.task._node_route_state",
                return_value=_RouteState.ONLINE,
            ),
            patch("apps.node.services.internal.task._send_task_command"),
        ):
            deliver_agent_task(task=node_task)
        complete_task(
            task_id=node_task.id,
            node_id=self.node.id,
            status=NodeTask.Status.FAILED,
            error="callback timed out",
            result={"cleanup_complete": False},
        )
        operation = Task.objects.get(task_type=Task.Type.NODE_LIFECYCLE)
        self.assertEqual(operation.status, Task.Status.FAILED)
        self.assertEqual(
            operation.events.filter(message__startswith="Task finished with status").count(),
            1,
        )

        complete_task(
            task_id=node_task.id,
            node_id=self.node.id,
            status=NodeTask.Status.SUCCESS,
            result={"cleanup_complete": True},
        )

        operation.refresh_from_db()
        self.assertEqual(operation.status, Task.Status.SUCCESS)
        self.assertTrue(operation.result_payload["cleanup_complete"])
        self.assertEqual(operation.result_payload["result"], "success")
        self.assertEqual(
            operation.steps.get(step_name="cleanup_node_endpoint").status,
            "success",
        )
        self.assertEqual(
            operation.events.filter(message__startswith="Task finished with status").count(),
            1,
        )
        self.assertEqual(
            operation.events.filter(
                message="Node removal result reconciled from the authoritative Agent task"
            ).count(),
            1,
        )

    @patch("apps.node.services.internal.node_lifecycle.run_agent_task_async")
    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=True)
    def test_online_remove_dispatches_uninstall(self, _routable, mock_dispatch):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )
        mock_dispatch.return_value = type(
            "Handle",
            (),
            {"task": task, "task_id": str(task.id)},
        )()

        result = start_node_remove(
            org=self.org,
            node=self.node,
            user=self.user,
            triggered_by_task_id=42,
            triggered_by_task_attempt=2,
        )
        self.assertEqual(result["state"], "removing")
        mock_dispatch.assert_called_once()
        self.assertEqual(
            mock_dispatch.call_args.kwargs["payload"]["source_unregister_task_id"],
            42,
        )
        self.assertEqual(
            mock_dispatch.call_args.kwargs["payload"]["source_unregister_attempt"],
            2,
        )

    @patch("apps.node.services.internal.node_lifecycle.validate_agent_upgrade", return_value="1.2.0")
    @patch("apps.node.services.internal.node_lifecycle.run_agent_task_async")
    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=True)
    def test_start_upgrade_dispatches_task(self, _routable, mock_dispatch, _validate):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            result={"target_version": "1.2.0"},
            watchdog_deadline_at=timezone.now(),
        )
        mock_dispatch.return_value = type(
            "Handle",
            (),
            {"task": task, "task_id": str(task.id)},
        )()

        result = start_node_upgrade(org=self.org, node=self.node, user=self.user)
        self.assertEqual(result["state"], "upgrading")
        self.assertEqual(result["target_version"], "1.2.0")

    @patch("apps.node.services.internal.node_workload.get_node_workload_blockers")
    def test_upgrade_blocked_by_workload(self, mock_blockers):
        from apps.node.services.internal.node_workload import NodeWorkloadBlocker

        mock_blockers.return_value = [
            NodeWorkloadBlocker(
                code="backup_running",
                task_uuid="abc",
                task_type="backup",
                label="backup · nightly",
            )
        ]
        with self.assertRaises(NodeLifecycleError) as ctx:
            start_node_upgrade(org=self.org, node=self.node, user=self.user)
        self.assertEqual(ctx.exception.code, "node_workload_active")

    def test_force_gateway_remove_does_not_bypass_knowledge_source_binding(self):
        from apps.lens_bridge.models import LensGatewayLink, LensKnowledgeSource

        gateway = Node.objects.create(
            organization=self.org,
            name="gateway-with-knowledge-source",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        LensKnowledgeSource.objects.create(
            organization=self.org,
            gateway=gateway,
            gateway_link=gateway_link,
            name="Bound knowledge source",
            source_path="/protected/source",
        )

        with self.assertRaises(NodeLifecycleError) as raised:
            start_node_remove(
                org=self.org,
                node=gateway,
                user=self.user,
                force=True,
            )

        self.assertEqual(raised.exception.code, "node_remove_blocked")
        self.assertTrue(
            any(
                blocker["code"] == "knowledge_source_bound"
                for blocker in raised.exception.blockers
            )
        )
        self.assertFalse(NodeTask.objects.filter(node=gateway).exists())

    @patch("apps.node.services.internal.node_lifecycle.validate_agent_upgrade", return_value="1.2.0")
    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=True)
    def test_preview_batch_upgrade(self, _routable, _validate):
        preview = preview_batch_operations(
            org=self.org,
            node_ids=[self.node.id],
            kind="upgrade",
        )
        self.assertEqual(len(preview["eligible"]), 1)
        self.assertEqual(preview["eligible"][0]["target_version"], "1.2.0")

    def test_upgrade_success_clears_lifecycle_when_version_differs(self):
        """Lifecycle is always cleared on SUCCESS — version was already verified
        by _advance_upgrade_verify before marking the task SUCCESS. A redundant
        post-SUCCESS version check risks false negatives (#639)."""
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.SUCCESS,
            result={"target_version": "1.2.0", "mode": "local_detached"},
            watchdog_deadline_at=timezone.now(),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        with patch("apps.node.services.internal.node_lifecycle.agent_session_registered", return_value=True):
            lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertIsNone(lifecycle)

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_upgrade_success_clears_when_version_matches_despite_stale_ws(self, _routable):
        self.node.version = "1.2.0"
        self.node.save(update_fields=["version"])
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.SUCCESS,
            result={"target_version": "1.2.0", "mode": "local_detached"},
            watchdog_deadline_at=timezone.now(),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertIsNone(lifecycle)

    def test_main_build_version_matches_exact_commit(self):
        self.node.version = "main-123abcd"
        self.assertTrue(
            _version_matches_target(node=self.node, target_version="main-123abcd")
        )

    def test_main_build_version_rejects_different_commit(self):
        self.node.version = "main-7654321"
        self.assertFalse(
            _version_matches_target(node=self.node, target_version="main-123abcd")
        )

    def test_release_version_match_preserves_ordered_semver_behavior(self):
        self.node.version = "1.2.1"
        self.assertTrue(_version_matches_target(node=self.node, target_version="1.2.0"))

    def test_release_identity_requires_target_commit_when_supplied(self):
        self.node.version = "1.2.0"
        self.node.metadata = {"inventory": {"agent_commit": "a" * 40}}
        self.assertTrue(
            _version_matches_target(
                node=self.node,
                target_version="1.2.0",
                target_commit="a" * 40,
            )
        )
        self.assertFalse(
            _version_matches_target(
                node=self.node,
                target_version="1.2.0",
                target_commit="b" * 40,
            )
        )

    @patch("apps.node.services.internal.node_lifecycle.agent_session_registered", return_value=False)
    def test_same_version_running_task_not_finalized_on_enrich(self, _session):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            payload={"target_version": "1.0.0"},
            result={"target_version": "1.0.0", "mode": "local_detached"},
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        advance_node_lifecycle(org=self.org, node=self.node, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertEqual(lifecycle["state"], "restarting")

    @patch("apps.node.services.internal.node_lifecycle.agent_session_registered", return_value=True)
    def test_same_version_detached_finalizes_after_reconnect(self, _session):
        stable_seconds = int(node_conf.UPGRADE_STABLE_SECONDS)
        verify_started = timezone.now() - timezone.timedelta(seconds=stable_seconds + 1)
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            payload={"target_version": "1.0.0"},
            result={
                "target_version": "1.0.0",
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
                "disconnect_observed_at": timezone.now().isoformat(),
                "verify_started_at": verify_started.isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        advance_node_lifecycle(org=self.org, node=self.node, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.SUCCESS)

    @patch("apps.node.services.internal.node_lifecycle.agent_session_registered", return_value=True)
    def test_same_version_does_not_verify_before_disconnect(self, _session):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            payload={"target_version": "1.0.0"},
            result={
                "target_version": "1.0.0",
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
                "verify_started_at": (
                    timezone.now()
                    - timezone.timedelta(seconds=node_conf.UPGRADE_STABLE_SECONDS + 1)
                ).isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )

        advance_node_lifecycle(org=self.org, node=self.node, user=self.user)

        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        self.assertNotIn("verify_started_at", task.result or {})

    @patch("apps.node.services.internal.node_lifecycle.agent_session_registered", return_value=True)
    def test_upgrade_verify_waits_for_stable_window(self, _session):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            payload={"target_version": "1.0.0"},
            result={
                "target_version": "1.0.0",
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
                "disconnect_observed_at": timezone.now().isoformat(),
                "verify_started_at": timezone.now().isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        advance_node_lifecycle(org=self.org, node=self.node, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertEqual(lifecycle["state"], "verifying")

    @patch("apps.node.services.internal.node_lifecycle.agent_session_registered", return_value=True)
    def test_upgrade_verify_starts_clock_on_first_stable_reconnect(self, _session):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            payload={"target_version": "1.0.0"},
            result={
                "target_version": "1.0.0",
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
                "disconnect_observed_at": timezone.now().isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        advance_node_lifecycle(org=self.org, node=self.node, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        self.assertIn("verify_started_at", task.result or {})
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertEqual(lifecycle["state"], "verifying")

    @patch("apps.node.services.internal.node_lifecycle.agent_session_registered", return_value=False)
    def test_upgrade_verify_clears_clock_when_ws_drops(self, _session):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            payload={"target_version": "1.0.0"},
            result={
                "target_version": "1.0.0",
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
                "disconnect_observed_at": timezone.now().isoformat(),
                "verify_started_at": timezone.now().isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        advance_node_lifecycle(org=self.org, node=self.node, user=self.user)
        task.refresh_from_db()
        self.assertNotIn("verify_started_at", task.result or {})
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertEqual(lifecycle["state"], "restarting")

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_remove_does_not_finalize_from_ws_disconnect_alone(self, _routable):
        detached_at = timezone.now() - timezone.timedelta(seconds=35)
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.RUNNING,
            result={
                "mode": "local_detached",
                "detached_at": detached_at.isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )
        summary = advance_node_lifecycle(org=self.org, node=self.node, user=self.user)
        self.assertIsNone(summary)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertEqual(lifecycle["phase"], "waiting_for_completion")
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_deleted)

    def test_remove_fails_when_completion_callback_times_out(self):
        detached_at = timezone.now() - timezone.timedelta(
            seconds=node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS + 1
        )
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.RUNNING,
            result={
                "mode": "local_detached",
                "detached_at": detached_at.isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )

        summary = advance_node_lifecycle(org=self.org, node=self.node, user=self.user)

        task.refresh_from_db()
        self.node.refresh_from_db()
        self.assertIsNone(summary)
        self.assertEqual(task.status, NodeTask.Status.FAILED)
        self.assertIn("timed out", task.last_error)
        self.assertFalse(self.node.is_deleted)

    def test_force_remove_purges_after_completion_callback_timeout(self):
        detached_at = timezone.now() - timezone.timedelta(
            seconds=node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS + 1
        )
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.RUNNING,
            payload={"force_cleanup": True},
            result={
                "mode": "local_detached",
                "detached_at": detached_at.isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )

        summary = advance_node_lifecycle(org=self.org, node=self.node, user=self.user)

        task.refresh_from_db()
        self.node.refresh_from_db()
        self.assertTrue(summary.get("purged"))
        self.assertEqual(task.status, NodeTask.Status.SUCCESS)
        self.assertFalse(task.result["cleanup_complete"])
        self.assertEqual(task.result["outcome"], "force_cleanup_success")
        self.assertTrue(task.result["completion_timed_out_at"])
        self.assertNotIn("completion_received_at", task.result)
        self.assertTrue(self.node.is_deleted)

    def test_force_gateway_timeout_records_unverified_agent_and_sidecar(self):
        self.node.role = NodeRole.GATEWAY
        self.node.save(update_fields=["role", "updated_at"])
        detached_at = timezone.now() - timezone.timedelta(
            seconds=node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS + 1
        )
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.RUNNING,
            payload={"force_cleanup": True},
            result={"mode": "local_detached", "detached_at": detached_at.isoformat()},
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )

        advance_node_lifecycle(org=self.org, node=self.node, user=self.user)

        task.refresh_from_db()
        self.assertEqual(
            task.result["retained_resources"],
            ["unverified_agent_installation", "unverified_lensnode_sidecar"],
        )

    @patch("apps.node.tasks.lifecycle.advance_node_lifecycle_for_node.apply_async")
    def test_detached_remove_queues_callback_timeout_verification(self, apply_async):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.RUNNING,
            result={
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )

        queued = queue_detached_remove_verification(node_task=task)

        self.assertTrue(queued)
        apply_async.assert_called_once_with(
            kwargs={"node_id": self.node.id},
            countdown=node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS + 1,
        )

    @patch("apps.source.tasks.source_unregister.queue_source_unregister_task")
    def test_failed_remove_immediately_queues_source_unregister_parent(
        self,
        queue_parent,
    ):
        for status in (
            NodeTask.Status.FAILED,
            NodeTask.Status.TIMEOUT,
            NodeTask.Status.CANCELED,
        ):
            with self.subTest(status=status):
                task = NodeTask.objects.create(
                    organization=self.org,
                    node=self.node,
                    kind="agent.uninstall",
                    status=status,
                    payload={"source_unregister_task_id": 42},
                    watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
                    correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
                    correlation_id=f"remove:{self.node.id}",
                )

                queued = queue_detached_remove_verification(node_task=task)

                self.assertTrue(queued)
                queue_parent.assert_called_once_with(task_id=42)
                queue_parent.reset_mock()

    @patch("apps.source.tasks.source_unregister.queue_source_unregister_task")
    def test_successful_remove_waits_for_signed_completion_callback(self, queue_parent):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.SUCCESS,
            payload={"source_unregister_task_id": 42},
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )

        queued = queue_detached_remove_verification(node_task=task)

        self.assertFalse(queued)
        queue_parent.assert_not_called()

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_pending_remove_does_not_finalize_when_ws_gone(self, _routable):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.PENDING,
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )
        summary = advance_node_lifecycle(org=self.org, node=self.node, user=self.user)
        self.assertIsNone(summary)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_deleted)

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_upgrade_active_detached_task_shows_restarting_when_ws_gone(self, _routable):
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            result={
                "target_version": "1.2.0",
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertEqual(lifecycle["state"], "restarting")

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_running_without_detached_marker_stays_upgrading(self, _routable):
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            result={"target_version": "1.2.0"},
            watchdog_deadline_at=timezone.now(),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertEqual(lifecycle["state"], "upgrading")

    @patch("apps.node.services.internal.node_lifecycle.agent_ws_routable", return_value=False)
    def test_remove_active_detached_shows_removing_before_finalize_window(self, _routable):
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.RUNNING,
            result={
                "mode": "local_detached",
                "detached_at": timezone.now().isoformat(),
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )
        lifecycle = compute_node_lifecycle(org=self.org, node=self.node)
        self.assertEqual(lifecycle["state"], "removing")

    def test_complete_task_running_extends_watchdog_for_detached(self):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.PENDING,
            watchdog_deadline_at=timezone.now(),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
        )
        before = timezone.now()
        updated = complete_task(
            task_id=task.id,
            node_id=self.node.id,
            status="running",
            result={
                "mode": "local_detached",
                "target_version": "1.2.0",
            },
        )
        self.assertEqual(updated.status, NodeTask.Status.RUNNING)
        self.assertGreater(
            updated.watchdog_deadline_at,
            before + timezone.timedelta(seconds=node_conf.TASK_WATCHDOG_SECONDS),
        )
        self.assertIn("detached_at", updated.result or {})
