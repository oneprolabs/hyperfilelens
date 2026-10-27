"""Normalize alert types to metric / health / task / event.

- availability → health
- system + disk_space_low (or metric_key) → metric
- remaining system → health

Migrated rows carry an internal origin marker so a rollback never guesses
whether a ``health`` row came from the old ``availability`` or ``system`` type.
"""

from __future__ import annotations

from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError


_LEGACY_TYPE = "_hfl_legacy_alert_type"
_LEGACY_TRIGGER = "_hfl_legacy_trigger_rule"


def _marked_rule(rule, legacy_type):
    return {
        **dict(rule or {}),
        _LEGACY_TYPE: legacy_type,
    }


def forwards(apps, schema_editor):
    AlertPolicy = apps.get_model("alert", "AlertPolicy")
    AlertRecord = apps.get_model("alert", "AlertRecord")

    for policy in AlertPolicy.objects.filter(type="availability").iterator():
        policy.type = "health"
        policy.trigger_rule = _marked_rule(policy.trigger_rule, "availability")
        policy.save(update_fields=["type", "trigger_rule", "updated_at"])

    for record in AlertRecord.objects.filter(type="availability").iterator():
        record.type = "health"
        metadata = dict(record.metadata or {})
        metadata[_LEGACY_TYPE] = "availability"
        record.metadata = metadata
        record.save(update_fields=["type", "metadata", "updated_at"])

    for policy in AlertPolicy.objects.filter(type="system").iterator():
        original_rule = dict(policy.trigger_rule or {})
        rule = dict(original_rule)
        check_type = str(rule.get("check_type") or "")
        if check_type == "disk_space_low" or rule.get("metric_key"):
            if check_type == "disk_space_low":
                rule = {
                    **rule,
                    "metric_key": rule.get("metric_key") or "disk_usage",
                    "operator": rule.get("operator") or ">=",
                    "threshold": rule.get("threshold", 90),
                    "duration_seconds": rule.get("duration_seconds", 0),
                }
            rule[_LEGACY_TYPE] = "system"
            rule[_LEGACY_TRIGGER] = original_rule
            policy.type = "metric"
            policy.trigger_rule = rule
            policy.save(update_fields=["type", "trigger_rule", "updated_at"])
            target_type = "metric"
        else:
            rule[_LEGACY_TYPE] = "system"
            rule[_LEGACY_TRIGGER] = original_rule
            policy.type = "health"
            policy.trigger_rule = rule
            policy.save(update_fields=["type", "trigger_rule", "updated_at"])
            target_type = "health"
        for record in AlertRecord.objects.filter(policy_id=policy.pk, type="system").iterator():
            metadata = dict(record.metadata or {})
            metadata[_LEGACY_TYPE] = "system"
            metadata["_hfl_migrated_to"] = target_type
            record.type = target_type
            record.metadata = metadata
            record.save(update_fields=["type", "metadata", "updated_at"])

    # Orphan records (policy already deleted) still need a legal type.
    for record in AlertRecord.objects.filter(type="system").iterator():
        metadata = dict(record.metadata or {})
        metadata[_LEGACY_TYPE] = "system"
        metadata["_hfl_migrated_to"] = "health"
        record.type = "health"
        record.metadata = metadata
        record.save(update_fields=["type", "metadata", "updated_at"])


def backwards(apps, schema_editor):
    AlertPolicy = apps.get_model("alert", "AlertPolicy")
    AlertRecord = apps.get_model("alert", "AlertRecord")
    migrated_system_policy_ids = [
        policy.pk
        for policy in AlertPolicy.objects.filter(
            type__in=["health", "metric"],
            trigger_rule__has_key=_LEGACY_TYPE,
        ).iterator()
        if (policy.trigger_rule or {}).get(_LEGACY_TYPE) == "system"
    ]

    # Rows created after this migration have no origin marker. Refuse to
    # downgrade rather than silently changing their meaning.
    if AlertPolicy.objects.filter(type="health").exclude(
        trigger_rule__has_key=_LEGACY_TYPE
    ).exists() or AlertRecord.objects.filter(type="health").exclude(
        metadata__has_key=_LEGACY_TYPE
    ).exists() or (
        migrated_system_policy_ids
        and AlertRecord.objects.filter(
            policy_id__in=migrated_system_policy_ids,
            type="metric",
        ).exclude(metadata__has_key=_LEGACY_TYPE).exists()
    ):
        raise IrreversibleError(
            "Cannot safely reverse alert type migration after new health or metric rows were created."
        )

    for policy in AlertPolicy.objects.filter(
        type__in=["health", "metric"],
        trigger_rule__has_key=_LEGACY_TYPE,
    ).iterator():
        rule = dict(policy.trigger_rule or {})
        legacy_type = rule.pop(_LEGACY_TYPE, None)
        original_rule = rule.pop(_LEGACY_TRIGGER, None)
        policy.type = legacy_type or policy.type
        policy.trigger_rule = (
            dict(original_rule) if isinstance(original_rule, dict) else rule
        )
        policy.save(update_fields=["type", "trigger_rule", "updated_at"])

    for record in AlertRecord.objects.filter(
        type__in=["health", "metric"],
        metadata__has_key=_LEGACY_TYPE,
    ).iterator():
        metadata = dict(record.metadata or {})
        legacy_type = metadata.pop(_LEGACY_TYPE, None)
        metadata.pop("_hfl_migrated_to", None)
        record.type = legacy_type or record.type
        record.metadata = metadata
        record.save(update_fields=["type", "metadata", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("alert", "0005_owner_scope"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
