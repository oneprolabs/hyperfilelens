"""Recovery capabilities, legacy configuration, and incident matching."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.alert.api.serializers.policy import AlertPolicySerializer
from apps.alert.constants import AlertStatus, AlertType, ResourceType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.evaluation import _evaluate_availability_policy
from apps.alert.services.internal.lifecycle import resolve_alert
from apps.alert.services.internal.recovery import supports_numeric_recovery
from apps.alert.services.internal.task_events import recover_task_alerts
from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.task.models import Task


class RecoveryCapabilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="recovery-capabilities", name="Recovery")
        self.policy = AlertPolicy.objects.create(
            organization=self.org,
            name="Heartbeat",
            type=AlertType.AVAILABILITY,
            resource_type=ResourceType.SYNC_PROXY,
            scope="selected",
            trigger_rule={"check_type": "heartbeat", "timeout_seconds": 300, "duration_seconds": 0},
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="recovery-node",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            last_seen_at=timezone.now(),
        )
        self.policy.resource_ids = [str(self.node.id)]
        self.policy.save()

    def record(self, *, policy=None, resource_id=None, key="heartbeat", **overrides):
        policy = policy or self.policy
        resource_id = str(self.node.id) if resource_id is None else str(resource_id)
        values = {
            "organization": self.org,
            "policy_id": policy.id,
            "type": policy.type,
            "severity": "warning",
            "resource_type": policy.resource_type,
            "resource_id": resource_id,
            "title": "Recovery test",
            "status": AlertStatus.FIRING,
            "fingerprint": f"{policy.id}|{self.org.id}|{resource_id}|{key}",
        }
        values.update(overrides)
        return AlertRecord.objects.create(**values)

    def test_capability_depends_on_system_check(self):
        for alert_type, rule, expected in [
            (AlertType.METRIC, {}, True),
            (AlertType.SYSTEM, {"check_type": "disk_space_low"}, True),
            (AlertType.SYSTEM, {"check_type": "service_health", "metric_key": "cpu_usage"}, True),
            (AlertType.SYSTEM, {"check_type": "service_health"}, False),
            (AlertType.TASK, {}, False),
            (AlertType.EVENT, {}, False),
            (AlertType.AVAILABILITY, {"check_type": "heartbeat"}, False),
        ]:
            with self.subTest(alert_type=alert_type, rule=rule):
                self.assertEqual(supports_numeric_recovery(alert_type, rule), expected)

    @patch("apps.alert.services.internal.lifecycle.send_resolved_notification")
    def test_heartbeat_recovers_historical_and_default_keys_once(self, notify):
        historical = self.record()
        legacy = self.record(key="default", status=AlertStatus.ACKNOWLEDGED)
        unrelated = self.record(key="cpu_usage")
        _evaluate_availability_policy(self.policy)
        _evaluate_availability_policy(self.policy)
        for record in (historical, legacy):
            record.refresh_from_db()
            self.assertEqual(record.status, AlertStatus.RESOLVED)
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.status, AlertStatus.FIRING)
        self.assertEqual(notify.call_count, 2)

    def test_heartbeat_recovery_respects_disabled_switch_but_manual_resolution_works(self):
        self.policy.recovery_rule = {"enabled": False, "notify_on_resolved": False}
        self.policy.save()
        alert = self.record()
        _evaluate_availability_policy(self.policy)
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.FIRING)
        resolve_alert(alert)
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED)

    def test_online_status_with_stale_heartbeat_does_not_resolve(self):
        alert = self.record()
        self.node.last_seen_at = timezone.now() - timedelta(hours=1)
        self.node.save()
        _evaluate_availability_policy(self.policy)
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.FIRING)

    def test_missing_heartbeat_does_not_resolve(self):
        alert = self.record()
        self.node.last_seen_at = None
        self.node.save()
        _evaluate_availability_policy(self.policy)
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.FIRING)

    def test_heartbeat_does_not_resolve_mismatched_records(self):
        other_org = Organization.objects.create(key="other-recovery-org", name="Other")
        alerts = [
            self.record(type=AlertType.METRIC),
            self.record(resource_type=ResourceType.AGENT_PROXY),
            self.record(organization=other_org),
            self.record(resource_id=str(self.node.id + 1)),
            self.record(policy_id=None),
        ]
        _evaluate_availability_policy(self.policy)
        for alert in alerts:
            alert.refresh_from_db()
            self.assertEqual(alert.status, AlertStatus.FIRING)

    def task_policy(self, recovery_rule):
        return AlertPolicy.objects.create(
            organization=self.org,
            name="Backup failure",
            type=AlertType.TASK,
            resource_type=ResourceType.TASK,
            scope="all",
            trigger_rule={"task_type": "backup", "event_type": "task_failed"},
            recovery_rule=recovery_rule,
        )

    def test_task_recovery_respects_opt_out_and_keeps_legacy_default(self):
        task = Task.objects.create(
            organization_id=self.org.id, task_type=Task.Type.BACKUP, status=Task.Status.SUCCESS,
        )
        enabled = self.task_policy(None)
        disabled = self.task_policy({"enabled": False})
        matching = self.record(policy=enabled, resource_id=task.id)
        opted_out = self.record(policy=disabled, resource_id=task.id)
        wrong_type = self.record(policy=enabled, resource_id=task.id, type=AlertType.METRIC)
        orphan = self.record(policy=enabled, resource_id=task.id, policy_id=None)
        wrong_uuid = self.record(
            policy=enabled, resource_id=task.id, metadata={"task_uuid": "another-task"},
        )
        other_task = self.record(policy=enabled, resource_id=task.id + 1)
        recover_task_alerts(task)
        matching.refresh_from_db()
        self.assertEqual(matching.status, AlertStatus.RESOLVED)
        for alert in (opted_out, wrong_type, orphan, wrong_uuid, other_task):
            alert.refresh_from_db()
            self.assertEqual(alert.status, AlertStatus.FIRING)

    def test_non_numeric_legacy_fields_are_accepted_and_preserved(self):
        for alert_type, trigger in [
            (AlertType.TASK, {"task_type": "backup", "event_type": "task_failed"}),
            (AlertType.EVENT, {"event_category": "configuration", "event_types": ["alert_policy_changed"]}),
            (AlertType.AVAILABILITY, {"check_type": "heartbeat", "timeout_seconds": 300, "duration_seconds": 0}),
            (AlertType.SYSTEM, {"check_type": "service_health", "duration_seconds": 0}),
        ]:
            recovery = {"enabled": True, "operator": "legacy", "threshold": "unused", "duration_seconds": "unused"}
            serializer = AlertPolicySerializer(data={
                "name": "Legacy", "severity": "warning", "type": alert_type, "resource_type": "system", "scope": "all",
                "trigger_rule": trigger, "recovery_rule": recovery,
            })
            with self.subTest(alert_type=alert_type):
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(serializer.validated_data["recovery_rule"], recovery)

    def test_numeric_recovery_still_validates_new_settings(self):
        serializer = AlertPolicySerializer(data={
            "name": "CPU", "severity": "warning", "type": "metric", "resource_type": "system", "scope": "all",
            "trigger_rule": {"metric_key": "cpu_usage", "operator": ">", "threshold": 80, "duration_seconds": 0},
            "recovery_rule": {"enabled": True, "operator": "invalid"},
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("recovery_rule", serializer.errors)

    def test_unrelated_edit_preserves_null_recovery_and_managed_fields(self):
        self.policy.trigger_rule["managed"] = "custom-managed-rule"
        self.policy.save()
        serializer = AlertPolicySerializer(self.policy, data={"name": "Renamed"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.policy.refresh_from_db()
        self.assertIsNone(self.policy.recovery_rule)
        self.assertEqual(self.policy.trigger_rule["managed"], "custom-managed-rule")

    def test_unrelated_edit_does_not_revalidate_unchanged_legacy_numeric_recovery(self):
        self.policy.type = AlertType.METRIC
        self.policy.resource_type = ResourceType.SYSTEM
        self.policy.scope = "all"
        self.policy.trigger_rule = {"metric_key": "cpu_usage", "operator": ">", "threshold": 80, "duration_seconds": 0}
        self.policy.recovery_rule = {"enabled": True, "operator": "legacy"}
        self.policy.save()
        serializer = AlertPolicySerializer(self.policy, data={
            "name": "Renamed", "trigger_rule": self.policy.trigger_rule,
            "recovery_rule": self.policy.recovery_rule,
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_notification_or_interval_edit_does_not_revalidate_legacy_numeric_fields(self):
        self.policy.type = AlertType.METRIC
        self.policy.resource_type = ResourceType.SYSTEM
        self.policy.scope = "all"
        self.policy.trigger_rule = {"metric_key": "cpu_usage", "operator": ">", "threshold": 80, "duration_seconds": 0}
        self.policy.recovery_rule = {"enabled": True, "operator": "legacy", "notify_on_resolved": True}
        self.policy.save()
        serializer = AlertPolicySerializer(self.policy, data={
            "trigger_rule": {**self.policy.trigger_rule, "evaluation_interval_seconds": 60},
            "recovery_rule": {**self.policy.recovery_rule, "notify_on_resolved": False},
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.policy.refresh_from_db()
        self.assertFalse(self.policy.recovery_rule["notify_on_resolved"])
        self.assertEqual(self.policy.recovery_rule["operator"], "legacy")

    def test_new_task_rules_default_to_manual_resolution_at_api_boundary(self):
        serializer = AlertPolicySerializer(data={
            "name": "New backup rule", "severity": "warning", "type": "task",
            "resource_type": "task", "scope": "all",
            "trigger_rule": {"task_type": "backup", "event_type": "task_failed"},
            "recovery_rule": {"notify_on_firing": False, "notify_on_resolved": True},
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["recovery_rule"], {
            "enabled": False, "notify_on_firing": False, "notify_on_resolved": True,
        })

    def test_task_recovery_does_not_cross_org_policy_or_operation_boundaries(self):
        task = Task.objects.create(
            organization_id=self.org.id, task_type=Task.Type.BACKUP, status=Task.Status.SUCCESS,
        )
        policy = self.task_policy({"enabled": True})
        other_org = Organization.objects.create(key="other-task-org", name="Other")
        other_policy = self.task_policy({"enabled": True})
        other_policy.organization = other_org
        other_policy.save()
        operation_policy = self.task_policy({"enabled": True})
        operation_policy.trigger_rule["operation_type"] = "maintenance_full"
        operation_policy.save()
        alerts = [
            self.record(policy=other_policy, resource_id=task.id),
            self.record(policy=policy, resource_id=task.id, organization=other_org),
            self.record(policy=operation_policy, resource_id=task.id),
            self.record(policy=policy, resource_id=task.id, resource_type=ResourceType.SYSTEM),
        ]
        recover_task_alerts(task)
        for alert in alerts:
            alert.refresh_from_db()
            self.assertEqual(alert.status, AlertStatus.FIRING)

    def test_deleted_node_does_not_auto_resolve(self):
        alert = self.record()
        self.node.delete()
        _evaluate_availability_policy(self.policy)
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.FIRING)

    def test_task_recovery_preserves_legacy_policy_resource_type(self):
        task = Task.objects.create(
            organization_id=self.org.id, task_type=Task.Type.BACKUP, status=Task.Status.SUCCESS,
        )
        policy = self.task_policy(None)
        policy.resource_type = ResourceType.SYSTEM
        policy.save()
        alert = self.record(policy=policy, resource_id=task.id)
        recover_task_alerts(task)
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED)
