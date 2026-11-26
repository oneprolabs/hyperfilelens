"""Add the cache warning rule without changing existing rules or delivery settings."""

from django.db import migrations


# Freeze this business definition; migration behavior must not follow future
# changes to the product default catalog.
WARNING_RULE = {
    "name": "Backup Space Check Warning",
    "description": "Alert when available cache space is near the required backup minimum.",
    "type": "event",
    "severity": "warning",
    "enabled": True,
    "resource_type": "task",
    "scope": "all",
    "resource_ids": [],
    "trigger_rule": {
        "event_category": "protection",
        "event_types": ["backup_space_check_warning"],
        "repeat_interval_seconds": 7200,
        "merge_enabled": True,
        "merge_window_seconds": 300,
        "merge_key": "",
    },
    "recovery_rule": {
        "enabled": False,
        "notify_on_firing": True,
        "notify_on_resolved": False,
    },
}


def add_cache_warning_rule(apps, schema_editor):
    Policy = apps.get_model("alerts", "AlertPolicy")
    Organization = apps.get_model("iam", "Organization")
    alias = schema_editor.connection.alias
    for organization_id in Organization.objects.using(alias).values_list("pk", flat=True).iterator():
        Policy.objects.using(alias).get_or_create(
            owner_scope="organization",
            organization_id=organization_id,
            default_key="backup.space_check_warning",
            defaults=WARNING_RULE,
        )


class Migration(migrations.Migration):
    dependencies = [("alerts", "0010_seed_ai_model_credit_rule")]
    # Keep any rule that an operator may subsequently customize on rollback.
    operations = [migrations.RunPython(add_cache_warning_rule, migrations.RunPython.noop)]
