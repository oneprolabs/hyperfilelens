"""Capability-gated node cache management, independent of performance limits."""
from __future__ import annotations

from copy import deepcopy

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.node.models import Node, NodeTask
from apps.node.services.capabilities import node_supports_capability

CAPABILITY = "cache_management_v1"
GIB_POLICY_CAPABILITY = "cache_policy_gib_v1"
GIB = 1 << 30
MAX_BYTES = 2**53 - 1  # exact JSON integer range shared with the browser
DEFAULT_POLICY = {"schema_version": 1, "revision": 0, "blocking_bytes": 10 * GIB, "warning_bytes": 20 * GIB}


def _cache_report(metadata: dict) -> dict:
    inventory = metadata.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    report = metadata.get("cache_status") or inventory.get("cache_status")
    return report if isinstance(report, dict) else {}


def supports_cache_management(node) -> bool:
    if node.role not in {"agent", "proxy"}:
        return False
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    capabilities = inventory.get("capabilities", [])
    if isinstance(capabilities, (list, tuple, set, frozenset)) and CAPABILITY in capabilities:
        return True
    # A later/incomplete heartbeat may omit the capability list even though the
    # Agent has already applied the durable demand-driven cache mode. The
    # applied mode is stronger evidence than a partial inventory snapshot.
    return _cache_report(metadata).get("mode") == "demand_driven"


def policy_for_node(node) -> dict:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    policy = metadata.get("cache_policy")
    if isinstance(policy, dict):
        return deepcopy(policy)
    report_policy = _cache_report(metadata).get("policy")
    if isinstance(report_policy, dict):
        return deepcopy(report_policy)
    # An older cache-management binary rejects binary warning deltas. Until
    # its new capability/report arrives, keep its established decimal default.
    if supports_cache_management(node) and not node_supports_capability(node, GIB_POLICY_CAPABILITY):
        return {"schema_version": 1, "revision": 0, "blocking_bytes": 10_000_000_000, "warning_bytes": 20_000_000_000}
    return dict(DEFAULT_POLICY)


def normalize_blocking(value) -> int:
    if type(value) is not int or not 0 < value <= MAX_BYTES - 10 * GIB:
        raise ValidationError({"blocking_bytes": "Enter a positive integer byte value within the supported range."})
    return value


def require_capability(node):
    if not supports_cache_management(node):
        raise ValidationError("Upgrade this Agent/Proxy to enable cache management. Existing cache settings remain available.")


def snapshot(node) -> dict:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    raw_report = metadata.get("cache_status") or inventory.get("cache_status") or {}
    report = deepcopy(raw_report) if isinstance(raw_report, dict) else {}
    if isinstance(metadata.get("cache_space_check"), dict):
        report["last_preflight"] = deepcopy(metadata["cache_space_check"])
    policy = policy_for_node(node)
    supported = supports_cache_management(node)
    applied = supported and isinstance(report, dict) and report.get("policy") == policy and report.get("application_state") == "applied"
    latest = NodeTask.objects.filter(node=node, kind__in=["cache.policy.apply", "cache.status", "cache.relocate", "cache.relocation.retry"]).order_by("-created_at").first()
    if supported and latest and latest.kind == "cache.policy.apply" and latest.status in {"failed", "timeout"} and not applied:
        state = "failed"
    else:
        state = "applied" if applied else "pending" if supported else "unsupported"
    return {"supported": supported, "policy_unit_supported": supported and node_supports_capability(node, GIB_POLICY_CAPABILITY), "policy": policy if supported else None, "application_state": state, "report": report, "last_task": task_public(latest) if latest else None}


def task_public(task) -> dict:
    # No command payload or private migration material is projected.
    return {"id": str(task.id), "kind": task.kind, "status": task.status, "error": task.last_error, "result": task.result, "updated_at": task.updated_at.isoformat()}


def start_task(node, kind: str, payload: dict):
    from apps.node.services.internal.agent_task import run_agent_task_async
    from apps.node.services.internal.node_registry import agent_ws_routable

    require_capability(node)
    if not agent_ws_routable(agent_id=node.id):
        raise ValidationError("The Agent/Proxy must be online and routable for cache management.")
    return run_agent_task_async(organization_id=node.organization_id, node_id=node.id, kind=kind, payload=payload, correlation_type="node.cache_management", correlation_id=str(node.id), platform=node.organization_id is None).task


