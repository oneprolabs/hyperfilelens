from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase

from apps.node.ws.node_agent import _MAX_AGENT_UPLINK_BYTES, NodeAgentConsumer
from apps.node.ws.wire import TASK_RESULT_ACK_SUBPROTOCOL


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
            return task

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
