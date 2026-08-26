from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.ws.node_agent import _MAX_AGENT_UPLINK_BYTES, NodeAgentConsumer
from apps.node.ws.uplink import (
    TaskResultHandling,
    _handle_task_result_delivery,
    trigger_task_result_followup,
)
from apps.node.ws.wire import (
    TASK_RESULT_ACK_SUBPROTOCOL,
    ParsedUplink,
    WireType,
)


def _immediate_database_sync_to_async(func):
    """Keep consumer unit tests independent of asgiref's thread executor."""

    async def invoke(*args, **kwargs):
        return func(*args, **kwargs)

    return invoke


class NodeAgentTaskResultAckTests(SimpleTestCase):

    async def test_oversized_uplink_is_closed_without_parsing(self):
        consumer = NodeAgentConsumer()
        consumer.node_id = 7
        consumer.close = AsyncMock()

        with patch("apps.node.ws.node_agent.loads_json") as loads_json:
            await consumer.receive(text_data="x" * (_MAX_AGENT_UPLINK_BYTES + 1))

        loads_json.assert_not_called()
        consumer.close.assert_awaited_once_with(code=1009)

    async def test_deployment_drain_closes_with_service_restart_code(self):
        consumer = NodeAgentConsumer()
        consumer.node_id = 7
        consumer.close = AsyncMock()

        await consumer.deployment_drain({"reason": "test cutover"})

        consumer.close.assert_awaited_once_with(code=1012)

    async def test_negotiates_ack_subprotocol_when_agent_offers_it(self):
        consumer = NodeAgentConsumer()
        consumer.scope = {
            "query_string": b"node_id=7&token=test-token",
            "subprotocols": [TASK_RESULT_ACK_SUBPROTOCOL],
            "client": ("127.0.0.1", 1234),
        }
        consumer.channel_name = "test-channel"
        consumer.channel_layer = SimpleNamespace(group_add=AsyncMock())
        consumer.accept = AsyncMock()

        with (
            patch(
                "apps.node.ws.node_agent.validate_agent_ws_credentials",
                return_value=True,
            ),
            patch(
                "apps.node.ws.node_agent.database_sync_to_async",
                side_effect=_immediate_database_sync_to_async,
            ),
            patch("apps.node.ws.node_agent.on_agent_connected"),
        ):
            await consumer.connect()

        self.assertTrue(consumer.task_result_ack_enabled)
        consumer.accept.assert_awaited_once_with(
            subprotocol=TASK_RESULT_ACK_SUBPROTOCOL
        )

    async def test_ack_is_sent_after_commit_and_before_projection_followup(self):
        events: list[str] = []
        task = SimpleNamespace(id="550e8400-e29b-41d4-a716-446655440000")
        consumer = NodeAgentConsumer()
        consumer.node_id = 7
        consumer.task_result_ack_enabled = True

        async def send(*, text_data=None, bytes_data=None, close=False):
            del bytes_data, close
            events.append("ack")
            body = json.loads(text_data)
            self.assertEqual(body["type"], "task.result.ack")
            self.assertEqual(body["task_id"], task.id)

        consumer.send = send

        def commit(**kwargs):
            del kwargs
            events.append("commit")
            return TaskResultHandling(
                task_id=task.id,
                disposition="accepted",
                node_task=task,
            )

        def followup(**kwargs):
            del kwargs
            events.append("followup")

        with (
            patch("apps.node.ws.node_agent.handle_uplink", side_effect=commit),
            patch(
                "apps.node.ws.node_agent.database_sync_to_async",
                side_effect=_immediate_database_sync_to_async,
            ),
            patch(
                "apps.node.ws.node_agent.trigger_task_result_followup",
                side_effect=followup,
            ),
            patch("apps.node.ws.node_agent.TASK_RESULT_BYTES") as result_bytes,
            patch("apps.node.ws.node_agent.TASK_RESULT_TRUNCATED") as result_truncated,
            patch("apps.node.ws.node_agent.TASK_RESULT_ACK_LATENCY") as ack_latency,
        ):
            await consumer.receive(
                text_data=json.dumps(
                    {
                        "type": "task.result",
                        "task_id": task.id,
                        "status": "success",
                        "result": {
                            "kopia_snapshot_id": "snap-1",
                            "result_truncated": True,
                        },
                    }
                )
            )

        self.assertEqual(events, ["commit", "ack", "followup"])
        result_bytes.observe.assert_called_once()
        result_truncated.inc.assert_called_once_with()
        ack_latency.observe.assert_called_once()

    async def test_database_failure_sends_no_ack(self):
        consumer = NodeAgentConsumer()
        consumer.node_id = 7
        consumer.task_result_ack_enabled = True
        consumer.send = AsyncMock()

        with (
            patch(
                "apps.node.ws.node_agent.handle_uplink",
                side_effect=DatabaseError("database unavailable"),
            ),
            patch(
                "apps.node.ws.node_agent.database_sync_to_async",
                side_effect=_immediate_database_sync_to_async,
            ),
            patch("apps.node.ws.node_agent.trigger_task_result_followup") as followup,
        ):
            await consumer.receive(
                text_data=json.dumps(
                    {
                        "type": "task.result",
                        "task_id": "550e8400-e29b-41d4-a716-446655440000",
                        "status": "success",
                        "result": {},
                    }
                )
            )

        consumer.send.assert_not_awaited()
        followup.assert_not_called()

    async def test_identical_retransmission_is_acked_without_followup(self):
        task = SimpleNamespace(
            id="550e8400-e29b-41d4-a716-446655440000",
            _result_retransmission_unchanged=True,
        )
        consumer = NodeAgentConsumer()
        consumer.node_id = 7
        consumer.task_result_ack_enabled = True
        consumer.send = AsyncMock()

        with (
            patch(
                "apps.node.ws.node_agent.handle_uplink",
                return_value=TaskResultHandling(
                    task_id=task.id,
                    disposition="duplicate",
                    node_task=task,
                ),
            ),
            patch(
                "apps.node.ws.node_agent.database_sync_to_async",
                side_effect=_immediate_database_sync_to_async,
            ),
            patch(
                "apps.node.ws.node_agent.project_identical_task_result_recovery"
            ) as recovery,
            patch("apps.node.ws.node_agent.trigger_task_result_followup") as followup,
        ):
            await consumer.receive(
                text_data=json.dumps(
                    {
                        "type": "task.result",
                        "task_id": task.id,
                        "status": "success",
                        "result": {},
                    }
                )
            )

        consumer.send.assert_awaited_once()
        recovery.assert_called_once_with(node_task=task)
        followup.assert_not_called()

    async def test_permanently_discarded_result_is_acked_without_followup(self):
        task_id = "550e8400-e29b-41d4-a716-446655440000"
        consumer = NodeAgentConsumer()
        consumer.node_id = 7
        consumer.task_result_ack_enabled = True
        consumer.send = AsyncMock()

        with (
            patch(
                "apps.node.ws.node_agent.handle_uplink",
                return_value=TaskResultHandling(
                    task_id=task_id,
                    disposition="discarded_stale_owner",
                ),
            ),
            patch(
                "apps.node.ws.node_agent.database_sync_to_async",
                side_effect=_immediate_database_sync_to_async,
            ),
            patch("apps.node.ws.node_agent.trigger_task_result_followup") as followup,
        ):
            await consumer.receive(
                text_data=json.dumps(
                    {
                        "type": "task.result",
                        "task_id": task_id,
                        "status": "success",
                        "result": {},
                    }
                )
            )

        body = json.loads(consumer.send.await_args.kwargs["text_data"])
        self.assertEqual(body, {"type": "task.result.ack", "task_id": task_id})
        followup.assert_not_called()

    async def test_owner_mismatch_is_not_acked_to_agent(self):
        task_id = "550e8400-e29b-41d4-a716-446655440000"
        consumer = NodeAgentConsumer()
        consumer.node_id = 7
        consumer.task_result_ack_enabled = True
        consumer.send = AsyncMock()

        with (
            patch(
                "apps.node.ws.node_agent.handle_uplink",
                return_value=TaskResultHandling(
                    task_id=task_id,
                    disposition="discarded_owner_mismatch",
                    acknowledge_agent=False,
                ),
            ),
            patch(
                "apps.node.ws.node_agent.database_sync_to_async",
                side_effect=_immediate_database_sync_to_async,
            ),
        ):
            await consumer.receive(
                text_data=json.dumps(
                    {
                        "type": "task.result",
                        "task_id": task_id,
                        "status": "success",
                        "result": {},
                    }
                )
            )

        consumer.send.assert_not_awaited()


class NodeTaskResultDispositionTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="node-task-result-disposition-org",
            name="Node Task Result Disposition Org",
        )
        self.current_node = Node.objects.create(
            organization=self.organization,
            name="current-result-agent",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.owner_node = Node.objects.create(
            organization=self.organization,
            name="original-result-agent",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
        )

    def _task(self, *, node: Node) -> NodeTask:
        return NodeTask.objects.create(
            organization=self.organization,
            node=node,
            kind="explorer.list",
            watchdog_deadline_at=timezone.now(),
        )

    @staticmethod
    def _message(task_id: str) -> ParsedUplink:
        return ParsedUplink(
            msg_type=WireType.TASK_RESULT,
            task_id=task_id,
            status="success",
            result={"entries": []},
        )

    @patch("apps.node.ws.uplink.TASK_RESULT_DISPOSITIONS")
    def test_unknown_task_is_permanently_discarded(self, dispositions):
        task_id = str(uuid.uuid4())

        handled = _handle_task_result_delivery(
            node_id=self.current_node.id,
            message=self._message(task_id),
        )

        self.assertEqual(handled.task_id, task_id)
        self.assertEqual(handled.disposition, "discarded_unknown")
        self.assertIsNone(handled.node_task)
        dispositions.labels.assert_called_once_with(
            disposition="discarded_unknown"
        )

    @patch("apps.node.ws.uplink.TASK_RESULT_DISPOSITIONS")
    def test_invalid_task_id_is_permanently_discarded(self, dispositions):
        handled = _handle_task_result_delivery(
            node_id=self.current_node.id,
            message=self._message("not-a-uuid"),
        )

        self.assertEqual(handled.task_id, "not-a-uuid")
        self.assertEqual(handled.disposition, "discarded_invalid")
        self.assertIsNone(handled.node_task)
        dispositions.labels.assert_called_once_with(
            disposition="discarded_invalid"
        )

    @patch("apps.node.ws.uplink.TASK_RESULT_DISPOSITIONS")
    def test_deleted_owner_result_is_permanently_discarded(self, dispositions):
        task = self._task(node=self.owner_node)
        self.owner_node.soft_delete()

        handled = _handle_task_result_delivery(
            node_id=self.current_node.id,
            message=self._message(str(task.id)),
        )

        self.assertEqual(handled.disposition, "discarded_stale_owner")
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.PENDING)
        dispositions.labels.assert_called_once_with(
            disposition="discarded_stale_owner"
        )

    @patch("apps.node.ws.uplink.TASK_RESULT_DISPOSITIONS")
    def test_deleted_connected_node_result_is_not_applied(self, dispositions):
        task = self._task(node=self.current_node)
        node_id = self.current_node.id
        self.current_node.soft_delete()

        handled = _handle_task_result_delivery(
            node_id=node_id,
            message=self._message(str(task.id)),
        )

        self.assertEqual(handled.disposition, "discarded_stale_owner")
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.PENDING)
        dispositions.labels.assert_called_once_with(
            disposition="discarded_stale_owner"
        )

    @patch("apps.node.ws.uplink.TASK_RESULT_DISPOSITIONS")
    def test_active_owner_mismatch_is_not_applied(self, dispositions):
        task = self._task(node=self.owner_node)

        handled = _handle_task_result_delivery(
            node_id=self.current_node.id,
            message=self._message(str(task.id)),
        )

        self.assertEqual(handled.disposition, "discarded_owner_mismatch")
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.PENDING)
        dispositions.labels.assert_called_once_with(
            disposition="discarded_owner_mismatch"
        )

    @patch("apps.node.ws.uplink.TASK_RESULT_DISPOSITIONS")
    def test_soft_deleted_task_is_not_applied(self, dispositions):
        task = self._task(node=self.current_node)
        task.soft_delete()

        handled = _handle_task_result_delivery(
            node_id=self.current_node.id,
            message=self._message(str(task.id)),
        )

        self.assertEqual(handled.disposition, "discarded_deleted_task")
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.PENDING)
        dispositions.labels.assert_called_once_with(
            disposition="discarded_deleted_task"
        )

    @patch("apps.node.ws.uplink.TASK_RESULT_DISPOSITIONS")
    def test_current_owner_result_is_applied(self, dispositions):
        task = self._task(node=self.current_node)

        handled = _handle_task_result_delivery(
            node_id=self.current_node.id,
            message=self._message(str(task.id)),
        )

        self.assertEqual(handled.disposition, "accepted")
        self.assertEqual(handled.node_task.id, task.id)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.SUCCESS)
        dispositions.labels.assert_called_once_with(disposition="accepted")


class NodeTaskResultFollowupTests(TestCase):
    def test_normal_result_projects_new_async_domains(self):
        organization = Organization.objects.create(
            key="node-task-result-followup-org",
            name="Node Task Result Follow-up Org",
        )
        node = Node.objects.create(
            organization=organization,
            name="node-task-result-followup-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        node_task = NodeTask.objects.create(
            organization=organization,
            node=node,
            kind="nas.test",
            correlation_type="source.connection_probe",
            correlation_id="1",
            status=NodeTask.Status.SUCCESS,
            dispatched_at=timezone.now(),
            accepted_at=timezone.now(),
            watchdog_deadline_at=timezone.now(),
        )

        with (
            patch(
                "apps.source.tasks.connection_probe."
                "project_source_connection_probe"
            ) as project_source,
            patch(
                "apps.protection.services.snapshot_delete_execution."
                "queue_snapshot_delete_result_followup"
            ) as queue_snapshot,
        ):
            trigger_task_result_followup(node_task_id=node_task.id)

        project_source.assert_called_once_with(node_task=node_task)
        queue_snapshot.assert_called_once_with(node_task=node_task)
