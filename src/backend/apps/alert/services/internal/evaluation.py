"""Periodic evaluation for metric, availability, and system alert policies."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.alert.constants import AlertType, PolicyScope, ResourceType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.lifecycle import fire_alert, resolve_alert
from apps.alert.services.internal.metadata_resources import resource_options
from apps.monitor.models import ResourceMetric, SystemMetric
from apps.monitor.services.internal.metric_values import (
    value_from_resource_metrics,
    value_from_system_metric,
)
from apps.node.models import Node
from apps.node.models.base import NodeRole

logger = logging.getLogger(__name__)

_DEFAULT_EVALUATION_INTERVAL_SECONDS = 60
_DEFAULT_SAMPLE_FRESHNESS_SECONDS = 600

_NODE_RESOURCE_BY_ROLE = {
    NodeRole.PROXY: ResourceType.SYNC_PROXY,
    NodeRole.AGENT: ResourceType.AGENT_PROXY,
    NodeRole.GATEWAY: ResourceType.GATEWAY,
}

_OPERATORS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _policy_silenced(policy: AlertPolicy) -> bool:
    rule = policy.trigger_rule or {}
    until_raw = rule.get("silenced_until")
    if not until_raw:
        return False
    until = parse_datetime(str(until_raw))
    if until is None:
        return False
    if timezone.is_naive(until):
        until = timezone.make_aware(until, timezone.get_current_timezone())
    return timezone.now() < until


def _policy_resource_ids(policy: AlertPolicy) -> list[str]:
    if policy.scope == PolicyScope.ALL or policy.resource_type == ResourceType.SYSTEM:
        options = resource_options(
            organization_id=policy.organization_id,
            resource_type=policy.resource_type,
        )
        return [str(item["id"]) for item in options]
    return [str(rid) for rid in (policy.resource_ids or [])]


def _compare(operator: str, value: float, threshold: float) -> bool:
    fn = _OPERATORS.get(str(operator or ">").strip())
    if fn is None:
        fn = _OPERATORS[">"]
    return fn(value, threshold)


def _rule_seconds(rule: dict, key: str, default: int) -> int:
    try:
        return max(0, int(rule.get(key, default)))
    except (TypeError, ValueError):
        return default


def _sample_freshness_seconds(rule: dict) -> int:
    """Return the maximum accepted sample age.

    Resource and Control Plane metrics are currently collected at up to five-minute
    intervals. A ten-minute compatibility window tolerates collector/scheduler skew
    without treating an offline resource's final sample as current indefinitely.
    """
    return max(
        _DEFAULT_EVALUATION_INTERVAL_SECONDS,
        _rule_seconds(
            rule,
            "sample_freshness_seconds",
            _DEFAULT_SAMPLE_FRESHNESS_SECONDS,
        ),
    )


def _condition_state(
    *,
    samples: list,
    value_getter,
    metric_key: str,
    operator: str,
    threshold: float,
    duration_seconds: int,
    now,
    freshness_seconds: int,
) -> tuple[bool | None, bool]:
    """Return ``(current_match, sustained_match)`` for ordered samples.

    ``current_match`` is ``None`` when the latest sample is absent, stale, or has
    no usable value. A positive duration requires at least two matching samples
    whose timestamps cover the configured duration; one observation is never
    treated as proof of a sustained condition.
    """
    if not samples:
        return None, False
    latest = samples[-1]
    if (now - latest.timestamp).total_seconds() > freshness_seconds:
        return None, False
    latest_value = value_getter(latest, metric_key)
    if latest_value is None:
        return None, False
    current_match = _compare(operator, latest_value, threshold)
    if not current_match:
        return False, False

    duration = max(0, int(duration_seconds or 0))
    if duration <= 0:
        return True, True

    matching_tail = []
    for sample in reversed(samples):
        value = value_getter(sample, metric_key)
        if value is None or not _compare(operator, value, threshold):
            break
        matching_tail.append(sample)
    if len(matching_tail) < 2:
        return True, False
    covered_seconds = (
        matching_tail[0].timestamp - matching_tail[-1].timestamp
    ).total_seconds()
    return True, covered_seconds >= duration


def _resource_samples(policy: AlertPolicy, resource_id: str, rule: dict, now) -> list:
    duration = max(
        _rule_seconds(rule, "duration_seconds", 0),
        _rule_seconds(policy.recovery_rule or {}, "duration_seconds", 0),
    )
    since = now - timedelta(
        seconds=duration + _sample_freshness_seconds(rule)
    )
    return list(
        ResourceMetric.objects.filter(
            organization_id=policy.organization_id,
            resource_type=policy.resource_type,
            resource_id=str(resource_id),
            timestamp__gte=since,
        ).order_by("timestamp")
    )


def _system_samples(rule: dict, recovery_rule: dict, now) -> tuple[list, object | None]:
    latest = SystemMetric.objects.select_related("host").order_by("-timestamp").first()
    if latest is None:
        return [], None
    duration = max(
        _rule_seconds(rule, "duration_seconds", 0),
        _rule_seconds(recovery_rule, "duration_seconds", 0),
    )
    since = now - timedelta(
        seconds=duration + _sample_freshness_seconds(rule)
    )
    qs = SystemMetric.objects.filter(timestamp__gte=since)
    if latest.host_id is not None:
        qs = qs.filter(host_id=latest.host_id)
    else:
        qs = qs.filter(host__isnull=True)
    return list(qs.order_by("timestamp")), latest.host


def _resource_value(sample: ResourceMetric, metric_key: str) -> float | None:
    return value_from_resource_metrics(sample.metrics or {}, metric_key)


def _system_value(sample: SystemMetric, metric_key: str) -> float | None:
    return value_from_system_metric(sample, metric_key)


def _recovery_satisfied(
    *,
    policy: AlertPolicy,
    samples: list,
    value_getter,
    metric_key: str,
    now,
    freshness_seconds: int,
) -> bool:
    recovery = policy.recovery_rule
    if not recovery:
        # Compatibility for policies created before recovery controls existed:
        # resolve immediately when the firing condition no longer matches.
        return True
    if recovery.get("enabled") is False:
        return False
    trigger = policy.trigger_rule or {}
    operator = str(recovery.get("operator") or _inverse_operator(trigger.get("operator")))
    try:
        threshold = float(recovery.get("threshold", trigger.get("threshold", 0)))
    except (TypeError, ValueError):
        return False
    current, sustained = _condition_state(
        samples=samples,
        value_getter=value_getter,
        metric_key=metric_key,
        operator=operator,
        threshold=threshold,
        duration_seconds=_rule_seconds(recovery, "duration_seconds", 0),
        now=now,
        freshness_seconds=freshness_seconds,
    )
    return current is True and sustained


def _inverse_operator(operator: object) -> str:
    return {
        ">": "<=",
        ">=": "<",
        "<": ">=",
        "<=": ">",
        "==": "!=",
        "!=": "==",
    }.get(str(operator or ">"), "<=")


def _evaluate_metric_policy(policy: AlertPolicy) -> None:
    rule = policy.trigger_rule or {}
    metric_key = str(rule.get("metric_key") or "").strip()
    operator = str(rule.get("operator") or ">")
    threshold = float(rule.get("threshold") or 0)
    duration_seconds = int(rule.get("duration_seconds") or 0)
    if not metric_key:
        return

    now = timezone.now()
    freshness_seconds = _sample_freshness_seconds(rule)
    for resource_id in _policy_resource_ids(policy):
        if policy.resource_type == ResourceType.SYSTEM:
            samples, _host = _system_samples(rule, policy.recovery_rule or {}, now)
            if not samples:
                logger.info(
                    "alert metric sample unavailable or stale policy=%s resource_type=%s resource_id=%s",
                    policy.id,
                    policy.resource_type,
                    resource_id,
                )
                continue
            latest = samples[-1]
            value = _system_value(latest, metric_key)
            resource_name = "Control Plane"
            value_getter = _system_value
        else:
            samples = _resource_samples(policy, resource_id, rule, now)
            if not samples:
                logger.info(
                    "alert metric sample unavailable or stale policy=%s resource_type=%s resource_id=%s",
                    policy.id,
                    policy.resource_type,
                    resource_id,
                )
                continue
            latest = samples[-1]
            value = _resource_value(latest, metric_key)
            resource_name = latest.resource_name or resource_id
            value_getter = _resource_value

        if value is None:
            continue

        current_match, sustained_match = _condition_state(
            samples=samples,
            value_getter=value_getter,
            metric_key=metric_key,
            operator=operator,
            threshold=threshold,
            duration_seconds=duration_seconds,
            now=now,
            freshness_seconds=freshness_seconds,
        )
        if current_match is None:
            logger.info(
                "alert metric sample unavailable or stale policy=%s resource_type=%s resource_id=%s",
                policy.id,
                policy.resource_type,
                resource_id,
            )
            continue
        if sustained_match:
            fire_alert(
                policy,
                resource=_ResourceStub(resource_id, resource_name),
                title=f"{policy.name}: {metric_key}",
                message=f"{metric_key}={value} {operator} {threshold}",
                current_value=value,
                alert_key=metric_key,
                metadata={"metric_key": metric_key, "value": value},
            )
        elif current_match is False and _recovery_satisfied(
            policy=policy,
            samples=samples,
            value_getter=value_getter,
            metric_key=metric_key,
            now=now,
            freshness_seconds=freshness_seconds,
        ):
            _maybe_resolve_metric(policy, resource_id)


def _maybe_resolve_metric(policy: AlertPolicy, resource_id: str) -> None:
    from apps.alert.constants import AlertStatus
    from apps.alert.services.internal.fingerprint import build_fingerprint

    metric_key = (policy.trigger_rule or {}).get("metric_key") or "default"
    fingerprint = build_fingerprint(policy, resource_id, str(metric_key))
    alert = AlertRecord.objects.filter(
        organization_id=policy.organization_id,
        fingerprint=fingerprint,
        status__in=[
            AlertStatus.PENDING,
            AlertStatus.FIRING,
            AlertStatus.ACKNOWLEDGED,
        ],
    ).first()
    if alert:
        resolve_alert(alert)


class _ResourceStub:
    def __init__(self, resource_id: str, name: str = ""):
        self.id = resource_id
        self.name = name


def _evaluate_availability_policy(policy: AlertPolicy) -> None:
    rule = policy.trigger_rule or {}
    check_type = str(rule.get("check_type") or "heartbeat")
    timeout_seconds = int(rule.get("timeout_seconds") or rule.get("duration_seconds") or 300)
    if check_type != "heartbeat":
        return

    now = timezone.now()
    for resource_id in _policy_resource_ids(policy):
        node = Node.objects.filter(
            organization_id=policy.organization_id,
            pk=resource_id,
        ).first()
        if node is None:
            continue
        expected_type = _NODE_RESOURCE_BY_ROLE.get(node.role)
        if expected_type and policy.resource_type != expected_type:
            continue
        last_seen = node.last_seen_at
        stale = last_seen is None or (now - last_seen).total_seconds() > timeout_seconds
        stub = _ResourceStub(str(node.id), node.name)
        if stale or node.availability != Node.Availability.ONLINE:
            fire_alert(
                policy,
                resource=stub,
                title=f"{policy.name}: node unavailable",
                message=f"Node {node.name} last seen {last_seen}",
                current_value=Decimal(str(int((now - last_seen).total_seconds()) if last_seen else timeout_seconds + 1)),
                alert_key=check_type,
                metadata={"check_type": check_type, "node_status": node.status},
            )
        else:
            _maybe_resolve_metric(policy, str(node.id))


def _evaluate_system_policy(policy: AlertPolicy) -> None:
    rule = policy.trigger_rule or {}
    check_type = str(rule.get("check_type") or "service_health")
    if check_type == "disk_space_low":
        policy.trigger_rule = {
            **rule,
            "metric_key": "disk_usage",
            "operator": ">=",
            "threshold": rule.get("threshold", 90),
            "duration_seconds": rule.get("duration_seconds", 0),
        }
        _evaluate_metric_policy(policy)
        return
    # service_health: rely on latest system metric CPU/memory thresholds if set
    if rule.get("metric_key"):
        _evaluate_metric_policy(policy)


def _evaluation_interval_seconds(policy: AlertPolicy) -> int:
    return max(
        _DEFAULT_EVALUATION_INTERVAL_SECONDS,
        _rule_seconds(
            policy.trigger_rule or {},
            "evaluation_interval_seconds",
            _DEFAULT_EVALUATION_INTERVAL_SECONDS,
        ),
    )


def _evaluation_due(policy: AlertPolicy, now) -> bool:
    if policy.last_evaluated_at is None:
        return True
    elapsed = (now - policy.last_evaluated_at).total_seconds()
    return elapsed >= _evaluation_interval_seconds(policy)


def evaluate_organization_policies(*, organization_id: int | None = None) -> dict:
    qs = AlertPolicy.objects.filter(enabled=True).exclude(type=AlertType.TASK).exclude(
        type=AlertType.EVENT
    )
    if organization_id is not None:
        qs = qs.filter(organization_id=organization_id)

    counts = {"metric": 0, "availability": 0, "system": 0, "skipped": 0}
    for policy in qs.iterator():
        if _policy_silenced(policy):
            counts["skipped"] += 1
            continue
        evaluated_at = timezone.now()
        if not _evaluation_due(policy, evaluated_at):
            counts["skipped"] += 1
            continue
        # Persist the attempt before evaluation so a failing policy cannot be
        # retried in a tight loop by overlapping scheduler runs.
        claimed = AlertPolicy.objects.filter(
            pk=policy.pk,
            last_evaluated_at=policy.last_evaluated_at,
        ).update(
            last_evaluated_at=evaluated_at
        )
        if not claimed:
            counts["skipped"] += 1
            continue
        policy.last_evaluated_at = evaluated_at
        try:
            if policy.type == AlertType.METRIC:
                _evaluate_metric_policy(policy)
                counts["metric"] += 1
            elif policy.type == AlertType.AVAILABILITY:
                _evaluate_availability_policy(policy)
                counts["availability"] += 1
            elif policy.type == AlertType.SYSTEM:
                _evaluate_system_policy(policy)
                counts["system"] += 1
        except Exception:
            logger.exception("alert evaluation failed policy=%s", policy.id)
    return counts


def evaluate_all_policies() -> dict:
    return evaluate_organization_policies()
