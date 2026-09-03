import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import close_old_connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensChatBinding,
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
)
from apps.lens_bridge.services.sync_queue import (
    queue_copilot_chat_provision,
    queue_copilot_chat_teardown,
)
from apps.lens_bridge.services import chat_binding, chat_lifecycle
from apps.lens_bridge.tasks import chat_lifecycle as chat_lifecycle_tasks
from apps.node.models import Node
from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
    SnapshotUsageLease,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from common.errors import AppError


class CopilotLifecycleQueueTests(SimpleTestCase):
    @patch(
        "apps.lens_bridge.services.chat_lifecycle.run_copilot_chat_provision",
        return_value={
            "session_link_id": 42,
            "status": "waiting",
            "next_poll": {
                "generation": 3,
                "sequence": 8,
                "retry_after_seconds": 30,
            },
        },
    )
    def test_pending_task_schedules_a_short_follow_up(self, _run):
        task = chat_lifecycle_tasks.execute_copilot_chat_provision_task
        with patch.object(task, "apply_async") as apply_async:
            result = task.run(session_link_id=42)

        self.assertEqual(result["status"], "waiting")
        apply_async.assert_called_once_with(
            kwargs={
                "session_link_id": 42,
                "expected_generation": 3,
                "expected_poll_sequence": 8,
            },
            countdown=30,
        )

    @patch("apps.lens_bridge.services.chat_lifecycle._defer_provision_poll")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle._claim_copilot_chat_provision",
        return_value=("claim-token", "claimed"),
    )
    @patch(
        "apps.lens_bridge.services.chat_lifecycle._run_copilot_chat_provision",
        return_value={"session_link_id": 42, "status": "waiting"},
    )
    def test_pending_conversion_releases_provision_worker_lease(
        self,
        _run,
        _claim,
        defer_poll,
    ):
        result = chat_lifecycle.run_copilot_chat_provision(session_link_id=42)

        self.assertEqual(result["status"], "waiting")
        defer_poll.assert_called_once_with(
            42,
            "claim-token",
            retry_after_seconds=5,
        )

    @patch("apps.lens_bridge.services.chat_lifecycle._cleanup_failed_provision")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle._defer_provision_for_transient_error",
        return_value={
            "generation": 2,
            "sequence": 4,
            "retry_after_seconds": 30,
        },
    )
    @patch(
        "apps.lens_bridge.services.chat_lifecycle._claim_copilot_chat_provision",
        return_value=("claim-token", "claimed"),
    )
    @patch(
        "apps.lens_bridge.services.chat_lifecycle._run_copilot_chat_provision",
        side_effect=chat_lifecycle.sl_client.LensBridgeUnavailable(),
    )
    def test_transient_source_lens_failure_waits_without_cleanup(
        self,
        _run,
        _claim,
        defer_transient,
        cleanup_failed,
    ):
        result = chat_lifecycle.run_copilot_chat_provision(session_link_id=42)

        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["next_poll"]["sequence"], 4)
        defer_transient.assert_called_once()
        cleanup_failed.assert_not_called()

    @patch("apps.lens_bridge.services.chat_lifecycle.LensSessionLink.objects.filter")
    @patch("apps.lens_bridge.services.chat_lifecycle._mark_provision_failed_by_id")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle._claim_copilot_chat_provision",
        return_value=("claim-token", "claimed"),
    )
    @patch(
        "apps.lens_bridge.services.chat_lifecycle._run_copilot_chat_provision",
        side_effect=RuntimeError("database schema mismatch"),
    )
    def test_provision_records_failures_before_pipeline_starts(
        self,
        _run,
        _claim,
        mark_failed,
        filter_sessions,
    ):
        filter_sessions.return_value.first.return_value = None
        with self.assertRaisesRegex(RuntimeError, "database schema mismatch"):
            chat_lifecycle.run_copilot_chat_provision(session_link_id=42)

        mark_failed.assert_called_once_with(
            42,
            "claim-token",
            "database schema mismatch",
            error_state={
                "code": "INSIGHT.CHAT_PREPARATION_FAILED",
                "message": (
                    "Chat preparation failed. Try again or contact your administrator."
                ),
                "retryable": True,
                "meta": {},
            },
            expected_generation=0,
        )

    @patch(
        "apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_provision_task.delay"
    )
    def test_provision_dispatches_to_celery(self, delay):
        queue_copilot_chat_provision(session_link_id=42)

        delay.assert_called_once_with(
            session_link_id=42,
            expected_generation=1,
            expected_poll_sequence=0,
        )

    @patch(
        "apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_teardown_task.delay",
        side_effect=ConnectionError("broker unavailable"),
    )
    def test_teardown_queue_failure_does_not_use_daemon_thread(self, _delay):
        with self.assertRaisesRegex(RuntimeError, "Unable to queue chat teardown"):
            queue_copilot_chat_teardown(session_link_id=42)


