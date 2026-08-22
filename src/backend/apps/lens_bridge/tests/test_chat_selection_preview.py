import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.iam.models import Membership
from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import chat_selection_preview, snapshot_scope_tasks
from apps.lens_bridge.services.chat_lifecycle import _chat_create_request_identity
from apps.node.models import Node, NodeTask
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory


class ChatSelectionPreviewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="selection-preview@example.test",
            email="selection-preview@example.test",
        )
        self.organization, _ = provision_registered_user_tenant(self.user)
        self.snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.organization.id,
            snapshot_uid="selection-preview-snapshot",
            idempotency_key="selection-preview-snapshot",
            source_type="agent",
            source_ref_id=11,
            backup_config_id=21,
            repository_id=31,
            task_id=41,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.organization.id,
            backup_config_id=21,
            backup_config_dir_id=51,
            source_path="/documents",
            path_type=BackupSourceSnapshotDirectory.PathType.DIRECTORY,
            repository_id=31,
            kopia_snapshot_id="kopia-selection-preview",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
            file_count=5739,
            size_bytes=2_300_000_000,
        )
        self.node = Node.objects.create(
            organization=self.organization,
            name="selection-reader",
            role=Node.Role.GATEWAY,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _selection_task(self, *, user=None):
        task_user = user or self.user
        return NodeTask.objects.create(
            organization=self.organization,
            requesting_organization_id=self.organization.id,
            node=self.node,
            correlation_type=snapshot_scope_tasks.SCOPE_CORRELATION_TYPE,
            correlation_id=(
                f"selection:user:{task_user.id}:{uuid.uuid4()}"
            ),
            kind="lens.snapshot.scope.resolve",
            status=NodeTask.Status.SUCCESS,
            result={
                "path_type": "dir",
                "file_count": 4,
                "size_bytes": 1024,
                "skipped_special_count": 0,
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=5),
        )

    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "snapshot_scope_tasks.dispatch_scope_resolution"
    )
    def test_root_scope_uses_persisted_summary_without_reader_task(self, dispatch):
        payload = chat_selection_preview.start_scope_preview(
            organization=self.organization,
            user=self.user,
            snapshot_id=self.snapshot.id,
            directory_id=self.directory.id,
            source_path="/documents/",
            gateway_link_id=71,
            request_token=str(uuid.uuid4()),
            attempt=0,
        )

        self.assertEqual(payload["status"], NodeTask.Status.SUCCESS)
        self.assertIsNone(payload["task_id"])
        self.assertEqual(payload["summary"]["file_count"], 5739)
        self.assertEqual(payload["summary"]["size_bytes"], 2_300_000_000)
        dispatch.assert_not_called()

    def test_root_scope_rejects_a_snapshot_that_is_no_longer_available(self):
        self.snapshot.status = BackupSourceSnapshot.Status.FAILED
        self.snapshot.save(update_fields=["status", "updated_at"])

        with self.assertRaises(ValidationError):
            chat_selection_preview.start_scope_preview(
                organization=self.organization,
                user=self.user,
                snapshot_id=self.snapshot.id,
                directory_id=self.directory.id,
                source_path="/documents",
                gateway_link_id=71,
                request_token=str(uuid.uuid4()),
                attempt=0,
            )

    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "snapshot_scope_tasks.dispatch_scope_resolution"
    )
    def test_nested_scope_dispatches_current_repository_reader_task(self, dispatch):
        task = SimpleNamespace(id=uuid.uuid4(), status=NodeTask.Status.PENDING)
        dispatch.return_value = task

        payload = chat_selection_preview.start_scope_preview(
            organization=self.organization,
            user=self.user,
            snapshot_id=self.snapshot.id,
            directory_id=self.directory.id,
            source_path="/documents/contracts",
            gateway_link_id=71,
            request_token=str(uuid.uuid4()),
            attempt=0,
        )

        self.assertEqual(payload["status"], NodeTask.Status.PENDING)
        kwargs = dispatch.call_args.kwargs
        self.assertEqual(kwargs["requesting_user_id"], self.user.id)
        self.assertEqual(kwargs["path"], "contracts")
        self.assertTrue(
            kwargs["correlation_id"].startswith(
                f"selection:user:{self.user.id}:"
            )
        )

    def test_scope_task_payload_never_exposes_agent_diagnostics(self):
        task = NodeTask.objects.create(
            organization=self.organization,
            requesting_organization_id=self.organization.id,
            node=self.node,
            correlation_type=snapshot_scope_tasks.SCOPE_CORRELATION_TYPE,
            correlation_id=f"selection:user:{self.user.id}:{uuid.uuid4()}",
            kind="lens.snapshot.scope.resolve",
            status=NodeTask.Status.FAILED,
            result={"config_file": "/private/repository.config"},
            last_error="failed to read /private/repository.config",
            watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=5),
        )

        payload = chat_selection_preview.scope_task_payload(task)

        self.assertNotIn("repository.config", str(payload))
        self.assertNotIn("summary", payload)

    def test_invalid_success_summary_becomes_safe_terminal_error(self):
        task = self._selection_task()
        task.result = {
            "path_type": "dir",
            "file_count": "invalid",
            "size_bytes": 1024,
        }
        task.save(update_fields=["result", "updated_at"])

        payload = chat_selection_preview.scope_task_payload(task)

        self.assertEqual(payload["status"], NodeTask.Status.FAILED)
        self.assertEqual(payload["error_code"], "INSIGHT.SCOPE_SUMMARY_INVALID")
        self.assertFalse(payload["retryable"])
        self.assertNotIn("file_count", payload["error"])
        self.assertNotIn("summary", payload)

    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "snapshot_scope_tasks.dispatch_scope_resolution"
    )
    def test_nested_scope_waits_when_user_already_has_two_active_previews(
        self,
        dispatch,
    ):
        for index in range(2):
            NodeTask.objects.create(
                organization=self.organization,
                requesting_organization_id=self.organization.id,
                node=self.node,
                correlation_type=snapshot_scope_tasks.SCOPE_CORRELATION_TYPE,
                correlation_id=f"selection:user:{self.user.id}:active-{index}",
                kind="lens.snapshot.scope.resolve",
                status=NodeTask.Status.RUNNING,
                watchdog_deadline_at=(
                    timezone.now() + timezone.timedelta(minutes=5)
                ),
            )

        payload = chat_selection_preview.start_scope_preview(
            organization=self.organization,
            user=self.user,
            snapshot_id=self.snapshot.id,
            directory_id=self.directory.id,
            source_path="/documents/contracts",
            gateway_link_id=71,
            request_token=str(uuid.uuid4()),
            attempt=0,
        )

        self.assertEqual(payload["status"], "waiting")
        self.assertEqual(payload["error_code"], "INSIGHT.SELECTION_PREVIEW_BUSY")
        self.assertTrue(payload["retryable"])
        dispatch.assert_not_called()

    def test_same_organization_peer_cannot_read_or_cancel_preview_task(self):
        peer = get_user_model().objects.create_user(
            username="selection-preview-peer@example.test",
            email="selection-preview-peer@example.test",
        )
        Membership.objects.create(
            organization=self.organization,
            user=peer,
            role=Membership.Role.OPERATOR,
            is_active=True,
        )
        task = self._selection_task()
        self.client.force_authenticate(user=peer)
        url = reverse(
            "lens-copilot-selection-preview-task",
            kwargs={"task_id": task.id},
        )

        read_response = self.client.get(
            url,
            HTTP_X_ORG_KEY=self.organization.key,
        )
        cancel_response = self.client.delete(
            url,
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(read_response.status_code, 404)
        self.assertEqual(cancel_response.status_code, 404)

    def test_other_organization_cannot_read_preview_task(self):
        other_user = get_user_model().objects.create_user(
            username="selection-preview-other@example.test",
            email="selection-preview-other@example.test",
        )
        other_organization, _ = provision_registered_user_tenant(other_user)
        task = self._selection_task()
        self.client.force_authenticate(user=other_user)

        response = self.client.get(
            reverse(
                "lens-copilot-selection-preview-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=other_organization.key,
        )

        self.assertEqual(response.status_code, 404)

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "org_public_gateway_used_bytes",
        return_value=(20, False),
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview._effective_limits",
        return_value={
            "gateway_select_max_files": 100,
            "gateway_select_max_bytes": 1_000,
            "max_public_gateway_capacity_bytes": 100,
        },
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "gateway_readiness.require_copilot_gateway"
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "_configured_gateway_link_for_chat"
    )
    @patch("apps.lens_bridge.services.chat_selection_preview.get_quota_provider")
    def test_public_preview_returns_only_org_capacity(
        self,
        get_quota_provider,
        configured_gateway,
        _require_gateway,
        _limits,
        _used,
    ):
        get_quota_provider.return_value = SimpleNamespace()
        configured_gateway.return_value = SimpleNamespace(
            scope=LensGatewayLink.GatewayScope.PLATFORM,
        )

        payload = chat_selection_preview.admission_preview(
            organization=self.organization,
            user=self.user,
            gateway_mode="auto",
            gateway_link_id=None,
            file_count=20,
            size_bytes=30,
        )

        self.assertTrue(payload["admission"]["allowed"])
        self.assertEqual(payload["organization_capacity"]["used_bytes"], 20)
        self.assertEqual(payload["organization_capacity"]["remaining_bytes"], 80)
        self.assertEqual(payload["organization_capacity"]["after_create_bytes"], 50)
        self.assertNotIn("gateway_capacity", payload)
        self.assertNotIn("instance", str(payload))

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "org_public_gateway_used_bytes",
        return_value=(100, False),
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview._effective_limits",
        return_value={"max_public_gateway_capacity_bytes": 100},
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "gateway_readiness.require_copilot_gateway"
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "_configured_gateway_link_for_chat"
    )
    @patch("apps.lens_bridge.services.chat_selection_preview.get_quota_provider")
    def test_public_preview_matches_zero_byte_hard_capacity_semantics(
        self,
        get_quota_provider,
        configured_gateway,
        _require_gateway,
        _limits,
        _used,
    ):
        get_quota_provider.return_value = SimpleNamespace()
        configured_gateway.return_value = SimpleNamespace(
            scope=LensGatewayLink.GatewayScope.PLATFORM,
        )

        payload = chat_selection_preview.admission_preview(
            organization=self.organization,
            user=self.user,
            gateway_mode="auto",
            gateway_link_id=None,
            file_count=1,
            size_bytes=0,
        )

        self.assertFalse(payload["admission"]["allowed"])
        self.assertIn(
            "organization_capacity",
            payload["admission"]["reasons"],
        )

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "org_public_gateway_used_bytes",
        return_value=(0, False),
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview._effective_limits",
        return_value={},
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "gateway_readiness.require_copilot_gateway"
    )
    @patch(
        "apps.lens_bridge.services.chat_selection_preview."
        "_configured_gateway_link_for_chat"
    )
    @patch("apps.lens_bridge.services.chat_selection_preview.get_quota_provider")
    def test_public_preview_fails_closed_when_org_capacity_limit_is_unavailable(
        self,
        get_quota_provider,
        configured_gateway,
        _require_gateway,
        _limits,
        _used,
    ):
        get_quota_provider.return_value = SimpleNamespace()
        configured_gateway.return_value = SimpleNamespace(
            scope=LensGatewayLink.GatewayScope.PLATFORM,
        )

        payload = chat_selection_preview.admission_preview(
            organization=self.organization,
            user=self.user,
            gateway_mode="auto",
            gateway_link_id=None,
            file_count=0,
            size_bytes=0,
        )

        self.assertFalse(payload["admission"]["allowed"])
        self.assertFalse(payload["organization_capacity"]["limit_available"])
        self.assertIn(
            "organization_capacity_unavailable",
            payload["admission"]["reasons"],
        )


