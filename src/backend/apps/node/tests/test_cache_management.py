"""Cache policy, mixed-version dispatch and confirmed-check state contracts."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.iam.models import Membership, Organization
from apps.node.models import Node, NodeTask
from apps.node.services.internal.cache_management import (
    CAPABILITY, GIB_POLICY_CAPABILITY, DEFAULT_POLICY, GIB, MAX_BYTES, attach_cache_context,
    normalize_blocking, policy_for_node, project_cache_status, save_policy, snapshot, start_relocation,
    supports_cache_management,
)
from apps.node.services.internal.performance_policy import merge_performance_metadata
from apps.node.services.internal.repository_cache_policy import attach_node_kopia_cache_policy


class CacheContractTests(SimpleTestCase):
    def test_new_defaults_use_binary_gib_and_existing_policies_are_unchanged(self):
        self.assertEqual(DEFAULT_POLICY["blocking_bytes"], 10_737_418_240)
        self.assertEqual(DEFAULT_POLICY["warning_bytes"], 21_474_836_480)
        old = {"schema_version": 1, "revision": 3, "blocking_bytes": 10_000_000_000, "warning_bytes": 20_000_000_000}
        for source in ({"cache_policy": old}, {"inventory": {"cache_status": {"policy": old}}}):
            node = SimpleNamespace(role="agent", metadata=deepcopy(source))
            self.assertEqual(policy_for_node(node), old)
            self.assertEqual(node.metadata, source)
        legacy = SimpleNamespace(role="agent", metadata={"inventory": {"capabilities": [CAPABILITY]}})
        self.assertEqual(policy_for_node(legacy)["blocking_bytes"], 10_000_000_000)


    def test_applied_demand_cache_status_survives_an_incomplete_capability_inventory(self):
        from types import SimpleNamespace

        node = SimpleNamespace(
            role="agent",
            metadata={
                "inventory": {
                    "capabilities": [],
                    "cache_status": {
                        "mode": "demand_driven",
                        "application_state": "applied",
                        "policy": {**DEFAULT_POLICY, "revision": 4},
                    },
                },
            },
        )
        self.assertTrue(supports_cache_management(node))
        self.assertEqual(policy_for_node(node)["revision"], 4)

    def test_warning_is_always_blocking_plus_ten_and_units_are_exact(self):
        for value in (2, 10, 20, 21, 30, 50):
            self.assertEqual(normalize_blocking(value * GIB) + 10 * GIB, (value + 10) * GIB)
        for value in (0, -1, True, 1.5, "10", MAX_BYTES):
            with self.assertRaises(ValidationError):
                normalize_blocking(value)

    def test_old_agent_and_proxy_keep_current_configuration_and_payload(self):
        for role in ("agent", "proxy"):
            node = SimpleNamespace(role=role, metadata={"perf_settings": {"kopiaCacheMb": 0}})
            original = deepcopy(node.metadata)
            payload = attach_node_kopia_cache_policy(node=node, payload={"repository": {"type": "s3"}})
            self.assertEqual(payload["kopia_cache_size_mb"], 0)
            self.assertEqual(attach_cache_context(node=node, payload=payload, kind="backup.run", correlation_type="protection.backup"), payload)
            self.assertNotIn("cache_policy", payload)
            self.assertEqual(node.metadata, original)

    def test_new_dispatch_and_redelivery_remove_legacy_budget(self):
        node = SimpleNamespace(role="proxy", metadata={"inventory": {"capabilities": [CAPABILITY, GIB_POLICY_CAPABILITY]}})
        incoming = {"repository": {}, "kopia_cache_size_mb": 0, "session_id": "backup-one"}
        payload = attach_cache_context(node=node, payload=incoming, kind="repository.server.start", correlation_type="")
        self.assertNotIn("kopia_cache_size_mb", payload)
        self.assertEqual(payload["cache_policy"], DEFAULT_POLICY)
        self.assertEqual(payload["cache_use_context"], "backup")
        self.assertIn("kopia_cache_size_mb", incoming)

    def test_console_metadata_cannot_forge_cache_capability_or_status(self):
        current = {"inventory": {"capabilities": []}, "cache_policy": DEFAULT_POLICY, "perf_settings": {"kopiaCacheMb": 123}}
        result = merge_performance_metadata(current, {"inventory": {"capabilities": [CAPABILITY, GIB_POLICY_CAPABILITY]}, "cache_policy": {"blocking_bytes": 1}, "cache_status": {"mode": "demand_driven"}}, role="agent")
        self.assertEqual(result, current)


class CacheManagementTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="cache-management", name="Cache Management")
        # Alert evaluation now requires the real tenant Owner's live grants.
        # Construct that actor as the registration workflow does, without seeding
        # extra default rules into these narrowly scoped integration fixtures.
        from django.contrib.auth import get_user_model
        from common.extension_spi import get_authz_provider

        self.owner = get_user_model().objects.create_user(username="cache-owner")
        self.owner_membership = Membership.objects.create(
            organization=self.org, user=self.owner, role=Membership.Role.OWNER,
        )
        provider = get_authz_provider()
        if provider is not None:
            provider.sync_member_role(
                user_id=self.owner.pk, organization_id=self.org.pk, role="owner",
            )
        self.node = Node.objects.create(organization=self.org, name="Agent", role="agent", metadata={"inventory": {"capabilities": [CAPABILITY, GIB_POLICY_CAPABILITY]}, "perf_settings": {"cpuCores": 3, "kopiaCacheMb": 4096, "bandwidth": 11}})
        self.task = NodeTask.objects.create(watchdog_deadline_at=timezone.now()+timezone.timedelta(hours=1), organization=self.org, node=self.node, kind="backup.run", payload={})

    @patch("apps.node.ws.uplink.redis_store.is_agent_session_current", return_value=True)
    @patch("apps.node.services.internal.cache_management.start_task")
    def test_repeated_saves_survive_same_session_partial_heartbeats_but_not_a_downgrade(self, start, _current):
        from apps.node.ws.uplink import apply_heartbeat_inventory_snapshot

        self.node.metadata.update({"inventory_session_id": "gib-session", "inventory_capabilities_session_id": "gib-session"})
        self.node.save()
        for value in (12, 14):
            # This is the actual capability list shape of an affected ordinary
            # heartbeat after a full inventory advertised both cache flags.
            apply_heartbeat_inventory_snapshot(node_id=self.node.pk, session_id="gib-session", inventory={"capabilities": [CAPABILITY, "performance_policy_v1"]})
            self.node.refresh_from_db()
            self.assertTrue(snapshot(self.node)["policy_unit_supported"])
            save_policy(self.node, value * GIB)
            self.node.refresh_from_db()
            self.assertEqual(self.node.metadata["cache_policy"]["blocking_bytes"], value * GIB)
        self.assertEqual(start.call_count, 2)
        applied = deepcopy(self.node.metadata["cache_policy"])
        apply_heartbeat_inventory_snapshot(node_id=self.node.pk, session_id="downgraded-session", inventory={"capabilities": [CAPABILITY]})
        self.node.refresh_from_db()
        self.assertFalse(snapshot(self.node)["policy_unit_supported"])
        self.assertNotIn(GIB_POLICY_CAPABILITY, self.node.metadata["inventory"]["capabilities"])
        with self.assertRaises(ValidationError):
            save_policy(self.node, 16 * GIB)
        start.assert_called()
        self.assertEqual(start.call_count, 2)
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["cache_policy"], applied)

    @patch("apps.node.services.internal.cache_management.start_task")
    def test_older_binary_cannot_receive_an_unrecognized_gib_policy(self, start):
        self.node.metadata["inventory"]["capabilities"] = [CAPABILITY]
        self.node.save()
        before = deepcopy(self.node.metadata)
        with self.assertRaisesMessage(ValidationError, "Upgrade"):
            save_policy(self.node, 12 * GIB)
        start.assert_not_called()
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata, before)

    @patch("apps.node.services.internal.cache_management.start_task")
    def test_save_policy_preserves_resource_settings_and_stores_derived_revision(self, start):
        before = deepcopy(self.node.metadata["perf_settings"])
        save_policy(self.node, 2 * GIB)
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["perf_settings"], before)
        self.assertEqual(self.node.metadata["cache_policy"], {"schema_version": 1, "revision": 1, "blocking_bytes": 2 * GIB, "warning_bytes": 12 * GIB})
        self.assertEqual(start.call_args.args[1], "cache.policy.apply")
        self.assertEqual(snapshot(self.node)["application_state"], "pending")

    @patch("apps.node.services.internal.cache_management.start_task")
    def test_save_revision_exceeds_a_newer_agent_observation(self, start):
        self.node.metadata["cache_status"] = {"policy": {**DEFAULT_POLICY, "revision": 7}}
        self.node.save()
        save_policy(self.node, 3 * GIB)
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["cache_policy"]["revision"], 8)

    @patch("apps.node.services.internal.cache_management.start_task")
    def test_relocation_requires_successful_validation_on_same_node(self, start):
        import uuid
        with self.assertRaises(ValidationError):
            start_relocation(self.node, uuid.uuid4())
        other = Node.objects.create(organization=self.org, name="Other", role="agent", metadata=self.node.metadata)
        validation = NodeTask.objects.create(watchdog_deadline_at=timezone.now()+timezone.timedelta(hours=1), organization=self.org, node=other, kind="cache.relocation.validate", status="success", result={"validation_token": "token", "expires_at": (timezone.now() + timezone.timedelta(minutes=5)).isoformat(), "policy_revision": 0})
        with self.assertRaises(ValidationError):
            start_relocation(self.node, validation.pk)
        start.assert_not_called()

    def test_each_check_is_recorded_but_transport_redelivery_is_idempotent(self):
        from apps.monitor.models import OperationalEvent
        now = timezone.now()
        check = {"check_id": "check-1", "state": "warning", "sampled_at": now.isoformat(), "cache_root": "/cache-volume/hfl", "available_bytes": 15 * GIB, "policy": DEFAULT_POLICY}
        project_cache_status(self.task, {"cache_space_check": check})
        project_cache_status(self.task, {"cache_space_check": check})
        self.assertEqual(OperationalEvent.objects.filter(event_type="backup_space_check_warning").count(), 1)
        next_check = {**check, "check_id": "check-2", "sampled_at": (now + timezone.timedelta(seconds=1)).isoformat()}
        project_cache_status(self.task, {"cache_space_check": next_check})
        self.assertEqual(OperationalEvent.objects.filter(event_type="backup_space_check_warning").count(), 2)
        old_check = {**check, "check_id": "late-check", "state": "warning", "sampled_at": (now - timezone.timedelta(seconds=1)).isoformat()}
        project_cache_status(self.task, {"cache_space_check": old_check})
        self.assertEqual(OperationalEvent.objects.filter(event_type="backup_space_check_warning").count(), 3)
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["cache_space_check"]["check_id"], "check-2")

    @patch("apps.alert.services.internal.event_alerts.handle_platform_event")
    def test_one_backup_admits_one_warning_for_prepare_directories_and_snapshot_rechecks(self, alert):
        from apps.monitor.models import OperationalEvent
        from apps.task.models import Task

        parent = Task.objects.create(organization_id=self.org.id, task_type="backup", display_name="Backup")
        now = timezone.now()
        check = {"state": "warning", "sampled_at": now.isoformat(), "cache_root": "/cache",
                 "cache_filesystem_id": "disk-one", "available_bytes": 15 * GIB, "policy": DEFAULT_POLICY}
        sample = 0
        for kind, correlation, count in (("repository.policy.apply", "protection.backup.policy_prepare", 1),
                                         ("backup.snapshot.create", "protection.backup", 2)):
            for directory in range(3):
                child = NodeTask.objects.create(
                    node=self.node, organization=self.org, kind=kind,
                    correlation_type=correlation, correlation_id=str(parent.task_uuid),
                    watchdog_deadline_at=now + timezone.timedelta(hours=1),
                    payload={"backup_config_dir_id": directory},
                )
                for _ in range(count):
                    sample += 1
                    data = {**check, "check_id": f"check-{sample}", "sampled_at": (now + timezone.timedelta(seconds=sample)).isoformat()}
                    project_cache_status(child, {"cache_space_check": data})
                    project_cache_status(child, {"cache_space_check": data})  # Progress/result redelivery.
        self.assertEqual(sample, 9)
        event = OperationalEvent.objects.get(source="agent.cache_preflight")
        self.assertEqual(event.resource_id, str(parent.id))
        self.assertEqual(event.correlation_id, str(parent.task_uuid))
        self.assertEqual(event.metadata["task_id"], parent.id)
        alert.assert_called_once()
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["cache_space_check"]["check_id"], "check-9")
        # Warning admission must never suppress a later blocked check's projection.
        project_cache_status(child, {"cache_space_check": {**data, "check_id": "blocked", "state": "blocked",
                            "sampled_at": (now + timezone.timedelta(seconds=10)).isoformat(), "available_bytes": 1}})
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["cache_space_check"]["state"], "blocked")
        self.assertEqual(OperationalEvent.objects.filter(source="agent.cache_preflight").count(), 1)

    @patch("apps.alert.services.internal.event_alerts.handle_platform_event")
    def test_warning_scope_keeps_distinct_backups_executors_and_cache_filesystems(self, _alert):
        from apps.monitor.models import OperationalEvent
        from apps.task.models import Task

        now = timezone.now()
        check = {"state": "warning", "sampled_at": now.isoformat(), "check_id": "one-check", "cache_root": "/cache",
                 "cache_filesystem_id": "disk-one", "available_bytes": 15 * GIB, "policy": DEFAULT_POLICY}
        proxy = Node.objects.create(organization=self.org, name="Proxy", role="proxy", metadata=self.node.metadata)
        parent = Task.objects.create(organization_id=self.org.id, task_type="backup", display_name="Backup")
        for executor, filesystem, path in ((self.node, "disk-one", "/cache"), (proxy, "disk-one", "/cache"),
                                           (self.node, "disk-two", "/cache"), (self.node, "disk-one", "/other-cache")):
            child = NodeTask.objects.create(node=executor, organization=self.org, kind="backup.snapshot.create",
                         parent_task=parent, watchdog_deadline_at=now + timezone.timedelta(hours=1))
            project_cache_status(child, {"cache_space_check": {**check, "cache_filesystem_id": filesystem, "cache_root": path}})
        self.assertEqual(OperationalEvent.objects.filter(source="agent.cache_preflight").count(), 4)
        next_backup = Task.objects.create(organization_id=self.org.id, task_type="backup", display_name="Next backup")
        child.parent_task = next_backup
        child.save(update_fields=["parent_task", "updated_at"])
        project_cache_status(child, {"cache_space_check": check})
        self.assertEqual(OperationalEvent.objects.filter(source="agent.cache_preflight").count(), 5)

    @patch("apps.alert.services.internal.event_alerts.handle_platform_event")
    def test_warning_cannot_associate_a_foreign_tenant_backup(self, _alert):
        from apps.monitor.models import OperationalEvent
        from apps.task.models import Task

        foreign = Organization.objects.create(key="cache-foreign", name="Other tenant")
        parent = Task.objects.create(organization_id=foreign.id, task_type="backup", display_name="Foreign backup")
        self.task.correlation_type = "protection.backup.policy_prepare"
        self.task.correlation_id = str(parent.task_uuid)
        self.task.save(update_fields=["correlation_type", "correlation_id", "updated_at"])
        project_cache_status(self.task, {"cache_space_check": {
            "state": "warning", "sampled_at": timezone.now().isoformat(), "cache_root": "/cache",
            "available_bytes": 15 * GIB, "policy": DEFAULT_POLICY,
        }})
        event = OperationalEvent.objects.get(source="agent.cache_preflight")
        self.assertEqual(event.organization_id, self.org.id)
        self.assertEqual(event.resource_id, str(self.task.id))
        self.assertIsNone(event.metadata["task_uuid"])

    def test_stale_applied_revision_never_marks_new_policy_applied(self):
        self.node.metadata["cache_policy"] = {**DEFAULT_POLICY, "revision": 1}
        self.node.save()
        task = NodeTask.objects.create(watchdog_deadline_at=timezone.now()+timezone.timedelta(hours=1), organization=self.org, node=self.node, kind="cache.policy.apply")
        project_cache_status(task, {"policy": DEFAULT_POLICY, "application_state": "applied", "mode": "demand_driven"})
        self.node.refresh_from_db()
        self.assertEqual(snapshot(self.node)["application_state"], "pending")

    def test_unknown_space_is_not_fabricated_as_low_space_or_zero(self):
        from apps.monitor.models import OperationalEvent
        check = {"check_id": "unknown", "state": "check_error", "sampled_at": timezone.now().isoformat(), "cache_root": "/cache-volume/hfl", "available_bytes": 0, "policy": DEFAULT_POLICY}
        project_cache_status(self.task, {"cache_space_check": check})
        self.node.refresh_from_db()
        self.assertIsNone(self.node.metadata["cache_space_check"]["available_bytes"])
        self.assertIsNone(snapshot(self.node)["report"]["last_preflight"]["available_bytes"])
        self.assertFalse(OperationalEvent.objects.filter(source="agent.cache_preflight").exists())

    def test_blocked_backup_does_not_create_a_second_cache_specific_event(self):
        from apps.monitor.models import OperationalEvent
        project_cache_status(self.task, {"cache_space_check": {"state": "blocked", "sampled_at": timezone.now().isoformat(), "available_bytes": 1, "cache_root": "/cache", "policy": DEFAULT_POLICY}})
        self.assertFalse(OperationalEvent.objects.filter(source="agent.cache_preflight").exists())

    @patch("apps.notification.channels.webhook.WebhookChannel.send")
    def test_warning_reuses_existing_rule_silence_and_notification_delivery(self, send):
        from apps.alert.models import AlertPolicy, AlertRecord
        from apps.monitor.models import OperationalEvent
        from apps.notification.models import NotificationChannel, NotificationDelivery, NotificationLog

        from apps.task.models import Task
        parent = Task.objects.create(organization_id=self.org.id, task_type="backup", display_name="Backup")
        self.task.correlation_type = "protection.backup"
        self.task.correlation_id = str(parent.task_uuid)
        self.task.save(update_fields=["correlation_type", "correlation_id", "updated_at"])

        channel = NotificationChannel.objects.create(
            organization=self.org, name="Existing channel", channel_type="webhook",
            config={"url": "https://example.invalid/cache-test"},
        )
        settings = {"event_category": "protection", "event_types": ["backup_space_check_warning"],
                    "merge_enabled": True, "merge_window_seconds": 600, "repeat_interval_seconds": 1800}
        policy = AlertPolicy.objects.create(
            organization=self.org, name="Existing space rule", type="event", resource_type="task",
            scope="all", enabled=True, trigger_rule=settings, notification_channel_ids=[channel.id],
        )
        check = {"check_id": "first", "state": "warning", "sampled_at": timezone.now().isoformat(),
                 "available_bytes": 15 * GIB, "cache_root": "/cache", "policy": DEFAULT_POLICY}
        project_cache_status(self.task, {"cache_space_check": check})
        project_cache_status(self.task, {"cache_space_check": check})
        self.assertEqual(OperationalEvent.objects.filter(source="agent.cache_preflight").count(), 1)
        alert = AlertRecord.objects.get(policy_id=policy.id)
        self.assertEqual(alert.resource_id, str(parent.id))
        self.assertEqual(alert.metadata["execution_node_id"], self.node.id)
        delivery = NotificationDelivery.objects.get(channel=channel)
        self.assertEqual(delivery.status, NotificationDelivery.Status.SENT)
        self.assertEqual(delivery.event_type, "alert.firing")
        self.assertEqual(NotificationLog.objects.filter(alert_record_id=alert.id, status="success").count(), 1)
        send.assert_called_once()

        # Rechecks of the same backup must not re-enter the unchanged event,
        # alert or notification pipeline.
        project_cache_status(self.task, {"cache_space_check": {**check, "check_id": "next"}})
        self.assertEqual(OperationalEvent.objects.filter(source="agent.cache_preflight").count(), 1)
        self.assertEqual(AlertRecord.objects.filter(policy_id=policy.id).count(), 1)
        self.assertEqual(NotificationDelivery.objects.filter(channel=channel).count(), 1)
        send.assert_called_once()
        policy.refresh_from_db()
        self.assertEqual(policy.trigger_rule, settings)
        self.assertEqual(policy.notification_channel_ids, [channel.id])

    def test_warning_alert_respects_revoked_tenant_owner_authority(self):
        from common.extension_spi import get_authz_provider
        from apps.alert.models import AlertPolicy, AlertRecord
        from apps.monitor.models import OperationalEvent
        from apps.task.models import Task

        if not callable(getattr(get_authz_provider(), "can_evaluate_alert_target", None)):
            self.skipTest("requires the Enterprise live alert-authorization contract")
        self.owner_membership.is_active = False
        self.owner_membership.save(update_fields=["is_active"])
        parent = Task.objects.create(organization_id=self.org.id, task_type="backup", display_name="Backup")
        self.task.correlation_type = "protection.backup"
        self.task.correlation_id = str(parent.task_uuid)
        self.task.save(update_fields=["correlation_type", "correlation_id", "updated_at"])
        AlertPolicy.objects.create(
            organization=self.org, name="Existing warning rule", type="event", resource_type="task",
            enabled=True, trigger_rule={"event_category": "protection", "event_types": ["backup_space_check_warning"]},
        )
        project_cache_status(self.task, {"cache_space_check": {
            "state": "warning", "sampled_at": timezone.now().isoformat(), "cache_root": "/cache",
            "available_bytes": 15 * GIB, "policy": DEFAULT_POLICY,
        }})
        self.assertEqual(OperationalEvent.objects.filter(source="agent.cache_preflight").count(), 1)
        self.assertFalse(AlertRecord.objects.exists())

    @patch("apps.alert.services.internal.lifecycle.send_notification")
    def test_failed_checks_use_existing_task_failure_signal_without_warning(self, send):
        from apps.alert.models import AlertPolicy, AlertRecord
        from apps.monitor.models import OperationalEvent
        from apps.task.models import Task
        from apps.task.signals import task_failed

        policy = AlertPolicy.objects.create(
            organization=self.org, name="Existing backup failure rule", type="task", resource_type="task",
            enabled=True, trigger_rule={"task_type": "backup", "event_type": "task_failed"},
        )
        for state, code in (("blocked", "CACHE_SPACE_INSUFFICIENT"), ("check_error", "CACHE_SPACE_CHECK_FAILED")):
            with self.subTest(state=state):
                project_cache_status(self.task, {"cache_space_check": {
                    "state": state, "sampled_at": timezone.now().isoformat(), "available_bytes": None,
                    "cache_root": "/cache", "policy": DEFAULT_POLICY,
                }})
                failed = Task.objects.create(
                    organization_id=self.org.id, task_type="backup", display_name="Cache preflight failed",
                    status="failed", error_code=code, error_message=f"{code}: check the cache filesystem",
                )
                with self.captureOnCommitCallbacks(execute=True):
                    task_failed.send(sender=Task, task_uuid=str(failed.task_uuid))
                alert = AlertRecord.objects.get(policy_id=policy.id, resource_id=str(failed.id))
                self.assertEqual(alert.message, failed.error_message)
                self.assertTrue(OperationalEvent.objects.filter(event_type="task.failed", correlation_id=str(failed.task_uuid)).exists())
        self.assertFalse(OperationalEvent.objects.filter(source="agent.cache_preflight").exists())
        self.assertEqual(send.call_count, 2)


class CacheManagementApiTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.iam.services.registration_service import provision_registered_user_tenant
        from rest_framework.test import APIClient
        self.user = get_user_model().objects.create_user(username="cache-api@test.local", email="cache-api@test.local", password="isolated-test-password", is_active=True)
        self.org, _ = provision_registered_user_tenant(self.user)
        self.node = Node.objects.create(organization=self.org, name="Legacy Agent", role="agent", metadata={"inventory": {"capabilities": []}, "perf_settings": {"kopiaCacheMb": 4096}})
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/v1/node/nodes/{self.node.id}/cache/"

    def test_old_node_read_is_truthful_and_new_command_is_rejected(self):
        response = self.client.get(self.url, HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["supported"])
        self.assertIsNone(response.data["policy"])
        response = self.client.post(self.url, {"operation": "save_policy", "blocking_bytes": 2 * GIB}, format="json", HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(response.status_code, 400)
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["perf_settings"]["kopiaCacheMb"], 4096)
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())

    def test_cache_task_lookup_cannot_cross_node_or_org(self):
        other_org = Organization.objects.create(key="cache-api-other", name="Other")
        other = Node.objects.create(organization=other_org, name="Other", role="proxy")
        task = NodeTask.objects.create(watchdog_deadline_at=timezone.now()+timezone.timedelta(hours=1), organization=other_org, node=other, kind="cache.relocation.validate", result={"validation_token": "private-validation"})
        response = self.client.get(self.url, {"task_id": str(task.id)}, HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f"/api/v1/node/nodes/{other.id}/cache/", HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(response.status_code, 404)

    def test_generic_task_endpoint_cannot_bypass_relocation_validation(self):
        response = self.client.post("/api/v1/node/node-tasks/", {"node_id": self.node.id, "kind": "cache.relocate", "payload": {"validation_token": "forged"}}, format="json", HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())


class NewModePerformanceIsolationTests(SimpleTestCase):
    def test_hidden_legacy_cache_values_and_absence_are_preserved(self):
        inputs = {"cpuCores": 2, "memCapMb": 1024, "bandwidth": 12, "log": "info", "logRetention": 14, "logSpaceMb": 512}
        for old_cache in ({}, {"kopiaCacheMb": 0}, {"kopiaCacheMb": 4096}, {"kopia_cache_mb": 16384}):
            current = {"inventory": {"capabilities": [CAPABILITY, GIB_POLICY_CAPABILITY]}, "perf_settings": {**inputs, **old_cache}}
            result = merge_performance_metadata(current, {"perf_settings": {**inputs, "cpuCores": 3, "kopiaCacheMb": 8192}}, role="agent")
            expected = {key: value for key, value in result["perf_settings"].items() if "cache" in key.lower()}
            self.assertEqual(expected, old_cache)
            self.assertEqual(result["perf_settings"]["cpuCores"], 3)