@transaction.atomic
def save_policy(node, value):
    node = Node.objects.select_for_update().get(pk=node.pk)
    require_capability(node)
    blocking = normalize_blocking(value)
    if not node_supports_capability(node, GIB_POLICY_CAPABILITY):
        raise ValidationError("Upgrade the source host or proxy host before saving a GiB cache policy. Existing byte-valued policies remain unchanged.")
    current = policy_for_node(node)
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory") if isinstance(metadata.get("inventory"), dict) else {}
    report = metadata.get("cache_status") or inventory.get("cache_status") or {}
    reported_policy = report.get("policy") if isinstance(report, dict) else None
    observed = reported_policy.get("revision", 0) if isinstance(reported_policy, dict) else 0
    observed = observed if type(observed) is int and 0 <= observed < MAX_BYTES else 0
    policy = {"schema_version": 1, "revision": max(current["revision"], observed) + 1, "blocking_bytes": blocking, "warning_bytes": blocking + 10 * GIB}
    metadata = dict(node.metadata or {})
    metadata["cache_policy"] = policy
    node.metadata = metadata
    node.save(update_fields=["metadata", "updated_at"])
    return start_task(node, "cache.policy.apply", {"cache_policy": policy})


@transaction.atomic
def start_relocation(node, validation_task_id):
    node = Node.objects.select_for_update().get(pk=node.pk)
    require_capability(node)
    if NodeTask.objects.filter(node=node, kind__in=["cache.relocate", "cache.relocation.retry"], status__in=["pending", "running"]).exists():
        raise ValidationError("Another cache relocation is running.")
    validated = NodeTask.objects.filter(pk=validation_task_id, node=node, kind="cache.relocation.validate", status="success").first()
    if validated is None or not isinstance(validated.result, dict):
        raise ValidationError("Complete destination validation on this node before accepting a relocation.")
    evidence = validated.result
    from django.utils.dateparse import parse_datetime
    expires = parse_datetime(str(evidence.get("expires_at") or ""))
    if expires is None or timezone.is_naive(expires) or expires <= timezone.now() or not evidence.get("validation_token"):
        raise ValidationError("Destination validation has expired; validate it again.")
    if evidence.get("policy_revision") != policy_for_node(node)["revision"]:
        raise ValidationError("Cache policy changed; validate the destination again.")
    return start_task(node, "cache.relocate", {"validation_token": evidence["validation_token"]})


def attach_cache_context(*, node, payload, kind, correlation_type):
    if not isinstance(payload, dict) or not supports_cache_management(node):
        return payload
    result = dict(payload)
    result.pop("kopia_cache_size_mb", None)
    result["cache_policy"] = policy_for_node(node)
    if kind in {"backup", "backup.run", "backup.snapshot.create"} or (correlation_type.startswith("protection.backup") and kind in {"repo.connect", "repository.connect", "repository.policy.apply", "repository.server.start"}) or (kind == "repository.server.start" and str(payload.get("session_id", "")).startswith("backup-")):
        result["cache_use_context"] = "backup"
    return result


@transaction.atomic
def project_cache_status(task, payload):
    """Project confirmed reports only, without replacing unrelated node metadata."""
    if not isinstance(payload, dict):
        return
    node = Node.objects.select_for_update().get(pk=task.node_id)
    if not supports_cache_management(node):
        return
    report = payload.get("cache_status") if isinstance(payload.get("cache_status"), dict) else payload if task.kind in {"cache.status", "cache.policy.apply"} else None
    metadata = dict(node.metadata or {})
    if isinstance(report, dict) and isinstance(report.get("policy"), dict):
        if report["policy"] == policy_for_node(node):
            safe = {key: deepcopy(report[key]) for key in ("policy", "application_state", "mode", "active_cache_root", "relocation", "usage", "space", "space_error", "last_preflight") if key in report}
            safe["reported_at"] = timezone.now().isoformat()
            metadata["cache_status"] = safe
    if task.kind in {"cache.relocate", "cache.relocation.retry"}:
        status = dict(metadata["cache_status"]) if isinstance(metadata.get("cache_status"), dict) else {}
        status["relocation"] = {key: payload[key] for key in ("task_id", "stage", "source", "destination", "error") if key in payload}
        if payload.get("stage") in {"completed", "switched_cleanup_pending"}:
            status["active_cache_root"] = payload.get("destination")
        metadata["cache_status"] = status
    check = payload.get("cache_space_check")
    if isinstance(check, dict):
        _project_space_check(node, metadata, task, check)
    node.metadata = metadata
    node.save(update_fields=["metadata", "updated_at"])