class ChatScopeDeduplicationTests(SimpleTestCase):
    def test_selection_request_tokens_isolate_same_user_browser_revisions(self):
        first = chat_selection_preview._scope_correlation_id(
            user_id=1,
            snapshot_id=2,
            directory_id=3,
            source_path="/documents",
            gateway_link_id=4,
            request_token=str(uuid.uuid4()),
            attempt=0,
        )
        second = chat_selection_preview._scope_correlation_id(
            user_id=1,
            snapshot_id=2,
            directory_id=3,
            source_path="/documents",
            gateway_link_id=4,
            request_token=str(uuid.uuid4()),
            attempt=0,
        )

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("selection:user:1:"))

    def test_parent_selection_replaces_covered_children(self):
        _request_hash, scopes = _chat_create_request_identity(
            backup_config_id=1,
            backup_source_snapshot_id=2,
            source_scopes=[
                {
                    "backup_snapshot_directory_id": 3,
                    "source_path": "/documents/contracts/a.pdf",
                },
                {
                    "backup_snapshot_directory_id": 3,
                    "source_path": "/documents",
                },
                {
                    "backup_snapshot_directory_id": 3,
                    "source_path": "/documents/contracts",
                },
            ],
            gateway_mode="auto",
            gateway_link_id=None,
            title=None,
        )

        self.assertEqual(
            scopes,
            [
                {
                    "backup_snapshot_directory_id": 3,
                    "source_path": "/documents",
                }
            ],
        )
