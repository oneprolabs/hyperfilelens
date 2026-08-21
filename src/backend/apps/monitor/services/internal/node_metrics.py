"""Map node heartbeat / websocket payloads to tenant resource metrics."""

from __future__ import annotations

from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.alert.constants import ResourceType
from apps.monitor.services.internal.resource_metrics import record_resource_metric
from apps.node.models import Node
from apps.node.models.base import NodeRole

_ROLE_TO_RESOURCE_TYPE = {
    NodeRole.PROXY: ResourceType.SYNC_PROXY,
    NodeRole.AGENT: ResourceType.AGENT_PROXY,
    NodeRole.GATEWAY: ResourceType.GATEWAY,
}


def _monitor_sample_is_stale(*, previous: object, incoming: str) -> bool:
    if not incoming:
        return False
    previous_text = str(previous or "").strip()
    if not previous_text:
        return False
    if incoming == previous_text:
        return True
    previous_at = parse_datetime(previous_text)
    incoming_at = parse_datetime(incoming)
    if previous_at is None or incoming_at is None:
        return False
    try:
        return incoming_at <= previous_at
    except TypeError:
        # Legacy timestamps may be timezone-naive. Accept the new sample and
        # replace the marker with the Agent's current RFC 3339 timestamp.
        return False


def _normalize_mountpoint(mountpoint: str) -> str:
    clean = str(mountpoint or "").strip()
    if len(clean) == 2 and clean[1] == ":":
        return f"{clean[0].upper()}:\\"
    if len(clean) >= 3 and clean[1] == ":" and clean[2] in ("\\", "/"):
        return f"{clean[0].upper()}:\\"
    return clean


def _disk_capacity_from_disks(disks: list) -> dict[str, int]:
    """Sum per-volume capacity for node list Capacity column."""
    total = used = free = 0
    count = 0
    seen: set[str] = set()
    for row in disks:
        if not isinstance(row, dict):
            continue
        mp = _normalize_mountpoint(str(row.get("mountpoint") or ""))
        if not mp or mp in seen:
            continue
        seen.add(mp)
        raw_total = row.get("total")
        if raw_total is None:
            continue
        try:
            total += int(raw_total)
            used += int(row.get("used") or 0)
            free += int(row.get("free") or 0)
            count += 1
        except (TypeError, ValueError):
            continue
    if count <= 0 or total <= 0:
        return {}
    return {
        "disk_total_bytes": total,
        "disk_used_bytes": used,
        "disk_free_bytes": free,
        "disk_count": count,
    }


def _hardware_inventory_summary(
    sample: dict,
    *,
    preserve_storage_inventory: bool = False,
) -> dict[str, int]:
    """Extract stable hardware fields for node list display."""
    summary: dict[str, int] = {}
    cpu = sample.get("cpu") if isinstance(sample.get("cpu"), dict) else {}
    memory = sample.get("memory") if isinstance(sample.get("memory"), dict) else {}
    disks = sample.get("disks") if isinstance(sample.get("disks"), list) else []

    for key in ("logical_cores", "logicalCores"):
        if cpu.get(key) is not None:
            summary["cpu_cores"] = int(cpu[key])
            break
    if memory.get("total") is not None:
        summary["memory_total_bytes"] = int(memory["total"])
    if not preserve_storage_inventory:
        summary.update(_disk_capacity_from_disks(disks))
        if "disk_count" not in summary and disks:
            summary["disk_count"] = len(disks)
    return summary