def _project_space_check(node, metadata, task, check):
    import hashlib
    import json
    import uuid
    from django.utils.dateparse import parse_datetime
    from apps.monitor.models import OperationalEvent
    from apps.monitor.services.events import record_operational_event
    from apps.task.models import Task
    from apps.alert.services.internal.event_alerts import handle_platform_event

    state = check.get("state")
    sampled = parse_datetime(str(check.get("sampled_at") or ""))
    if state not in {"normal", "warning", "blocked", "check_error"} or sampled is None or timezone.is_naive(sampled):
        return
    check = {key: deepcopy(check[key]) for key in ("check_id", "state", "sampled_at", "cache_root", "cache_filesystem_id", "cache_generation", "available_bytes", "policy", "error") if key in check}
    if state == "check_error":
        check["available_bytes"] = None
    old = metadata.get("cache_space_check")
    old = old if isinstance(old, dict) else {}
    previous = parse_datetime(str(old.get("sampled_at") or ""))
    # Latest telemetry is independent of warning admission below. Keep it
    # ordered by sample time, cache generation and policy revision.
    report_snapshot = metadata.get("cache_status") if isinstance(metadata.get("cache_status"), dict) else {}
    current_root = report_snapshot.get("active_cache_root")
    path_matches = not current_root or current_root == check.get("cache_root")
    generation_ok = (check.get("cache_generation") if type(check.get("cache_generation")) is int else 0) >= (old.get("cache_generation") if type(old.get("cache_generation")) is int else 0)
    current_policy = old.get("policy") if isinstance(old.get("policy"), dict) else {}
    incoming_policy = check.get("policy") if isinstance(check.get("policy"), dict) else {}
    revision_ok = (incoming_policy.get("revision") if type(incoming_policy.get("revision")) is int else 0) >= (current_policy.get("revision") if type(current_policy.get("revision")) is int else 0)
    if (previous is None or sampled > previous) and path_matches and generation_ok and revision_ok:
        metadata["cache_space_check"] = deepcopy(check)
        report = dict(metadata["cache_status"]) if isinstance(metadata.get("cache_status"), dict) else {}
        report["last_preflight"] = deepcopy(check)
        metadata["cache_status"] = report
    if state != "warning":
        # Both blocked and unavailable checks fail the backup and are already
        # handled by the existing task_failed event/alert/notification chain.
        return
    parent = None
    backup_tasks = Task.objects.filter(organization_id=task.organization_id, task_type=Task.Type.BACKUP)
    if task.parent_task_id:
        parent = backup_tasks.filter(pk=task.parent_task_id).first()
    elif task.correlation_type in {"protection.backup", "protection.backup.policy_prepare"}:
        try:
            task_uuid = uuid.UUID(str(task.correlation_id))
        except (ValueError, TypeError, AttributeError):
            task_uuid = None
        if task_uuid:
            parent = backup_tasks.filter(task_uuid=task_uuid).first()
    path = str(check.get("cache_root") or "")[:4096]
    filesystem_id = str(check.get("cache_filesystem_id") or hashlib.sha256(path.encode()).hexdigest())
    policy = check.get("policy") if isinstance(check.get("policy"), dict) else {}
    available = check.get("available_bytes")
    if type(available) is not int or available < 0:
        return
    from decimal import Decimal

    def gib(value):
        return f"{Decimal(value) / GIB:.3f} GiB" if type(value) is int and value >= 0 else "unknown"

    message = f"Executing node cache filesystem {path!r} is near the required backup minimum: {gib(available)} available; blocking threshold {gib(policy.get('blocking_bytes'))}; warning threshold {gib(policy.get('warning_bytes'))}. Checked at {sampled.isoformat()}."
    check_id = str(check.get("check_id") or hashlib.sha256(json.dumps(check, sort_keys=True, default=str).encode()).hexdigest())
    if parent:
        # Preparation, directories and snapshot rechecks belong to one backup.
        # Keep every actual preflight and its latest telemetry, but admit only
        # one warning per backup, executor and cache filesystem/directory.
        scope = json.dumps([str(parent.task_uuid), node.id, filesystem_id, path])
        dedup_key = f"backup-space:backup:{hashlib.sha256(scope.encode()).hexdigest()}"
    else:
        # Retain transport idempotency for legacy tasks without a business link.
        dedup_key = f"backup-space:{task.id}:{hashlib.sha256(check_id.encode()).hexdigest()}"
    # project_cache_status holds the executor's node row lock, serializing
    # same-backup warning admission even for concurrently running directories.
    # Event recording and alert/notification lifecycle behavior stay unchanged.
    if OperationalEvent.objects.filter(organization_id=task.organization_id, dedup_key=dedup_key).exists():
        return
    event = record_operational_event(
        organization_id=task.organization_id,
        event_type="backup_space_check_warning",
        category="protection",
        severity="warning",
        title="Backup Space Check Warning",
        details=message,
        occurred_at=sampled,
        resource_type="task",
        resource_id=str(parent.id) if parent else str(task.id),
        resource_name=parent.display_name if parent else node.name,
        source="agent.cache_preflight",
        target_path=path[:1000],
        correlation_id=str(parent.task_uuid) if parent else str(task.id),
        dedup_key=dedup_key,
        metadata={
            "execution_node_id": node.id,
            "execution_node_name": node.name,
            "cache_filesystem_id": filesystem_id,
            "reason_code": "CACHE_SPACE_NEAR_LIMIT",
            "task_id": parent.id if parent else None,
            "task_uuid": str(parent.task_uuid) if parent else None,
            "node_task_id": str(task.id),
            **check,
        },
    )
    if task.organization_id is not None:
        handle_platform_event(
            organization=task.organization,
            event_category="protection",
            event_type=event.event_type,
            title=event.title,
            message=event.details,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            resource_name=event.resource_name,
            metadata=event.metadata,
        )
