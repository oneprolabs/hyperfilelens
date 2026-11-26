"""Late reconnect recovery must not reopen or overwrite lifecycle history."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from threading import Event, Thread
from unittest.mock import Mock, patch

from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal.node_lifecycle import (
    _build_upgrade_timeline,
    _upgrade_lifecycle_payload,
    advance_node_lifecycle,
    record_upgrade_disconnect,
    record_upgrade_session_observation,
)
from apps.node.services.internal.node_upgrade_recovery import (
    advance_failed_upgrade_recovery,
    project_reconnect_timeout_upgrade,
    queue_failed_upgrade_recovery,
    reconnect_timeout_recovery_node_ids,
    upgrade_recovery_verified,
)
from apps.node.services.internal.task import (
    _update_node_lifecycle_status,
    complete_task,
    project_node_lifecycle_task,
)
from apps.node.tasks.lifecycle import advance_active_lifecycle_nodes
from apps.node.ws.uplink import _handle_task_result_delivery
from apps.node.ws.wire import ParsedUplink, WireType
from apps.task.models import Task

_LIFECYCLE = "apps.node.services.internal.node_lifecycle"
_RECOVERY = "apps.node.services.internal.node_upgrade_recovery"


class RecoveryFixture:
    def create_fixture(self):
        # Only the recovery module's clock is controlled. Model timestamps and
        # Django's shared timezone module keep their normal behavior.
        clock_patch = patch(f"{_RECOVERY}.timezone", wraps=timezone)
        self.recovery_clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.recovery_clock.now.return_value = timezone.now()
        self.org = Organization.objects.create(key="late-upgrade", name="Late Upgrade")
        self.node = Node.objects.create(
            organization=self.org,
            name="late-proxy",
            role=NodeRole.PROXY,
            status=Node.Status.UPGRADE_FAILED,
            availability=Node.Availability.ONLINE,
            version="1.0.0",
        )
        self.failed_at = timezone.now() - timedelta(minutes=10)
        self.task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.FAILED,
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.pk}",
            payload={
                "target_version": "1.2.0",
                "target_commit": "abc123",
                "pre_upgrade_session_id": "old",
            },
            result={
                "mode": "local_detached",
                "detached_at": (self.failed_at - timedelta(minutes=10)).isoformat(),
                "failure_code": "AGENT_RECONNECT_TIMEOUT",
                "failed_at": self.failed_at.isoformat(),
                "lifecycle_terminal_sealed": True,
                "lifecycle_failure_audited": True,
            },
            last_error="Agent did not reconnect after the upgrade.",
            watchdog_deadline_at=self.failed_at,
        )

    def success(self):
        return complete_task(
            task_id=self.task.pk,
            node_id=self.node.pk,
            status="success",
            result={"mode": "local_detached", "host_upgrade_status": "success"},
        )

    def observe(self, *, session="new", inventory=None):
        return record_upgrade_session_observation(
            node_id=self.node.pk,
            session_id=session,
            inventory=inventory
            if inventory is not None
            else {
                "agent_version": "1.2.0",
                "agent_commit": "abc123",
            },
        )

    def advance(self):
        return advance_failed_upgrade_recovery(
            node_id=self.node.pk,
            organization_id=self.org.pk,
        )

    def expire_stable_clock(self):
        self.recovery_clock.now.return_value += timedelta(
            seconds=node_conf.UPGRADE_STABLE_SECONDS + 1,
        )

    def ready_to_recover(self):
        self.success()
        self.observe()
        self.assertFalse(self.advance())
        self.expire_stable_clock()


class NodeUpgradeRecoveryTests(RecoveryFixture, TestCase):
    def setUp(self):
        self.create_fixture()
        self.session = patch(
            f"{_LIFECYCLE}._current_agent_session", return_value="new"
        ).start()
        self.route = patch(f"{_LIFECYCLE}.agent_ws_routable", return_value=True).start()
        self.addCleanup(patch.stopall)
        patch(
            "apps.node.services.internal.redis_store.get_redis", return_value=None
        ).start()

    def test_late_success_restores_node_but_preserves_terminal_history(self):
        parent = Task.objects.create(
            organization_id=self.org.pk,
            task_type=Task.Type.NODE_LIFECYCLE,
            status=Task.Status.FAILED,
            display_name="Upgrade",
        )
        self.task.parent_task = parent
        self.task.save(update_fields=["parent_task", "updated_at"])
        original = deepcopy(self.task.result)
        self.ready_to_recover()

        with patch(
            "apps.node.services.internal.task._sync_agent_source_pipeline_status"
        ) as sync:
            self.assertTrue(self.advance())
        sync.assert_called_once_with(node_id=self.node.pk)
        self.node.refresh_from_db()
        self.task.refresh_from_db()
        parent.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.ACTIVE)
        self.assertEqual(self.task.status, NodeTask.Status.FAILED)
        self.assertEqual(parent.status, Task.Status.FAILED)
        self.assertEqual(
            self.task.last_error, "Agent did not reconnect after the upgrade."
        )
        for key, value in original.items():
            self.assertEqual(self.task.result[key], value)
        self.assertTrue(upgrade_recovery_verified(self.task))
        self.assertEqual(Node.objects.count(), 1)
        self.assertEqual(NodeTask.objects.count(), 1)
        audit = AuditLog.objects.get(action="node.lifecycle.upgrade.recovered")
        self.assertEqual(audit.metadata["failed_at"], self.failed_at.isoformat())

    def test_inventory_before_success_is_retained_without_early_healing(self):
        self.assertTrue(self.observe())
        self.assertFalse(self.advance())
        self.task.refresh_from_db()
        self.assertNotIn("verify_started_at", self.task.result["upgrade_recovery"])
        self.success()
        self.assertFalse(self.advance())
        self.expire_stable_clock()
        self.assertTrue(self.advance())

    def test_success_before_inventory_waits_for_version_evidence(self):
        self.success()
        self.assertFalse(self.advance())
        record_upgrade_session_observation(node_id=self.node.pk, session_id="new")
        self.assertFalse(self.advance())
        self.observe()
        self.assertFalse(self.advance())
        self.expire_stable_clock()
        self.assertTrue(self.advance())

    def test_host_success_recorded_before_timeout_also_qualifies(self):
        self.task.result["host_upgrade_status"] = "success"
        self.task.save(update_fields=["result", "updated_at"])
        self.observe()
        self.assertFalse(self.advance())
        self.expire_stable_clock()
        self.assertTrue(self.advance())

    def test_duplicate_success_does_not_refresh_history_or_embed_itself(self):
        self.ready_to_recover()
        self.assertTrue(self.advance())
        self.task.refresh_from_db()
        result = deepcopy(self.task.result)
        updated_at = self.task.updated_at
        replay = {**result, "host_upgrade_status": "success"}

        with (
            self.captureOnCommitCallbacks(execute=True),
            patch(f"{_RECOVERY}.queue_failed_upgrade_recovery") as wake,
        ):
            updated = complete_task(
                task_id=self.task.pk,
                node_id=self.node.pk,
                status="success",
                result=replay,
            )

        wake.assert_called_once_with(node_id=self.node.pk)
        self.task.refresh_from_db()
        self.assertTrue(updated._late_result_ignored)
        self.assertEqual(self.task.result, result)
        self.assertEqual(self.task.updated_at, updated_at)
        self.assertFalse(self.advance())
        self.assertEqual(
            AuditLog.objects.filter(action="node.lifecycle.upgrade.recovered").count(),
            1,
        )
        self.assertNotIn("late_result", self.task.result["late_result"]["result"])
        self.assertNotIn("upgrade_recovery", self.task.result["late_result"]["result"])

    def test_wire_result_is_acked_and_wakes_only_after_commit(self):
        message = ParsedUplink(
            msg_type=WireType.TASK_RESULT,
            task_id=str(self.task.pk),
            status="success",
            result={"repaired_after_restart": True},
        )
        with patch(f"{_RECOVERY}.queue_failed_upgrade_recovery") as wake:
            with self.captureOnCommitCallbacks(execute=True):
                receipt = _handle_task_result_delivery(
                    node_id=self.node.pk,
                    message=message,
                    session_id="new",
                )
                wake.assert_not_called()
            wake.assert_called_once_with(node_id=self.node.pk)
        self.assertTrue(receipt.acknowledge_agent)
        self.assertEqual(receipt.node_task.status, NodeTask.Status.FAILED)
        self.assertTrue(receipt.node_task._late_result_ignored)

    def test_wire_result_cannot_supply_control_plane_recovery_marker(self):
        result = deepcopy(self.task.result)
        for key in ("failure_code", "lifecycle_terminal_sealed", "failed_at"):
            result.pop(key, None)
        self.task.status = NodeTask.Status.RUNNING
        self.task.result = result
        self.task.save(update_fields=["status", "result", "updated_at"])
        self.session.return_value = "old"
        _handle_task_result_delivery(
            node_id=self.node.pk,
            session_id="old",
            message=ParsedUplink(
                msg_type=WireType.TASK_RESULT,
                task_id=str(self.task.pk),
                status="running",
                result={
                    "mode": "local_detached",
                    "upgrade_recovery": {
                        "task_id": str(self.task.pk),
                        "verified_at": timezone.now().isoformat(),
                        "observed_agent_version": "1.2.0",
                        "observed_agent_commit": "abc123",
                        "detached_session_id": "untrusted-old",
                        "reconnect_session_id": "untrusted-new",
                        "inventory_session_id": "untrusted-new",
                    },
                },
            ),
        )
        self.task.refresh_from_db()
        self.assertNotIn("upgrade_recovery", self.task.result)

        advance_node_lifecycle(org=self.org, node=self.node)

        self.node.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, NodeTask.Status.FAILED)
        self.assertEqual(self.task.result["failure_code"], "AGENT_RECONNECT_TIMEOUT")
        self.assertEqual(self.node.status, Node.Status.UPGRADE_FAILED)
        self.assertFalse(upgrade_recovery_verified(self.task))

    def test_all_wire_upgrade_result_statuses_strip_reserved_recovery_field(self):
        for status in ("running", "success", "failed", "canceled"):
            for marker in (True, None, "untrusted", {"verified_at": "untrusted"}):
                with self.subTest(status=status, marker=marker):
                    NodeTask.objects.filter(pk=self.task.pk).update(
                        status=NodeTask.Status.RUNNING,
                        result={"mode": "local_detached"},
                    )
                    _handle_task_result_delivery(
                        node_id=self.node.pk,
                        message=ParsedUplink(
                            msg_type=WireType.TASK_RESULT,
                            task_id=str(self.task.pk),
                            status=status,
                            result={
                                "mode": "local_detached",
                                "upgrade_recovery": marker,
                            },
                        ),
                    )
                    self.task.refresh_from_db()
                    self.assertNotIn("upgrade_recovery", self.task.result)

    def test_wire_duplicate_success_cannot_overwrite_trusted_recovery(self):
        self.ready_to_recover()
        self.assertTrue(self.advance())
        self.task.refresh_from_db()
        trusted = deepcopy(self.task.result["upgrade_recovery"])
        _handle_task_result_delivery(
            node_id=self.node.pk,
            message=ParsedUplink(
                msg_type=WireType.TASK_RESULT,
                task_id=str(self.task.pk),
                status="success",
                result={"upgrade_recovery": {"verified_at": "untrusted"}},
            ),
        )
        self.task.refresh_from_db()
        self.node.refresh_from_db()
        self.assertEqual(self.task.result["upgrade_recovery"], trusted)
        self.assertEqual(self.task.status, NodeTask.Status.FAILED)
        self.assertEqual(self.node.status, Node.Status.ACTIVE)
        self.assertEqual(
            AuditLog.objects.filter(action="node.lifecycle.upgrade.recovered").count(),
            1,
        )

    def test_partial_inventory_preserves_same_session_build_and_clock(self):
        self.ready_to_recover()
        self.task.refresh_from_db()
        before = deepcopy(self.task.result["upgrade_recovery"])
        self.observe(inventory={"agent_version": "1.2.0"})
        self.observe(inventory={"metrics": {}})
        self.task.refresh_from_db()
        self.assertEqual(self.task.result["upgrade_recovery"], before)
        self.assertTrue(self.advance())

    def test_wrong_or_missing_build_does_not_heal(self):
        self.success()
        for inventory in (
            {"agent_version": "1.1.0", "agent_commit": "abc123"},
            {"agent_version": "1.2.0", "agent_commit": "other"},
            {"agent_version": "1.3.0"},
            {"agent_commit": "abc123"},
        ):
            with self.subTest(inventory=inventory):
                # Each case starts a new session, so omitted fields cannot
                # inherit a complete identity from a previous case.
                session = str(inventory)
                self.session.return_value = session
                self.observe(session=session, inventory=inventory)
                self.assertFalse(self.advance())
                self.task.refresh_from_db()
                self.assertNotIn(
                    "verify_started_at", self.task.result["upgrade_recovery"]
                )

    def test_no_commit_target_can_recover_and_suppress_failure_banner(self):
        self.task.payload.pop("target_commit")
        self.task.save(update_fields=["payload", "updated_at"])
        self.success()
        self.observe(inventory={"agent_version": "1.2.0"})
        self.advance()
        self.expire_stable_clock()
        self.assertTrue(self.advance())
        self.task.refresh_from_db()
        self.node.refresh_from_db()
        self.assertIsNone(
            _upgrade_lifecycle_payload(org=self.org, node=self.node, task=self.task)
        )
        project_node_lifecycle_task(node_task=self.task)
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.ACTIVE)

    def test_stale_or_original_session_cannot_supply_evidence(self):
        self.success()
        self.assertFalse(self.observe(session="superseded"))
        self.session.return_value = "old"
        self.assertFalse(self.observe(session="old"))
        self.assertFalse(self.advance())
        self.task.refresh_from_db()
        self.assertNotIn("upgrade_recovery", self.task.result)

    def test_missing_pre_upgrade_session_is_not_inferred(self):
        self.task.payload.pop("pre_upgrade_session_id")
        self.task.save(update_fields=["payload", "updated_at"])
        self.success()
        self.assertFalse(self.observe())
        self.assertFalse(self.advance())

    def test_session_change_discards_old_inventory_and_stability(self):
        self.ready_to_recover()
        self.session.return_value = "newer"
        record_upgrade_session_observation(node_id=self.node.pk, session_id="newer")
        self.assertFalse(self.advance())
        self.task.refresh_from_db()
        recovery = self.task.result["upgrade_recovery"]
        self.assertNotIn("inventory_session_id", recovery)
        self.assertNotIn("verify_started_at", recovery)
        self.observe(session="newer")
        self.assertFalse(self.advance())
        self.expire_stable_clock()
        self.assertTrue(self.advance())

    def test_disconnect_resets_stability_without_changing_failure(self):
        self.ready_to_recover()
        self.assertTrue(record_upgrade_disconnect(node_id=self.node.pk))
        self.task.refresh_from_db()
        self.assertNotIn("verify_started_at", self.task.result["upgrade_recovery"])
        self.assertEqual(self.task.result["failure_code"], "AGENT_RECONNECT_TIMEOUT")
        self.assertFalse(self.advance())

    def test_unroutable_or_offline_node_resets_stability(self):
        self.ready_to_recover()
        self.route.return_value = False
        self.assertFalse(self.advance())
        self.task.refresh_from_db()
        self.assertNotIn("verify_started_at", self.task.result["upgrade_recovery"])
        self.route.return_value = True
        self.advance()
        self.expire_stable_clock()
        Node.objects.filter(pk=self.node.pk).update(
            availability=Node.Availability.OFFLINE
        )
        self.assertFalse(self.advance())
        self.task.refresh_from_db()
        self.assertNotIn("verify_started_at", self.task.result["upgrade_recovery"])

    def test_newer_lifecycle_operation_supersedes_old_recovery(self):
        self.ready_to_recover()
        for kind, status in (
            ("agent.upgrade", NodeTask.Status.RUNNING),
            ("agent.upgrade", NodeTask.Status.FAILED),
            ("agent.uninstall", NodeTask.Status.SUCCESS),
            ("agent.uninstall", NodeTask.Status.CANCELED),
        ):
            with self.subTest(kind=kind, status=status):
                newer = NodeTask.objects.create(
                    organization=self.org,
                    node=self.node,
                    kind=kind,
                    status=status,
                    watchdog_deadline_at=timezone.now(),
                    correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
                )
                self.assertFalse(self.advance())
                self.assertEqual(list(reconnect_timeout_recovery_node_ids()), [])
                newer.delete()

    def test_wrong_org_deleted_node_and_actual_failure_do_not_recover(self):
        self.ready_to_recover()
        other = Organization.objects.create(key="other", name="Other")
        self.assertFalse(
            advance_failed_upgrade_recovery(
                node_id=self.node.pk,
                organization_id=other.pk,
            )
        )
        for code in (
            "HOST_UPGRADE_FAILED",
            "TARGET_BUILD_MISMATCH",
            "AGENT_PACKAGE_DOWNLOAD_INTERRUPTED",
        ):
            self.task.refresh_from_db()
            self.task.result["failure_code"] = code
            self.task.save(update_fields=["result", "updated_at"])
            self.assertFalse(self.advance())
        self.task.result["failure_code"] = "AGENT_RECONNECT_TIMEOUT"
        self.task.save(update_fields=["result", "updated_at"])
        Node.objects.filter(pk=self.node.pk).update(is_deleted=True)
        self.assertFalse(self.advance())

    def test_canceled_uninstall_and_gateway_do_not_use_new_recovery(self):
        self.ready_to_recover()
        for field, value in (
            ("status", NodeTask.Status.CANCELED),
            ("kind", "agent.uninstall"),
        ):
            original = getattr(self.task, field)
            setattr(self.task, field, value)
            self.task.save(update_fields=[field, "updated_at"])
            self.assertFalse(self.advance())
            setattr(self.task, field, original)
            self.task.save(update_fields=[field, "updated_at"])
        Node.objects.filter(pk=self.node.pk).update(role=NodeRole.GATEWAY)
        self.assertFalse(self.advance())

    def test_periodic_sweep_recovers_previously_persisted_late_result(self):
        self.ready_to_recover()
        self.assertEqual(list(reconnect_timeout_recovery_node_ids()), [self.node.pk])

        @contextmanager
        def lease(**kwargs):
            yield Mock(refresh=Mock(return_value=True))

        with patch(
            "apps.node.tasks.lifecycle.redis_store.periodic_lease", side_effect=lease
        ):
            result = advance_active_lifecycle_nodes.run()
        self.assertEqual(result["advanced"], 1)
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.ACTIVE)
        self.assertEqual(list(reconnect_timeout_recovery_node_ids()), [])

    def test_periodic_candidates_require_success_and_latest_task(self):
        self.observe()
        self.assertEqual(list(reconnect_timeout_recovery_node_ids()), [])
        self.success()
        self.assertEqual(list(reconnect_timeout_recovery_node_ids()), [self.node.pk])

    def test_existing_lifecycle_entry_point_advances_recovery(self):
        self.ready_to_recover()
        advance_node_lifecycle(org=self.org, node=self.node)
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.ACTIVE)

    def test_timeline_keeps_failed_at_after_evidence_updates(self):
        self.success()
        self.observe()
        self.advance()
        self.task.refresh_from_db()
        timeline = _build_upgrade_timeline(node=self.node, task=self.task)
        self.assertEqual(timeline[-1]["at"], self.failed_at.isoformat())

    def test_newer_operation_is_not_overwritten_by_recovered_projection(self):
        self.ready_to_recover()
        self.assertTrue(self.advance())
        self.task.refresh_from_db()
        newer = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            status=NodeTask.Status.RUNNING,
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            watchdog_deadline_at=timezone.now(),
        )
        project_node_lifecycle_task(node_task=newer)
        project_node_lifecycle_task(node_task=self.task)
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.REMOVING)

    def test_stale_python_task_cannot_reproject_recovered_node_as_failed(self):
        self.ready_to_recover()
        stale_task = NodeTask.objects.get(pk=self.task.pk)
        self.assertTrue(self.advance())
        self.assertFalse(upgrade_recovery_verified(stale_task))
        project_node_lifecycle_task(node_task=stale_task)
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.ACTIVE)

    def test_recovery_audit_failure_rolls_back_node_and_marker(self):
        self.ready_to_recover()
        with patch(
            f"{_RECOVERY}.write_audit_log", side_effect=RuntimeError("audit failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                self.advance()
        self.node.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.UPGRADE_FAILED)
        self.assertFalse(upgrade_recovery_verified(self.task))
        self.assertTrue(self.advance())

    def test_stable_clock_does_not_finish_early(self):
        self.success()
        self.observe()
        self.assertFalse(self.advance())
        self.recovery_clock.now.return_value += timedelta(
            seconds=node_conf.UPGRADE_STABLE_SECONDS - 1,
        )
        self.assertFalse(self.advance())
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.UPGRADE_FAILED)
        self.recovery_clock.now.return_value += timedelta(seconds=1)
        self.assertTrue(self.advance())

    def test_agent_role_uses_the_same_recovery(self):
        Node.objects.filter(pk=self.node.pk).update(role=NodeRole.AGENT)
        self.ready_to_recover()
        self.assertTrue(self.advance())

    def test_task_owner_mismatch_is_not_recovered(self):
        self.ready_to_recover()
        other = Organization.objects.create(key="task-owner", name="Other")
        NodeTask.objects.filter(pk=self.task.pk).update(organization=other)
        self.assertFalse(self.advance())

    def test_wakeup_is_discarded_when_result_transaction_rolls_back(self):
        with patch(f"{_RECOVERY}.queue_failed_upgrade_recovery") as wake:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaisesRegex(RuntimeError, "rollback"):
                    with transaction.atomic():
                        self.success()
                        raise RuntimeError("rollback")
            wake.assert_not_called()
        self.task.refresh_from_db()
        self.assertNotIn("late_result", self.task.result)

    def test_wakeup_failure_does_not_break_result_delivery(self):
        with (
            patch(
                "apps.node.services.internal.redis_store.claim_lifecycle_advance_event",
                return_value=True,
            ),
            patch(
                "apps.node.tasks.lifecycle.advance_node_lifecycle_for_node.apply_async",
                side_effect=RuntimeError("broker unavailable"),
            ),
        ):
            queue_failed_upgrade_recovery(node_id=self.node.pk)


class NodeUpgradeRecoveryLockTests(RecoveryFixture, TransactionTestCase):
    """Exercise real PostgreSQL row locks rather than mocked querysets."""

    def setUp(self):
        self.create_fixture()
        self.addCleanup(patch.stopall)
        patch(f"{_LIFECYCLE}._current_agent_session", return_value="new").start()
        patch(f"{_LIFECYCLE}.agent_ws_routable", return_value=True).start()
        patch(
            "apps.node.services.internal.redis_store.get_redis", return_value=None
        ).start()
        patch(f"{_RECOVERY}.queue_failed_upgrade_recovery").start()
        self.ready_to_recover()

    @contextmanager
    def locked_row(self, model, pk, *, when_locked=None):
        acquired, release = Event(), Event()
        errors = []

        def hold():
            close_old_connections()
            try:
                with transaction.atomic():
                    model.objects.select_for_update().get(pk=pk)
                    if when_locked:
                        when_locked()
                    acquired.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("test lock was not released")
            except Exception as exc:
                errors.append(exc)
                acquired.set()
            finally:
                connection.close()

        thread = Thread(target=hold)
        thread.start()
        try:
            self.assertTrue(acquired.wait(timeout=5))
            self.assertEqual(errors, [])
            yield
        finally:
            release.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    @contextmanager
    def paused_projection(self, target, original):
        reached, resume = Event(), Event()
        errors = []

        def pause(*args, **kwargs):
            reached.set()
            if not resume.wait(timeout=10):
                raise TimeoutError("projection test was not resumed")
            return original(*args, **kwargs)

        def project():
            close_old_connections()
            try:
                # Intentionally no outer transaction: the projection service
                # must provide its own node fence for standalone callers.
                with patch(target, side_effect=pause):
                    project_node_lifecycle_task(
                        node_task=NodeTask.objects.get(pk=self.task.pk),
                    )
            except Exception as exc:
                errors.append(exc)
                reached.set()
            finally:
                connection.close()

        thread = Thread(target=project)
        thread.start()
        try:
            self.assertTrue(reached.wait(timeout=5))
            self.assertEqual(errors, [])
            yield
        finally:
            resume.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_standalone_projection_rechecks_new_operations_under_node_fence(self):
        self.assertTrue(self.advance())
        for kind, task_status, node_status in (
            ("agent.uninstall", NodeTask.Status.RUNNING, Node.Status.REMOVING),
            ("agent.upgrade", NodeTask.Status.RUNNING, Node.Status.UPGRADING),
            ("agent.upgrade", NodeTask.Status.FAILED, Node.Status.UPGRADE_FAILED),
        ):
            with self.subTest(kind=kind, task_status=task_status):
                with self.paused_projection(
                    f"{_RECOVERY}.project_reconnect_timeout_upgrade",
                    project_reconnect_timeout_upgrade,
                ):
                    # Commit a new operation after the preliminary latest-id
                    # check, before the recovery projection acquires its fence.
                    with transaction.atomic():
                        Node.objects.select_for_update().get(pk=self.node.pk)
                        newer = NodeTask.objects.create(
                            organization=self.org,
                            node=self.node,
                            kind=kind,
                            status=task_status,
                            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
                            watchdog_deadline_at=timezone.now() + timedelta(hours=1),
                        )
                        Node.objects.filter(pk=self.node.pk).update(status=node_status)
                self.node.refresh_from_db()
                self.assertEqual(self.node.status, node_status)
                newer.delete()
                Node.objects.filter(pk=self.node.pk).update(status=Node.Status.ACTIVE)

    def test_standalone_projection_holds_node_fence_through_status_write(self):
        self.assertTrue(self.advance())
        with self.paused_projection(
            "apps.node.services.internal.task._update_node_lifecycle_status",
            _update_node_lifecycle_status,
        ):
            with transaction.atomic():
                available_node = (
                    Node.objects.select_for_update(skip_locked=True)
                    .filter(pk=self.node.pk)
                    .first()
                )
                self.assertIsNone(available_node)
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.ACTIVE)

    def test_recovery_projection_skips_busy_task_without_lock_inversion(self):
        self.assertTrue(self.advance())
        Node.objects.filter(pk=self.node.pk).update(status=Node.Status.UPGRADE_FAILED)
        with self.locked_row(NodeTask, self.task.pk):
            project_reconnect_timeout_upgrade(node_task=self.task)
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.UPGRADE_FAILED)
        project_reconnect_timeout_upgrade(node_task=self.task)
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.ACTIVE)

    def test_busy_node_is_skipped_then_recovers(self):
        with self.locked_row(Node, self.node.pk):
            self.assertFalse(self.advance())
        self.assertTrue(self.advance())

    def test_busy_task_is_skipped_without_lock_order_deadlock(self):
        with self.locked_row(NodeTask, self.task.pk):
            self.assertFalse(self.advance())
        self.assertTrue(self.advance())

    def test_new_operation_after_node_lock_wins_before_recovery(self):
        def create_new_operation():
            NodeTask.objects.create(
                organization=self.org,
                node=self.node,
                kind="agent.uninstall",
                status=NodeTask.Status.RUNNING,
                correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
                watchdog_deadline_at=timezone.now(),
            )

        with self.locked_row(Node, self.node.pk, when_locked=create_new_operation):
            self.assertFalse(self.advance())
        self.assertFalse(self.advance())
        self.node.refresh_from_db()
        self.assertEqual(self.node.status, Node.Status.UPGRADE_FAILED)