def _scalar_metrics(sample: dict) -> dict:
    """Extract alert-friendly scalar keys from a full monitor sample."""
    cpu = sample.get("cpu") if isinstance(sample.get("cpu"), dict) else {}
    memory = sample.get("memory") if isinstance(sample.get("memory"), dict) else {}
    swap = sample.get("swap") if isinstance(sample.get("swap"), dict) else {}
    disks = sample.get("disks") if isinstance(sample.get("disks"), list) else []
    networks = (
        sample.get("networks") if isinstance(sample.get("networks"), list) else []
    )
    load = (
        sample.get("load_average")
        if isinstance(sample.get("load_average"), list)
        else []
    )

    scalars: dict[str, float] = {}
    if cpu.get("usage_percent") is not None:
        scalars["cpu_usage"] = float(cpu["usage_percent"])
    if memory.get("percent") is not None:
        scalars["memory_usage"] = float(memory["percent"])
    if swap.get("percent") is not None:
        scalars["swap_usage"] = float(swap["percent"])

    percents = [
        float(d.get("percent"))
        for d in disks
        if isinstance(d, dict) and d.get("percent") is not None
    ]
    if percents:
        scalars["disk_usage"] = max(percents)

    if networks:
        rx = sum(int(n.get("bytes_recv") or 0) for n in networks if isinstance(n, dict))
        tx = sum(int(n.get("bytes_sent") or 0) for n in networks if isinstance(n, dict))
        scalars["network_rx"] = float(rx)
        scalars["network_tx"] = float(tx)

    if len(load) > 0:
        scalars["load_1m"] = float(load[0])
    if len(load) > 1:
        scalars["load_5m"] = float(load[1])
    if len(load) > 2:
        scalars["load_15m"] = float(load[2])

    for key, value in sample.items():
        if key in scalars or key in {
            "cpu",
            "memory",
            "swap",
            "disks",
            "disk_io",
            "networks",
            "load_average",
            "timestamp",
            "metadata",
            "boot_time",
        }:
            continue
        if isinstance(value, (int, float)):
            scalars[key] = float(value)
    return scalars


@transaction.atomic
def ingest_node_monitor_sample(*, node: Node, sample: dict) -> None:
    """Persist a full host monitor sample for charts and alert evaluation."""
    if not isinstance(sample, dict) or not sample:
        return

    node = Node.objects.select_for_update().filter(pk=node.pk).first()
    if node is None:
        return
    resource_type = _ROLE_TO_RESOURCE_TYPE.get(node.role)
    if not resource_type:
        return

    meta = dict(node.metadata or {})
    sample_timestamp = str(sample.get("timestamp") or "").strip()
    if _monitor_sample_is_stale(
        previous=meta.get("monitor_sample_timestamp"),
        incoming=sample_timestamp,
    ):
        return

    payload = dict(sample)
    payload.update(_scalar_metrics(sample))

    record_resource_metric(
        organization_id=node.organization_id,
        resource_type=resource_type,
        resource_id=str(node.id),
        resource_name=node.name,
        metrics=payload,
    )

    meta["metrics"] = {k: payload[k] for k in _scalar_metrics(sample)}
    sample_metadata = (
        sample.get("metadata") if isinstance(sample.get("metadata"), dict) else {}
    )
    collection_status = str(sample_metadata.get("collection_status") or "").strip()
    unavailable_metrics = sample_metadata.get("unavailable_metrics")
    if collection_status:
        meta["monitor_collection_status"] = collection_status
    if isinstance(unavailable_metrics, list):
        meta["monitor_unavailable_metrics"] = [
            str(value)[:64] for value in unavailable_metrics if str(value).strip()
        ][:32]
    if sample_timestamp:
        meta["monitor_sample_timestamp"] = sample_timestamp
    if sample.get("boot_time") is not None:
        meta["boot_time"] = sample["boot_time"]
    inv = dict(meta.get("inventory") or {})
    capabilities = inv.get("capabilities")
    preserve_storage_inventory = (
        isinstance(capabilities, list) and "storage_inventory_v1" in capabilities
    )
    hw = _hardware_inventory_summary(
        sample,
        preserve_storage_inventory=preserve_storage_inventory,
    )
    if hw:
        inv.update(hw)
        meta["inventory"] = inv
    Node.objects.filter(pk=node.pk).update(metadata=meta)


def ingest_node_heartbeat_metrics(*, node: Node) -> None:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    metrics = metadata.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return
    ingest_node_monitor_sample(node=node, sample=metrics)
