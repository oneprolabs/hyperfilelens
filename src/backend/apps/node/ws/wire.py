"""
WebSocket wire format (control plane ↔ Agent).

Uplink and downlink JSON uses a top-level ``type`` field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from apps.node.models import NodeTask


class WireType(StrEnum):
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat.ack"
    TASK_COMMAND = "task.command"
    TASK_ACCEPTED = "task.accepted"
    TASK_CANCEL = "task.cancel"
    TASK_PROGRESS = "task.progress"
    TASK_ALIVE = "task.alive"
    TASK_RESULT = "task.result"
    TASK_RESULT_ACK = "task.result.ack"


TASK_RESULT_ACK_SUBPROTOCOL = "hfl.task-result-ack.v1"


@dataclass(frozen=True)
class ParsedUplink:
    """Normalized Agent → control plane frame."""

    msg_type: WireType
    task_id: str | None = None
    progress: dict[str, Any] | None = None
    is_alive: bool = False
    status: str | None = None
    result: dict[str, Any] | None = None
    error: str = ""
    heartbeat_payload: dict[str, Any] | None = None
    received_at: datetime | None = None


def _coerce_dict(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return {field: value}


def loads_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_uplink(data: dict[str, Any]) -> ParsedUplink | None:
    msg_type = str(data.get("type", "")).strip().lower()
    if not msg_type:
        return None

    try:
        kind = WireType(msg_type)
    except ValueError:
        return None

    if kind == WireType.HEARTBEAT:
        payload = data.get("payload")
        if payload is None:
            payload = data.get("inventory")
        hb_payload = _coerce_dict(payload, field="value") if payload is not None else None
        if hb_payload == {}:
            hb_payload = None
        return ParsedUplink(msg_type=kind, heartbeat_payload=hb_payload)

    task_id = data.get("task_id")
    if kind in (
        WireType.TASK_ACCEPTED,
        WireType.TASK_PROGRESS,
        WireType.TASK_ALIVE,
        WireType.TASK_RESULT,
    ):
        if not task_id:
            return None

    if kind == WireType.TASK_ACCEPTED:
        return ParsedUplink(
            msg_type=kind,
            task_id=str(task_id),
            status=str(data.get("status") or "running"),
        )

    if kind == WireType.TASK_PROGRESS:
        progress = data.get("progress")
        if progress is None:
            progress = data.get("payload")
        return ParsedUplink(
            msg_type=kind,
            task_id=str(task_id),
            progress=_coerce_dict(progress, field="value"),
            is_alive=False,
        )

    if kind == WireType.TASK_ALIVE:
        return ParsedUplink(
            msg_type=kind,
            task_id=str(task_id),
            progress={},
            is_alive=True,
        )

    if kind == WireType.TASK_RESULT:
        result = data.get("result")
        if result is None:
            result = data.get("payload")
        return ParsedUplink(
            msg_type=kind,
            task_id=str(task_id),
            status=str(data.get("status") or NodeTask.Status.SUCCESS),
            result=_coerce_dict(result, field="value"),
            error=str(data.get("error") or data.get("message") or ""),
        )

    return None


def dumps_wire(message: dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False)


def task_command_wire(
    *,
    task_id: UUID | str,
    kind: str,
    node_id: int,
    payload: dict[str, Any] | None = None,
    correlation_type: str = "",
    correlation_id: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": WireType.TASK_COMMAND,
        "task_id": str(task_id),
        "kind": kind,
        "node_id": node_id,
        "payload": payload or {},
    }
    if correlation_type:
        body["correlation_type"] = correlation_type
    if correlation_id:
        body["correlation_id"] = correlation_id
    if trace_id:
        body["trace_id"] = trace_id
    return body


def task_cancel_wire(*, task_id: UUID | str, node_id: int) -> dict[str, Any]:
    return {
        "type": WireType.TASK_CANCEL,
        "task_id": str(task_id),
        "node_id": node_id,
    }


def heartbeat_ack_wire() -> dict[str, Any]:
    return {"type": WireType.HEARTBEAT_ACK}


def task_result_ack_wire(*, task_id: UUID | str) -> dict[str, Any]:
    return {
        "type": WireType.TASK_RESULT_ACK,
        "task_id": str(task_id),
    }
