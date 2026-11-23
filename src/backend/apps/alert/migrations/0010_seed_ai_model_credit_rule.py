"""Seed the new credit rule once; failures must not restore deleted rules."""

from django.db import migrations


def seed_credit_rules(apps, schema_editor):
    policy = apps.get_model("alerts", "AlertPolicy")
    organization = apps.get_model("iam", "Organization")
    alias = schema_editor.connection.alias
    # A fresh installation has no organizations yet; its normal bootstrap
    # seeds the catalog. This migration backfills already-installed instances.
    if not organization.objects.using(alias).exists():
        return
    defaults = {
        "name": "AI Model Credit Issue",
        "description": "Alert when the model provider explicitly reports insufficient credit.",
        "type": "event",
        "severity": "critical",
        "enabled": True,
        "resource_type": "ai_model",
        "scope": "all",
        "resource_ids": [],
        "trigger_rule": {
            "event_category": "configuration",
            "event_types": ["ai_model_insufficient_credit"],
            "repeat_interval_seconds": 3600,
        },
        "recovery_rule": {
            "enabled": True, "notify_on_firing": True, "notify_on_resolved": True,
        },
    }
    policy.objects.using(alias).get_or_create(
        owner_scope="platform", organization_id=None,
        default_key="ai_model.insufficient_credit", defaults=defaults,
    )
    for org_id in organization.objects.using(alias).values_list("pk", flat=True).iterator():
        policy.objects.using(alias).get_or_create(
            owner_scope="organization", organization_id=org_id,
            default_key="ai_model.insufficient_credit", defaults=defaults,
        )


class Migration(migrations.Migration):
    dependencies = [("alerts", "0009_ai_model_resource")]

    operations = [
        migrations.RunPython(seed_credit_rules, migrations.RunPython.noop),
    ]
