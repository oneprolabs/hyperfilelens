"""
``NodeAgentConsumer`` — Agent WSS session at ``/ws/node/agent/``.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.node import conf as node_conf
from apps.node.metrics import (
    AGENT_UPLINK_REJECTED,
    AGENT_WS_DISCONNECTS,
    TASK_RESULT_ACK_LATENCY,
    TASK_RESULT_BYTES,
    TASK_RESULT_TRUNCATED,
)
from apps.node.services.internal.agent_ws_auth import validate_agent_ws_credentials
from apps.node.services.internal.client_ip import resolve_agent_client_ip_from_scope
from apps.node.ws.groups import agent_group_name, ws_instance_group_name
from apps.node.ws.uplink import (
    apply_heartbeat_inventory_snapshot,
    handle_uplink,
    on_agent_connected,
    on_agent_disconnected,
    project_identical_task_result_recovery,
    trigger_task_result_followup,
)
from apps.node.ws.uplink_queue import enqueue_uplink, touch_heartbeat_fast
from apps.node.ws.wire import (
    TASK_RESULT_ACK_SUBPROTOCOL,
    WireType,
    dumps_wire,
    heartbeat_ack_wire,
    loads_json,
    parse_uplink,
    task_result_ack_wire,
)

logger = logging.getLogger(__name__)

_CLOSE_UNAUTHORIZED = 4401
_CLOSE_SERVICE_RESTART = 1012
_CLOSE_MESSAGE_TOO_LARGE = 1009
_MAX_AGENT_UPLINK_BYTES = 512 * 1024


@dataclass(frozen=True)
class _ConnectParams:
    node_id: int
    token: str


def _parse_connect_params(scope: dict) -> _ConnectParams | None:
    raw = (scope.get("query_string") or b"").decode("utf-8")
    qs = parse_qs(raw)
    node_raw = (qs.get("node_id") or qs.get("agent_id") or [""])[0].strip()
    token = (qs.get("token") or [""])[0].strip()
    if not node_raw.isdigit() or not token:
        return None
    return _ConnectParams(node_id=int(node_raw), token=token)


class NodeAgentConsumer(AsyncWebsocketConsumer):
    """Handle registry heartbeats and ``NodeTask`` uplink frames."""

    node_id: int
    agent_group: str

    async def connect(self) -> None:
        params = _parse_connect_params(self.scope)
        if params is None:
            logger.warning("agent ws connect rejected: missing node_id or token")
            await self.close(code=_CLOSE_UNAUTHORIZED)
            return

        ok = await database_sync_to_async(validate_agent_ws_credentials)(
            params.node_id,
            params.token,
        )
        if not ok:
            logger.warning(
                "agent ws connect rejected: invalid credentials node_id=%s",
                params.node_id,
            )
            await self.close(code=_CLOSE_UNAUTHORIZED)
            return

        self.node_id = params.node_id
        self.session_id = uuid.uuid4().hex
        self.agent_group = agent_group_name(node_id=self.node_id)
        self.ws_instance_group = ws_instance_group_name(
            ws_instance_id=node_conf.WS_INSTANCE_ID
        )
        await self.channel_layer.group_add(self.agent_group, self.channel_name)
        await self.channel_layer.group_add(
            self.ws_instance_group,
            self.channel_name,
        )
        offered = set(self.scope.get("subprotocols") or [])
        self.task_result_ack_enabled = TASK_RESULT_ACK_SUBPROTOCOL in offered
        await self.accept(
            subprotocol=TASK_RESULT_ACK_SUBPROTOCOL
            if self.task_result_ack_enabled
            else None
        )
        await database_sync_to_async(on_agent_connected)(
            node_id=self.node_id,
            session_id=self.session_id,
            client_ip=resolve_agent_client_ip_from_scope(self.scope),
        )

    async def disconnect(self, close_code: int) -> None:
        normalized_code = str(close_code) if close_code in {1000, 1001, 1006, 1009, 1012} else "other"
        AGENT_WS_DISCONNECTS.labels(code=normalized_code).inc()
        if getattr(self, "agent_group", ""):
            await self.channel_layer.group_discard(
                self.agent_group,
                self.channel_name,
            )
        if getattr(self, "ws_instance_group", ""):
            await self.channel_layer.group_discard(
                self.ws_instance_group,
                self.channel_name,
            )
        if getattr(self, "node_id", None) and getattr(self, "session_id", ""):
            await database_sync_to_async(on_agent_disconnected)(
                node_id=self.node_id,
                session_id=self.session_id,
            )

    async def receive(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,
    ) -> None:
        if text_data is None and bytes_data is not None:
            text_data = bytes_data.decode("utf-8", errors="replace")
        if not text_data:
            return

        frame_bytes = len(text_data.encode("utf-8"))
        if frame_bytes > _MAX_AGENT_UPLINK_BYTES:
            AGENT_UPLINK_REJECTED.labels(reason="message_too_large").inc()
            logger.warning(
                "agent uplink rejected node_id=%s reason=message_too_large bytes=%s max_bytes=%s",
                self.node_id,
                frame_bytes,
                _MAX_AGENT_UPLINK_BYTES,
            )
            await self.close(code=_CLOSE_MESSAGE_TOO_LARGE)
            return

        data = loads_json(text_data)
        if data is None:
            AGENT_UPLINK_REJECTED.labels(reason="invalid_json").inc()
            logger.warning(
                "agent uplink rejected node_id=%s reason=invalid_json bytes=%s",
                self.node_id,
                frame_bytes,
            )
            return

        message = parse_uplink(data)
        if message is None:
            AGENT_UPLINK_REJECTED.labels(reason="unknown_type").inc()
            logger.warning(
                "agent uplink rejected node_id=%s reason=unknown_type bytes=%s",
                self.node_id,
                frame_bytes,
            )
            return

        logger.debug(
            "agent uplink received node_id=%s type=%s task_id=%s bytes=%s",
            self.node_id,
            message.msg_type,
            message.task_id or "",
            frame_bytes,
        )

        result_received_at = time.monotonic()
        if message.msg_type == WireType.TASK_RESULT:
            TASK_RESULT_BYTES.observe(frame_bytes)
            if bool((message.result or {}).get("result_truncated")):
                TASK_RESULT_TRUNCATED.inc()
            logger.info(
                "agent task result received node_id=%s task_id=%s bytes=%s truncated=%s",
                self.node_id,
                message.task_id,
                frame_bytes,
                bool((message.result or {}).get("result_truncated")),
            )

        if message.msg_type == WireType.HEARTBEAT:
            await database_sync_to_async(touch_heartbeat_fast)(
                node_id=self.node_id,
                session_id=self.session_id,
            )
            await self.send(text_data=dumps_wire(heartbeat_ack_wire()))
            await database_sync_to_async(apply_heartbeat_inventory_snapshot)(
                node_id=self.node_id,
                inventory=message.heartbeat_payload,
            )
            await database_sync_to_async(enqueue_uplink)(
                node_id=self.node_id, message=message
            )
            return

        # Task frames drive watchdog + lifecycle; must persist synchronously.
        try:
            task = await database_sync_to_async(handle_uplink)(
                node_id=self.node_id,
                message=message,
            )
        except Exception:
            AGENT_UPLINK_REJECTED.labels(reason="persistence_failed").inc()
            logger.exception(
                "agent uplink persist failed node_id=%s task_id=%s type=%s bytes=%s category=persistence_failed",
                self.node_id,
                message.task_id,
                message.msg_type,
                frame_bytes,
            )
            return

        if message.msg_type != WireType.TASK_RESULT or task is None:
            return
        if self.task_result_ack_enabled:
            try:
                await self.send(
                    text_data=dumps_wire(task_result_ack_wire(task_id=task.id))
                )
                TASK_RESULT_ACK_LATENCY.observe(time.monotonic() - result_received_at)
            except Exception:
                logger.warning(
                    "agent task result ACK send failed node_id=%s task_id=%s",
                    self.node_id,
                    task.id,
                    exc_info=True,
                )
        if bool(getattr(task, "_result_retransmission_unchanged", False)):
            try:
                await database_sync_to_async(project_identical_task_result_recovery)(
                    node_task=task
                )
            except Exception:
                logger.exception(
                    "repository health retransmission projection failed node_id=%s task_id=%s",
                    self.node_id,
                    task.id,
                )
            return
        try:
            await database_sync_to_async(trigger_task_result_followup)(
                node_task_id=task.id
            )
        except Exception:
            logger.exception(
                "agent task result follow-up failed node_id=%s task_id=%s",
                self.node_id,
                task.id,
            )

    async def node_downlink(self, event: dict) -> None:
        """Deliver a flat downlink frame (``task.command`` / ``task.cancel``)."""
        body = event.get("message")
        if not isinstance(body, dict):
            return
        try:
            await self.send(text_data=dumps_wire(body))
        except Exception:
            logger.warning(
                "agent downlink send failed node_id=%s task_id=%s kind=%s",
                getattr(self, "node_id", "-"),
                body.get("task_id"),
                body.get("kind"),
                exc_info=True,
            )

    async def deployment_drain(self, event: dict) -> None:
        """Ask Agents to reconnect through stable Nginx during a color cutover."""
        logger.info(
            "closing agent websocket for deployment drain node_id=%s reason=%s",
            getattr(self, "node_id", "-"),
            event.get("reason", "service restart"),
        )
        await self.close(code=_CLOSE_SERVICE_RESTART)