class ChatBindingValidationTests(SimpleTestCase):
    @patch(
        "apps.lens_bridge.services.chat_binding.lock_repositories_for_workload",
        side_effect=DjangoValidationError(
            {"repository_id": "Repository is not available for read operations."}
        ),
    )
    @patch("apps.lens_bridge.services.chat_binding.BackupConfig.objects.filter")
    def test_repository_domain_error_is_exposed_as_api_validation(
        self,
        filter_configs,
        _lock_repositories,
    ):
        filter_configs.return_value.first.return_value = SimpleNamespace(
            repository_id=7
        )

        with self.assertRaises(ValidationError) as raised:
            chat_binding._validate_snapshot(
                SimpleNamespace(id=5),
                backup_config_id=3,
                backup_source_snapshot_id=11,
                backup_snapshot_directory_id=None,
            )

        self.assertIn("repository_id", raised.exception.detail)


class CopilotDefaultTitleTests(SimpleTestCase):
    def test_extracts_windows_directory_name(self):
        self.assertEqual(
            chat_lifecycle._source_path_basename(r"C:\Finance\Reports"),
            "Reports",
        )

    def test_extracts_posix_file_name(self):
        self.assertEqual(
            chat_lifecycle._source_path_basename("/srv/contracts/report.pdf"),
            "report.pdf",
        )

    def test_drive_root_uses_source_fallback(self):
        self.assertEqual(chat_lifecycle._source_path_basename("C:\\"), "")

    @patch(
        "apps.lens_bridge.services.chat_lifecycle._unique_session_title",
        side_effect=lambda _org, *, user, base_title: base_title,
    )
    def test_multiple_scopes_use_first_item_and_remaining_count(self, _unique_title):
        title = chat_lifecycle._default_session_title(
            object(),
            user=object(),
            source_name="zjb-2",
            source_scopes=[
                {"source_path": r"C:\Finance\Reports"},
                {"source_path": r"C:\Finance\Contracts"},
                {"source_path": r"C:\Finance\Forecasts"},
            ],
        )

        self.assertEqual(title, "Reports +2")

    @patch("apps.lens_bridge.services.chat_lifecycle.LensSessionLink.objects.filter")
    def test_duplicate_titles_use_parenthesized_number(self, filter_sessions):
        filter_sessions.return_value.values_list.return_value = [
            "Reports",
            "Reports (2)",
        ]

        title = chat_lifecycle._unique_session_title(
            object(),
            user=object(),
            base_title="Reports",
        )

        self.assertEqual(title, "Reports (3)")


class CopilotTrustedScopeSummaryTests(SimpleTestCase):
    def test_accepts_complete_nonnegative_summary(self):
        self.assertTrue(
            chat_lifecycle._scope_has_trusted_summary(
                {"path_type": "dir", "file_count": 2, "size_bytes": 42}
            )
        )

    def test_rejects_negative_or_non_numeric_summary(self):
        self.assertFalse(
            chat_lifecycle._scope_has_trusted_summary(
                {"path_type": "file", "file_count": 1, "size_bytes": -1}
            )
        )
        self.assertFalse(
            chat_lifecycle._scope_has_trusted_summary(
                {"path_type": "dir", "file_count": "invalid", "size_bytes": 42}
            )
        )
        self.assertFalse(
            chat_lifecycle._scope_has_trusted_summary(
                {"path_type": "dir", "file_count": 1.5, "size_bytes": 42}
            )
        )
        self.assertFalse(
            chat_lifecycle._scope_has_trusted_summary(
                {"path_type": "dir", "file_count": 1, "size_bytes": 2**63}
            )
        )

    def test_rejects_file_summary_without_exactly_one_file(self):
        self.assertFalse(
            chat_lifecycle._scope_has_trusted_summary(
                {"path_type": "file", "file_count": 0, "size_bytes": 0}
            )
        )
        self.assertFalse(
            chat_lifecycle._scope_has_trusted_summary(
                {"path_type": "file", "file_count": 2, "size_bytes": 42}
            )
        )


class CopilotRetryTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="copilot-retry", name="Copilot Retry"
        )
        self.user = get_user_model().objects.create_user(
            username="copilot-retry",
            email="copilot-retry@example.com",
            password="test-password",
        )

    def create_session(self, lifecycle_status: str) -> LensSessionLink:
        return LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            title="Retry Chat",
            lifecycle_status=lifecycle_status,
        )

    def create_public_gateway_session(self) -> LensSessionLink:
        """Create a failed tenant chat reserved on a platform Gateway."""
        from apps.lens_bridge.services import platform_lens

        platform_org = platform_lens.get_or_create_platform_org()
        gateway = Node.objects.create(
            organization=platform_org,
            name="public-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=platform_org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )
        return LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            title="Retry Public Chat",
            lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
            gateway_link=gateway_link,
            source_scopes_json=[
                {
                    "source_path": "/docs/a.txt",
                    "path_type": "file",
                    "size_bytes": 1024,
                    "backup_snapshot_directory_id": 1,
                }
            ],
        )

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    def test_failed_session_is_queued_once(self, queue_provision):
        session = self.create_session(LensSessionLink.LifecycleStatus.FAILED)

        with self.captureOnCommitCallbacks(execute=True):
            updated = chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertEqual(
            updated.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING
        )
        self.assertEqual(updated.provision_phase, LensSessionLink.ProvisionPhase.QUEUED)
        queue_provision.assert_called_once_with(session.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    def test_failed_session_retry_reacquires_snapshot_usage(self, queue_provision):
        source_agent = Node.objects.create(
            organization=self.organization,
            name="retry-chat-source",
            role=Node.Role.AGENT,
        )
        snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.organization.id,
            snapshot_uid="retry-snapshot",
            idempotency_key="retry-snapshot",
            source_type="agent",
            source_ref_id=source_agent.id,
            backup_config_id=1,
            repository_id=1,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        session = self.create_session(LensSessionLink.LifecycleStatus.FAILED)
        session.backup_source_snapshot_id = snapshot.id
        session.save(update_fields=["backup_source_snapshot_id", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertTrue(
            SnapshotUsageLease.objects.filter(
                snapshot_id=snapshot.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
                consumer_id=str(session.id),
            ).exists()
        )
        queue_provision.assert_called_once_with(session.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    def test_unclaimed_provisioning_session_retry_is_requeued(self, queue_provision):
        session = self.create_session(LensSessionLink.LifecycleStatus.PROVISIONING)

        with self.captureOnCommitCallbacks(execute=True):
            updated = chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertEqual(
            updated.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING
        )
        queue_provision.assert_called_once_with(session.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    def test_legacy_chat_binding_backfills_gateway_before_retry(
        self,
        queue_provision,
    ):
        gateway = Node.objects.create(
            organization=self.organization,
            name="legacy-chat-gateway",
            role=Node.Role.GATEWAY,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=self.organization,
            gateway=gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
        )
        binding = LensChatBinding.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            backup_config_id=10,
            backup_source_snapshot_id=20,
            source_path="/legacy",
            gateway_link=gateway_link,
        )
        session = self.create_session(LensSessionLink.LifecycleStatus.FAILED)
        session.chat_binding = binding
        session.save(update_fields=["chat_binding", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            updated = chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertEqual(updated.gateway_link_id, gateway_link.id)
        session.refresh_from_db()
        self.assertEqual(session.gateway_link_id, gateway_link.id)
        queue_provision.assert_called_once_with(session.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    def test_live_provisioning_session_retry_is_idempotent(self, queue_provision):
        session = self.create_session(LensSessionLink.LifecycleStatus.PROVISIONING)
        session.provision_claim_token = uuid.uuid4()
        session.provision_claimed_at = timezone.now()
        session.provision_next_retry_at = timezone.now() + timedelta(minutes=1)
        session.save(
            update_fields=[
                "provision_claim_token",
                "provision_claimed_at",
                "provision_next_retry_at",
                "updated_at",
            ]
        )

        updated = chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertEqual(
            updated.lifecycle_status,
            LensSessionLink.LifecycleStatus.PROVISIONING,
        )
        queue_provision.assert_not_called()

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    def test_ready_session_retry_returns_current_state(self, queue_provision):
        session = self.create_session(LensSessionLink.LifecycleStatus.READY)

        updated = chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertEqual(
            updated.lifecycle_status, LensSessionLink.LifecycleStatus.READY
        )
        queue_provision.assert_not_called()

    def test_deleting_session_is_not_retryable(self):
        session = self.create_session(LensSessionLink.LifecycleStatus.DELETING)

        with self.assertRaisesRegex(ValidationError, "Session is not retryable"):
            chat_lifecycle.retry_copilot_chat_provision(session)

    def test_cleanup_blocked_session_is_not_retryable(self):
        session = self.create_session(LensSessionLink.LifecycleStatus.FAILED)
        session.cleanup_intent = LensSessionLink.CleanupIntent.RESET_FOR_RETRY
        session.cleanup_status = LensSessionLink.CleanupStatus.BLOCKED
        session.save(
            update_fields=["cleanup_intent", "cleanup_status", "updated_at"]
        )

        with self.assertRaisesRegex(ValidationError, "recovery must finish"):
            chat_lifecycle.retry_copilot_chat_provision(session)

    def test_stale_poll_message_cannot_claim_or_spawn_a_successor(self):
        session = self.create_session(LensSessionLink.LifecycleStatus.PROVISIONING)
        session.provision_generation = 3
        session.provision_poll_sequence = 2
        session.save(
            update_fields=[
                "provision_generation",
                "provision_poll_sequence",
                "updated_at",
            ]
        )

        token, status = chat_lifecycle._claim_copilot_chat_provision(
            session.id,
            expected_generation=3,
            expected_poll_sequence=1,
        )

        self.assertIsNone(token)
        self.assertEqual(status, "stale")
        session.refresh_from_db()
        self.assertIsNone(session.provision_claim_token)

    def test_legacy_message_cannot_claim_a_new_generation(self):
        session = self.create_session(LensSessionLink.LifecycleStatus.PROVISIONING)
        session.provision_generation = 2
        session.provision_poll_sequence = 0
        session.save(
            update_fields=[
                "provision_generation",
                "provision_poll_sequence",
                "updated_at",
            ]
        )

        token, status = chat_lifecycle._claim_copilot_chat_provision(session.id)

        self.assertIsNone(token)
        self.assertEqual(status, "stale")
        session.refresh_from_db()
        self.assertIsNone(session.provision_claim_token)

    def test_partially_fenced_message_is_stale(self):
        session = self.create_session(LensSessionLink.LifecycleStatus.PROVISIONING)

        token, status = chat_lifecycle._claim_copilot_chat_provision(
            session.id,
            expected_generation=1,
        )

        self.assertIsNone(token)
        self.assertEqual(status, "stale")
        session.refresh_from_db()
        self.assertIsNone(session.provision_claim_token)

    def test_valid_poll_advances_exactly_one_sequence(self):
        session = self.create_session(LensSessionLink.LifecycleStatus.PROVISIONING)
        session.provision_generation = 3
        session.provision_poll_sequence = 2
        session.save(
            update_fields=[
                "provision_generation",
                "provision_poll_sequence",
                "updated_at",
            ]
        )
        token, status = chat_lifecycle._claim_copilot_chat_provision(
            session.id,
            expected_generation=3,
            expected_poll_sequence=2,
        )

        self.assertEqual(status, "claimed")
        next_poll = chat_lifecycle._defer_provision_poll(
            session.id,
            token,
            retry_after_seconds=30,
        )

        self.assertEqual(next_poll["generation"], 3)
        self.assertEqual(next_poll["sequence"], 3)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity.session_scope_occupancy",
        return_value=(1024, False),
    )
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity.assert_public_gateway_capacity"
    )
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity.lock_public_gateway_capacity"
    )
    def test_failed_public_gateway_retry_rechecks_capacity(
        self,
        lock_capacity,
        assert_capacity,
        _session_occupancy,
        queue_provision,
    ):
        session = self.create_public_gateway_session()

        with self.captureOnCommitCallbacks(execute=True):
            updated = chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertEqual(
            updated.lifecycle_status, LensSessionLink.LifecycleStatus.PROVISIONING
        )
        lock_capacity.assert_called_once()
        assert_capacity.assert_called_once_with(
            gateway_link=lock_capacity.return_value,
            additional_bytes=1024,
            unknown_size=False,
        )
        queue_provision.assert_called_once_with(session.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity.assert_public_gateway_capacity"
    )
    def test_failed_public_gateway_retry_stays_failed_when_capacity_is_full(
        self,
        assert_capacity,
        queue_provision,
    ):
        from common.errors import AppError

        assert_capacity.side_effect = AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title="full",
            diagnostic="full",
        )
        session = self.create_public_gateway_session()

        with self.assertRaises(AppError):
            chat_lifecycle.retry_copilot_chat_provision(session)

        session.refresh_from_db()
        self.assertEqual(
            session.lifecycle_status, LensSessionLink.LifecycleStatus.FAILED
        )
        queue_provision.assert_not_called()

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "assert_public_gateway_capacity"
    )
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "lock_public_gateway_capacity"
    )
    def test_stale_provisioning_reservation_retry_does_not_consume_capacity_twice(
        self,
        lock_capacity,
        assert_capacity,
        queue_provision,
    ):
        session = self.create_public_gateway_session()
        session.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
        session.capacity_reservation_status = (
            LensSessionLink.CapacityReservationStatus.RESERVED
        )
        session.capacity_reserved_bytes = 1024
        session.provision_claimed_at = None
        session.save(
            update_fields=[
                "lifecycle_status",
                "capacity_reservation_status",
                "capacity_reserved_bytes",
                "provision_claimed_at",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            updated = chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertEqual(
            updated.lifecycle_status,
            LensSessionLink.LifecycleStatus.PROVISIONING,
        )
        self.assertEqual(
            updated.capacity_reservation_status,
            LensSessionLink.CapacityReservationStatus.PENDING,
        )
        lock_capacity.assert_not_called()
        assert_capacity.assert_not_called()
        queue_provision.assert_called_once_with(session.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "assert_public_gateway_capacity"
    )
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "lock_public_gateway_capacity"
    )
    def test_failed_existing_workspace_retry_does_not_consume_capacity_twice(
        self,
        lock_capacity,
        assert_capacity,
        queue_provision,
    ):
        session = self.create_public_gateway_session()
        knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.organization,
            name="Existing retry workspace",
            gateway=session.gateway_link.gateway,
            gateway_link=session.gateway_link,
            source_path="/docs/a.txt",
            source_scopes_json=session.source_scopes_json,
            created_by=self.user,
        )
        session.knowledge_source = knowledge_source
        session.capacity_reservation_status = (
            LensSessionLink.CapacityReservationStatus.RESERVED
        )
        session.capacity_reserved_bytes = 1024
        session.save(
            update_fields=[
                "knowledge_source",
                "capacity_reservation_status",
                "capacity_reserved_bytes",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            updated = chat_lifecycle.retry_copilot_chat_provision(session)

        self.assertEqual(
            updated.lifecycle_status,
            LensSessionLink.LifecycleStatus.PROVISIONING,
        )
        self.assertEqual(
            updated.capacity_reservation_status,
            LensSessionLink.CapacityReservationStatus.RESERVED,
        )
        lock_capacity.assert_not_called()
        assert_capacity.assert_not_called()
        queue_provision.assert_called_once_with(session.id)


class CopilotCapacityReservationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="copilot-capacity-reservation",
            name="Copilot Capacity Reservation",
        )
        self.user = get_user_model().objects.create_user(
            username="copilot-capacity-reservation@example.test",
            email="copilot-capacity-reservation@example.test",
        )
        gateway = Node.objects.create(
            organization=self.organization,
            name="capacity-gateway",
            role=Node.Role.GATEWAY,
        )
        self.gateway_link = LensGatewayLink.objects.create(
            organization=self.organization,
            gateway=gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
        )

    @patch("apps.subscription.services.quota.assert_gateway_select_within_limits")
    def test_reservation_locks_session_with_nullable_relations(self, _assert_limits):
        claim_token = uuid.uuid4()
        session = LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            scope_resolution_status=(LensSessionLink.ScopeResolutionStatus.RESOLVED),
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.PENDING
            ),
            provision_claim_token=claim_token,
            knowledge_source=None,
            source_scopes_json=[
                {"path_type": "dir", "file_count": 3, "size_bytes": 4096}
            ],
        )

        chat_lifecycle._reserve_chat_capacity(
            link=session,
            claim_token=str(claim_token),
        )

        session.refresh_from_db()
        self.assertEqual(
            session.capacity_reservation_status,
            LensSessionLink.CapacityReservationStatus.RESERVED,
        )
        self.assertEqual(session.capacity_reserved_bytes, 4096)

    @patch("apps.subscription.services.quota.assert_gateway_select_within_limits")
    @patch("common.extension_spi.get_quota_provider")
    def test_missing_organization_capacity_meter_is_temporarily_unavailable(
        self,
        get_quota_provider,
        _assert_limits,
    ):
        get_quota_provider.return_value = SimpleNamespace(
            get_limits=lambda _organization: {},
        )
        platform_organization = Organization.objects.create(
            key="capacity-meter-platform",
            name="Capacity Meter Platform",
        )
        platform_gateway = Node.objects.create(
            organization=platform_organization,
            name="capacity-meter-platform-gateway",
            role=Node.Role.GATEWAY,
        )
        platform_gateway_link = LensGatewayLink.objects.create(
            organization=platform_organization,
            gateway=platform_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )
        claim_token = uuid.uuid4()
        session = LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            gateway_link=platform_gateway_link,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            scope_resolution_status=LensSessionLink.ScopeResolutionStatus.RESOLVED,
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.PENDING
            ),
            provision_claim_token=claim_token,
            source_scopes_json=[
                {"path_type": "dir", "file_count": 3, "size_bytes": 4096}
            ],
        )

        with self.assertRaises(AppError) as raised:
            chat_lifecycle._reserve_chat_capacity(
                link=session,
                claim_token=str(claim_token),
            )

        self.assertEqual(
            raised.exception.code,
            "SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE",
        )
        self.assertEqual(raised.exception.status, 503)
        self.assertTrue(raised.exception.retryable)


class CopilotCapacityReservationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_same_public_gateway_capacity_cannot_be_oversubscribed(self):
        platform_org = Organization.objects.create(
            key="capacity-platform",
            name="Capacity Platform",
        )
        gateway = Node.objects.create(
            organization=platform_org,
            name="shared-capacity-gateway",
            role=Node.Role.GATEWAY,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=platform_org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            capacity_bytes=1024**3,
        )
        session_claims = []
        reservation_bytes = 700 * 1024**2
        for index in range(2):
            organization = Organization.objects.create(
                key=f"capacity-tenant-{index}",
                name=f"Capacity Tenant {index}",
            )
            user = get_user_model().objects.create_user(
                username=f"capacity-tenant-{index}@example.test",
                email=f"capacity-tenant-{index}@example.test",
            )
            claim_token = uuid.uuid4()
            session = LensSessionLink.objects.create(
                organization=organization,
                hfl_user=user,
                gateway_link=gateway_link,
                lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
                scope_resolution_status=(
                    LensSessionLink.ScopeResolutionStatus.RESOLVED
                ),
                capacity_reservation_status=(
                    LensSessionLink.CapacityReservationStatus.PENDING
                ),
                provision_claim_token=claim_token,
                knowledge_source=None,
                source_scopes_json=[
                    {
                        "path_type": "dir",
                        "file_count": 1,
                        "size_bytes": reservation_bytes,
                    }
                ],
            )
            session_claims.append((session.id, str(claim_token)))

        start_barrier = threading.Barrier(2)

        def reserve(session_id, claim_token):
            close_old_connections()
            try:
                start_barrier.wait(timeout=10)
                try:
                    chat_lifecycle._reserve_chat_capacity(
                        link=LensSessionLink(pk=session_id),
                        claim_token=claim_token,
                    )
                except AppError as exc:
                    return exc.code
                return "reserved"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda args: reserve(*args),
                    session_claims,
                )
            )

        self.assertCountEqual(
            results,
            ["reserved", "SUBSCRIPTION.QUOTA_EXCEEDED"],
        )
        reservations = list(
            LensSessionLink.objects.filter(
                id__in=[session_id for session_id, _claim in session_claims]
            ).values_list("capacity_reservation_status", "capacity_reserved_bytes")
        )
        self.assertEqual(
            sum(
                reserved_bytes
                for status, reserved_bytes in reservations
                if status == LensSessionLink.CapacityReservationStatus.RESERVED
            ),
            reservation_bytes,
        )


class CopilotChatModelBindingTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="copilot-model-binding",
            name="Copilot Model Binding",
        )
        self.user = get_user_model().objects.create_user(
            username="copilot-model-binding@example.test",
            email="copilot-model-binding@example.test",
        )
        self.gateway = Node.objects.create(
            organization=self.organization,
            name="private-gateway",
            role=Node.Role.GATEWAY,
        )
        self.source_agent = Node.objects.create(
            organization=self.organization,
            name="chat-model-binding-source",
            role=Node.Role.AGENT,
        )
        self.gateway_link = LensGatewayLink.objects.create(
            organization=self.organization,
            gateway=self.gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
        )
        self.repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="chat-model-binding-repository",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="chat-model-binding",
            config={"prefix": "chat/model-binding"},
        )
        reserve_repository_location(self.repository)
        mark_repository_location_owned(self.repository)
        mark_repository_location_ownership_verified(self.repository)
        self.config = BackupConfig.objects.create(
            organization_id=self.organization.id,
            name="Documents",
            source_type="host",
            source_ref_id=self.source_agent.id,
            repository_id=self.repository.id,
        )
        self.snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.organization.id,
            snapshot_uid="snapshot-model-binding",
            idempotency_key="snapshot-model-binding",
            source_type="host",
            source_ref_id=self.source_agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.organization.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=1,
            source_path="/documents",
            repository_id=self.repository.id,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

    def _create_chat(
        self,
        *,
        idempotency_key: str = "copilot-model-binding-create",
        analysis_type: str | None = None,
    ):
        return chat_lifecycle.create_copilot_chat(
            self.organization,
            user=self.user,
            backup_config_id=self.config.id,
            backup_source_snapshot_id=self.snapshot.id,
            source_scopes=[
                {
                    "source_path": "/documents",
                    "backup_snapshot_directory_id": self.directory.id,
                    "path_type": "dir",
                }
            ],
            gateway_mode=LensSessionLink.GatewaySelectionMode.MANUAL,
            gateway_link_id=self.gateway_link.id,
            idempotency_key=idempotency_key,
            analysis_type=analysis_type,
        )

    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org",
        return_value=(None, None),
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_missing_agent_model_blocks_chat_creation(
        self,
        _context,
        resolve_gateway,
        _default_models,
    ):
        resolve_gateway.return_value = self.gateway_link

        with self.assertRaises(ValidationError):
            self._create_chat()

        self.assertFalse(
            LensSessionLink.objects.filter(organization=self.organization).exists()
        )

    def test_offline_repository_is_reported_as_an_api_validation_error(self):
        self.repository.health = Repository.Health.OFFLINE
        self.repository.save(update_fields=["health", "updated_at"])

        with self.assertRaises(ValidationError) as raised:
            self._create_chat()

        self.assertIn("repository_id", raised.exception.detail)
        self.assertFalse(
            LensSessionLink.objects.filter(organization=self.organization).exists()
        )

    def test_unavailable_snapshot_is_rejected_before_chat_creation(self):
        self.snapshot.status = BackupSourceSnapshot.Status.FAILED
        self.snapshot.save(update_fields=["status", "updated_at"])

        with self.assertRaises(ValidationError) as raised:
            self._create_chat()

        self.assertIn("backup_source_snapshot_id", raised.exception.detail)
        self.assertFalse(
            LensSessionLink.objects.filter(organization=self.organization).exists()
        )

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org"
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_missing_multimodal_model_keeps_text_chat_available(
        self,
        _context,
        resolve_gateway,
        default_models,
        queue_provision,
    ):
        agent_uuid = uuid.uuid4()
        resolve_gateway.return_value = self.gateway_link
        default_models.return_value = (str(agent_uuid), None)

        with self.captureOnCommitCallbacks(execute=True):
            session = self._create_chat()

        self.assertEqual(str(session.agent_model_ref), str(agent_uuid))
        self.assertIsNone(session.multimodal_model_ref)
        self.assertIsNotNone(session.gateway_queue_entered_at)
        self.assertTrue(
            SnapshotUsageLease.objects.filter(
                snapshot_id=self.snapshot.id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
                consumer_id=str(session.id),
            ).exists()
        )
        queue_provision.assert_called_once_with(session.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org"
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_code_analysis_does_not_depend_on_gateway_task_snapshot(
        self,
        _context,
        resolve_gateway,
        default_models,
        queue_provision,
    ):
        self.gateway_link.config_json = {
            "sl_lensnode_snapshot": {
                "sl_tasks": [{"name": "knowledge_qa", "title": "Knowledge Q&A"}]
            }
        }
        self.gateway_link.save(update_fields=["config_json", "updated_at"])
        resolve_gateway.return_value = self.gateway_link
        default_models.return_value = (str(uuid.uuid4()), None)

        with self.captureOnCommitCallbacks(execute=True):
            session = self._create_chat(
                idempotency_key="copilot-code-analysis-create",
                analysis_type=LensSessionLink.AnalysisType.CODE_ANALYSIS,
            )

        self.assertEqual(
            session.analysis_type,
            LensSessionLink.AnalysisType.CODE_ANALYSIS,
        )
        queue_provision.assert_called_once_with(session.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org",
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_same_create_key_returns_the_original_chat(
        self,
        _context,
        resolve_gateway,
        default_models,
        queue_provision,
    ):
        resolve_gateway.return_value = self.gateway_link
        default_models.return_value = (str(uuid.uuid4()), None)

        with self.captureOnCommitCallbacks(execute=True):
            first = self._create_chat()
        self.gateway_link.chat_queue_capacity = 0
        self.gateway_link.save(update_fields=["chat_queue_capacity", "updated_at"])
        second = self._create_chat()

        self.assertEqual(second.id, first.id)
        self.assertEqual(
            LensSessionLink.objects.filter(organization=self.organization).count(),
            1,
        )
        queue_provision.assert_called_once_with(first.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org",
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_new_chat_is_rejected_when_gateway_waiting_capacity_is_full(
        self,
        _context,
        resolve_gateway,
        default_models,
        queue_provision,
    ):
        resolve_gateway.return_value = self.gateway_link
        default_models.return_value = (str(uuid.uuid4()), None)
        self.gateway_link.chat_queue_capacity = 0
        self.gateway_link.save(update_fields=["chat_queue_capacity", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            first = self._create_chat()

        with self.assertRaises(AppError) as raised:
            self._create_chat(idempotency_key="second-chat-on-full-gateway")

        self.assertEqual(
            raised.exception.code,
            "INSIGHT.GATEWAY_CHAT_QUEUE_FULL",
        )
        self.assertEqual(
            LensSessionLink.objects.filter(organization=self.organization).count(),
            1,
        )
        queue_provision.assert_called_once_with(first.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org",
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_same_create_key_replays_after_snapshot_directory_becomes_unavailable(
        self,
        _context,
        resolve_gateway,
        default_models,
        queue_provision,
    ):
        resolve_gateway.return_value = self.gateway_link
        default_models.return_value = (str(uuid.uuid4()), None)

        with self.captureOnCommitCallbacks(execute=True):
            first = self._create_chat()
        self.directory.status = BackupSourceSnapshotDirectory.Status.FAILED
        self.directory.save(update_fields=["status", "updated_at"])

        replay = self._create_chat()

        self.assertEqual(replay.id, first.id)
        queue_provision.assert_called_once_with(first.id)

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org",
        return_value=("fd1bd1fc-8856-4d3f-aae0-d0d289ddca98", None),
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_same_create_key_rejects_a_different_request(
        self,
        _context,
        resolve_gateway,
        _default_models,
        _queue_provision,
    ):
        resolve_gateway.return_value = self.gateway_link
        self._create_chat()

        with self.assertRaises(chat_lifecycle.ChatCreateIdempotencyConflict):
            chat_lifecycle.create_copilot_chat(
                self.organization,
                user=self.user,
                backup_config_id=self.config.id,
                backup_source_snapshot_id=self.snapshot.id,
                source_scopes=[
                    {
                        "source_path": "/documents/reports",
                        "backup_snapshot_directory_id": self.directory.id,
                        "path_type": "dir",
                    }
                ],
                gateway_mode=LensSessionLink.GatewaySelectionMode.MANUAL,
                gateway_link_id=self.gateway_link.id,
                idempotency_key="copilot-model-binding-create",
            )

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org",
        return_value=("fd1bd1fc-8856-4d3f-aae0-d0d289ddca98", None),
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    @patch("apps.protection.services.snapshot_browser.browse_snapshot_directory")
    @patch("apps.node.services.interface.run_agent_task_sync")
    @patch("apps.lens_bridge.services.sl_client.request_json")
    def test_nested_scope_create_is_local_and_returns_pending(
        self,
        sl_request,
        run_agent_sync,
        browse_snapshot,
        _context,
        resolve_gateway,
        _default_models,
        _queue_provision,
    ):
        resolve_gateway.return_value = self.gateway_link

        session = chat_lifecycle.create_copilot_chat(
            self.organization,
            user=self.user,
            backup_config_id=self.config.id,
            backup_source_snapshot_id=self.snapshot.id,
            source_scopes=[
                {
                    "source_path": "/documents/reports",
                    "backup_snapshot_directory_id": self.directory.id,
                    "path_type": "dir",
                }
            ],
            gateway_mode=LensSessionLink.GatewaySelectionMode.MANUAL,
            gateway_link_id=self.gateway_link.id,
            idempotency_key="nested-local-create",
        )

        self.assertEqual(
            session.scope_resolution_status,
            LensSessionLink.ScopeResolutionStatus.PENDING,
        )
        self.assertEqual(
            session.capacity_reservation_status,
            LensSessionLink.CapacityReservationStatus.PENDING,
        )
        browse_snapshot.assert_not_called()
        run_agent_sync.assert_not_called()
        sl_request.assert_not_called()

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_provision_or_mark_failed")
    @patch(
        "apps.lens_bridge.services.chat_lifecycle."
        "provisioning.configured_default_model_refs_for_org",
        return_value=("fd1bd1fc-8856-4d3f-aae0-d0d289ddca98", None),
    )
    @patch("apps.lens_bridge.services.chat_lifecycle._configured_gateway_link_for_chat")
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    def test_root_file_scope_is_always_counted_as_one_file(
        self,
        _context,
        resolve_gateway,
        _default_models,
        _queue_provision,
    ):
        resolve_gateway.return_value = self.gateway_link
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.FILE
        self.directory.file_count = 0
        self.directory.size_bytes = 42
        self.directory.save(
            update_fields=["path_type", "file_count", "size_bytes", "updated_at"]
        )

        session = self._create_chat()

        self.assertEqual(session.source_scopes_json[0]["path_type"], "file")
        self.assertEqual(session.source_scopes_json[0]["file_count"], 1)
        self.assertEqual(session.source_scopes_json[0]["size_bytes"], 42)
        self.assertEqual(
            session.scope_resolution_status,
            LensSessionLink.ScopeResolutionStatus.RESOLVED,
        )


class CopilotScopeRecoveryTests(SimpleTestCase):
    @patch("apps.lens_bridge.services.chat_lifecycle._set_phase")
    @patch("apps.lens_bridge.services.chat_lifecycle._update_provision_claim")
    def test_scope_resolution_recovers_dispatched_task_by_correlation(
        self,
        update_claim,
        _set_phase,
    ):
        task_id = uuid.uuid4()
        recovered_task = type(
            "RecoveredTask",
            (),
            {"id": task_id, "status": "pending"},
        )()
        link = type(
            "ScopeLink",
            (),
            {
                "id": 9,
                "organization": object(),
                "scope_resolution_status": "pending",
                "provision_state_json": {
                    "scope_resolution": {
                        "scope_index": 0,
                        "correlation_id": "chat:9:scope:0:dispatch-token",
                    }
                },
            },
        )()

        with patch(
            "apps.lens_bridge.services.snapshot_scope_tasks.scope_task_for_correlation",
            return_value=recovered_task,
        ) as recover_task:
            result = chat_lifecycle._resolve_chat_scopes(
                link=link,
                claim_token="claim-token",
                scopes=[{"path_type": "unknown"}],
            )

        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["scope_task_id"], str(task_id))
        recover_task.assert_called_once_with(
            organization=link.organization,
            correlation_id="chat:9:scope:0:dispatch-token",
        )
        update_claim.assert_called_once_with(
            link,
            "claim-token",
            "provision_state_json",
        )
        self.assertEqual(
            link.provision_state_json["scope_resolution"]["task_id"],
            str(task_id),
        )

    @patch("apps.lens_bridge.services.chat_lifecycle._set_phase")
    @patch("apps.lens_bridge.services.chat_lifecycle._update_provision_claim")
    def test_scope_resolution_ignores_a_mismatched_stored_task_id(
        self,
        update_claim,
        _set_phase,
    ):
        task_id = uuid.uuid4()
        recovered_task = type(
            "RecoveredTask",
            (),
            {"id": task_id, "status": "pending"},
        )()
        link = type(
            "ScopeLink",
            (),
            {
                "id": 9,
                "organization": object(),
                "scope_resolution_status": "pending",
                "provision_state_json": {
                    "scope_resolution": {
                        "scope_index": 0,
                        "task_id": "wrong-task",
                        "correlation_id": "chat:9:scope:0:dispatch-token",
                    }
                },
            },
        )()

        with (
            patch(
                "apps.lens_bridge.services.snapshot_scope_tasks."
                "scope_task_for_reference",
                return_value=None,
            ) as task_for_reference,
            patch(
                "apps.lens_bridge.services.snapshot_scope_tasks."
                "scope_task_for_correlation",
                return_value=recovered_task,
            ) as task_for_correlation,
        ):
            result = chat_lifecycle._resolve_chat_scopes(
                link=link,
                claim_token="claim-token",
                scopes=[{"source_path": "/documents/reports"}],
            )

        self.assertEqual(result["status"], "waiting")
        task_for_reference.assert_called_once()
        task_for_correlation.assert_called_once()
        update_claim.assert_called_once_with(
            link,
            "claim-token",
            "provision_state_json",
        )
        self.assertEqual(
            link.provision_state_json["scope_resolution"]["task_id"],
            str(task_id),
        )
