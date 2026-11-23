"""Model-configuration incidents, independent of the executing gateway."""

from types import SimpleNamespace

from django.utils.dateparse import parse_datetime

from apps.alert.constants import AlertStatus, ResourceType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.lifecycle import fire_alert, resolve_alert
from apps.lens_bridge.models import LensUsageLedger
from apps.lens_bridge.services.provider_errors import INSUFFICIENT_CREDIT


def _incidents(model):
    return AlertRecord.objects.filter(
        owner_scope=model.scope, organization_id=model.organization_id,
        resource_type=ResourceType.AI_MODEL, resource_id=str(model.pk),
        metadata__provider_error_code=INSUFFICIENT_CREDIT,
    )


def fire_credit_alert(row, model, evidence):
    policies = AlertPolicy.objects.filter(
        owner_scope=model.scope, organization_id=model.organization_id,
        type="event", enabled=True, resource_type=ResourceType.AI_MODEL,
        trigger_rule__event_category="configuration",
    )
    seen_at = max(entry["observed_at"] for entry in evidence)
    recovery_evidence = LensUsageLedger.objects.filter(
        run_status="done",
        provider_error_state_json__status="checked",
        provider_error_state_json__recovered_model_ref=str(model.sl_config_uuid),
        provider_error_state_json__observed_at__gte=seen_at,
    )
    if model.scope == "organization":
        recovery_evidence = recovery_evidence.filter(organization_id=model.organization_id)
    # Delayed historical diagnostics must not reopen a recovered incident.
    if recovery_evidence.exists() or _incidents(model).filter(
        status=AlertStatus.RESOLVED, metadata__recovered_at__gte=seen_at,
    ).exists():
        return
    for policy in policies:
        rule = policy.trigger_rule or {}
        if "ai_model_insufficient_credit" not in (rule.get("event_types") or []):
            continue
        if policy.scope == "selected" and str(model.pk) not in {
            str(value) for value in policy.resource_ids or []
        }:
            continue
        # The model lock serializes diagnoses; the incident lock also protects
        # delivery metadata written by the notifier after a previous commit.
        previous = _incidents(model).select_for_update().filter(
            policy_id=policy.pk,
            status__in=(AlertStatus.FIRING, AlertStatus.ACKNOWLEDGED),
        ).first()
        metadata = dict(previous.metadata or {}) if previous else {}
        count = int(metadata.get("affected_requests") or 0) + 1
        metadata.update(
            provider_error_code=INSUFFICIENT_CREDIT,
            model_ref=str(model.sl_config_uuid),
            affected_requests=count,
            example_run_uuid=str(row.sl_run_uuid),
            first_observed_at=min(metadata.get("first_observed_at") or seen_at, seen_at),
            last_observed_at=max(metadata.get("last_observed_at") or seen_at, seen_at),
            event_category="configuration",
            event_type="ai_model_insufficient_credit",
        )
        fire_alert(
            policy, resource=SimpleNamespace(id=model.pk, name=model.display_name),
            title="AI Model Credit Issue",
            message=(
                "The model provider reported insufficient account credit. "
                f"Model configuration: {model.sl_config_uuid}. "
                f"Affected requests: {count}. "
                f"Last observed: {metadata['last_observed_at']}. "
                f"Example request: {row.sl_run_uuid}. "
                "Check the provider account balance and restore available credit."
            ),
            alert_key=f"ai-credit:{model.sl_config_uuid}",
            metadata=metadata, defer_notifications=True, preserve_acknowledgement=True,
        )


def recover_credit_alerts(model, observed_at):
    for alert in _incidents(model).select_for_update().filter(
        status__in=(AlertStatus.FIRING, AlertStatus.ACKNOWLEDGED),
    ):
        last_seen = parse_datetime((alert.metadata or {}).get("last_observed_at", ""))
        if last_seen is None or observed_at <= last_seen:
            continue
        policy = AlertPolicy.objects.filter(pk=alert.policy_id).first()
        if policy is None or (policy.recovery_rule or {}).get("enabled") is not True:
            continue
        alert.metadata = {**alert.metadata, "recovered_at": observed_at.isoformat()}
        alert.save(update_fields=["metadata"])
        resolve_alert(alert, defer_notifications=True)
