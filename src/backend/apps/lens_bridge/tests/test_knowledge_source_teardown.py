import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import knowledge_source_teardown, teardown_blocking
from apps.node.models import Node
from apps.lens_bridge.tasks.chat_lifecycle import (
    reconcile_lens_resource_teardowns_task,
)
from apps.lens_bridge.tasks.knowledge_source_teardown import (
    due_knowledge_source_teardown_ids,
)


class KnowledgeSourceDeleteApiTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="ks-delete-api",
            name="KS delete API",
        )
        self.user = get_user_model().objects.create_user(
            username="ks-delete-api@example.test",
            email="ks-delete-api@example.test",
        )
        Membership.objects.create(
            organization=self.organization,
            user=self.user,
            role=Membership.Role.OWNER,
        )
        self.gateway = Node.objects.create(
            organization=self.organization,
            name="private-gateway",
            role=Node.Role.GATEWAY,
        )
        self.gateway_link = LensGatewayLink.objects.create(
            organization=self.organization,
            gateway=self.gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            workspace_root="/workspace/org-1/data",
        )
        self.knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.organization,
            name="Local KS",
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            source_path="/workspace/org-1/data/documents",
            status=LensKnowledgeSource.Status.READY,
            created_by=self.user,
        )
        LensWorkspaceBinding.objects.create(
            organization=self.organization,
            knowledge_source=self.knowledge_source,
            gateway_link=self.gateway_link,
            execution_organization_id=self.organization.id,
            execution_node_id=self.gateway.id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.GATEWAY_LOCAL,
            workspace_root="/workspace/org-1/data",
            state=LensWorkspaceBinding.State.READY,
            identity_status=LensWorkspaceBinding.IdentityStatus.NOT_APPLICABLE,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @mock.patch(
        "apps.lens_bridge.tasks.knowledge_source_teardown."
        "execute_knowledge_source_teardown_task.delay"
    )
    def test_delete_returns_202_without_running_teardown_in_request(self, delay):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                reverse(
                    "lens-knowledge-source-detail",
                    kwargs={"pk": self.knowledge_source.id},
                ),
                HTTP_X_ORG_KEY=self.organization.key,
            )

        self.assertEqual(response.status_code, 202)
        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.lifecycle_status,
            LensKnowledgeSource.LifecycleStatus.DELETING,
        )
        delay.assert_called_once_with(
            knowledge_source_id=self.knowledge_source.id
        )

    def test_direct_delete_is_blocked_while_chat_owns_knowledge_source(self):
        LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )

        response = self.client.delete(
            reverse(
                "lens-knowledge-source-detail",
                kwargs={"pk": self.knowledge_source.id},
            ),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 400)

    @mock.patch(
        "apps.lens_bridge.tasks.knowledge_source_teardown."
        "execute_knowledge_source_teardown_task.delay"
    )
    def test_direct_delete_allowed_when_owning_chat_is_already_deleting(
        self, delay
    ):
        LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                reverse(
                    "lens-knowledge-source-detail",
                    kwargs={"pk": self.knowledge_source.id},
                ),
                HTTP_X_ORG_KEY=self.organization.key,
            )

        self.assertEqual(response.status_code, 202)
        delay.assert_called_once_with(
            knowledge_source_id=self.knowledge_source.id
        )

    @mock.patch(
        "apps.node.services.internal.node_workload.get_node_workload_blockers",
        return_value=[],
    )
    def test_standalone_teardown_proceeds_while_owner_chat_is_deleting(
        self, _blockers
    ):
        """Chat+KS teardown race must not deadlock on a deleting owner chat."""
        LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
        )
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.save(
            update_fields=["lifecycle_status", "updated_at"]
        )

        result = knowledge_source_teardown.run_knowledge_source_teardown(
            knowledge_source_id=self.knowledge_source.id
        )

        self.assertEqual(result["status"], "deleted")
        self.knowledge_source.refresh_from_db()
        self.assertTrue(self.knowledge_source.is_deleted)

    @mock.patch(
        "apps.node.services.internal.node_workload.get_node_workload_blockers",
        return_value=[],
    )
    def test_standalone_teardown_still_blocked_by_ready_chat(self, _blockers):
        LensSessionLink.objects.create(
            organization=self.organization,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.save(
            update_fields=["lifecycle_status", "updated_at"]
        )

        with self.assertRaises(
            knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError
        ) as ctx:
            knowledge_source_teardown.run_knowledge_source_teardown(
                knowledge_source_id=self.knowledge_source.id
            )

        self.assertIn("active Chat", str(ctx.exception))
        self.knowledge_source.refresh_from_db()
        self.assertFalse(self.knowledge_source.is_deleted)

    @mock.patch(
        "apps.lens_bridge.tasks.chat_lifecycle."
        "execute_copilot_chat_teardown_task.delay"
    )
    @mock.patch(
        "apps.lens_bridge.tasks.knowledge_source_teardown."
        "execute_knowledge_source_teardown_task.delay"
    )
    def test_reconciler_queues_due_knowledge_source(self, ks_delay, chat_delay):
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.teardown_next_retry_at = timezone.now()
        self.knowledge_source.save(
            update_fields=[
                "lifecycle_status",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )

        result = reconcile_lens_resource_teardowns_task(limit=10)

        self.assertEqual(result["queued"], 1)
        ks_delay.assert_called_once_with(
            knowledge_source_id=self.knowledge_source.id
        )
        chat_delay.assert_not_called()

    @mock.patch(
        "apps.node.services.internal.node_workload.get_node_workload_blockers"
    )
    def test_teardown_waits_for_active_restore_then_converges(self, blockers):
        blockers.return_value = [mock.MagicMock(code="restore_active")]
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.save(
            update_fields=["lifecycle_status", "updated_at"]
        )

        with self.assertRaises(
            knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError
        ):
            knowledge_source_teardown.run_knowledge_source_teardown(
                knowledge_source_id=self.knowledge_source.id
            )

        self.knowledge_source.refresh_from_db()
        self.knowledge_source.teardown_next_retry_at = timezone.now()
        self.knowledge_source.save(
            update_fields=["teardown_next_retry_at", "updated_at"]
        )
        blockers.return_value = []
        result = knowledge_source_teardown.run_knowledge_source_teardown(
            knowledge_source_id=self.knowledge_source.id
        )

        self.assertEqual(result["status"], "deleted")

    @mock.patch.object(teardown_blocking, "INTERVENTION_ATTEMPT_THRESHOLD", 2)
    @mock.patch.object(teardown_blocking, "INTERVENTION_AGE_SECONDS", 1)
    @mock.patch(
        "apps.node.services.internal.node_workload.get_node_workload_blockers",
        return_value=[mock.MagicMock(code="restore_active")],
    )
    def test_persistent_blocker_stops_reconciler_until_operator_recovery(
        self,
        _blockers,
    ):
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.save(
            update_fields=[
                "lifecycle_status",
                "updated_at",
            ]
        )

        with self.assertRaises(
            knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError
        ):
            knowledge_source_teardown.run_knowledge_source_teardown(
                knowledge_source_id=self.knowledge_source.id
            )

        self.knowledge_source.refresh_from_db()
        state = dict(self.knowledge_source.teardown_state_json)
        state["blocking"]["first_seen_at"] = (
            timezone.now() - timedelta(seconds=2)
        ).isoformat()
        self.knowledge_source.teardown_state_json = state
        self.knowledge_source.teardown_next_retry_at = timezone.now()
        self.knowledge_source.save(
            update_fields=[
                "teardown_state_json",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )

        with self.assertRaises(
            knowledge_source_teardown.KnowledgeSourceTeardownIncompleteError
        ):
            knowledge_source_teardown.run_knowledge_source_teardown(
                knowledge_source_id=self.knowledge_source.id
            )

        self.knowledge_source.refresh_from_db()
        self.assertEqual(
            self.knowledge_source.teardown_state_json["blocking"]["reason"],
            "validate_gateway_workload",
        )
        self.assertTrue(
            self.knowledge_source.teardown_state_json["blocking"][
                "intervention_required"
            ]
        )
        self.assertIsNone(self.knowledge_source.teardown_next_retry_at)
        self.assertNotIn(
            self.knowledge_source.id,
            due_knowledge_source_teardown_ids(limit=10),
        )


class KnowledgeSourceTeardownConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_two_workers_receive_only_one_claim(self):
        organization = Organization.objects.create(
            key="ks-teardown-concurrency",
            name="KS teardown concurrency",
        )
        user = get_user_model().objects.create_user(
            username="ks-teardown-concurrency@example.test",
            email="ks-teardown-concurrency@example.test",
        )
        gateway = Node.objects.create(
            organization=organization,
            name="gateway",
            role=Node.Role.GATEWAY,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=organization,
            gateway=gateway,
            owner_user=user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        knowledge_source = LensKnowledgeSource.objects.create(
            organization=organization,
            name="Concurrent KS",
            gateway=gateway,
            gateway_link=gateway_link,
            source_path="/workspace/data",
            status=LensKnowledgeSource.Status.READY,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
        )
        barrier = threading.Barrier(2)

        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return knowledge_source_teardown._claim(knowledge_source.id)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: claim(), range(2)))

        self.assertEqual(
            sum(1 for token, _status in results if token is not None),
            1,
        )
        self.assertEqual(
            {status for token, status in results if token is None},
            {"busy"},
        )
