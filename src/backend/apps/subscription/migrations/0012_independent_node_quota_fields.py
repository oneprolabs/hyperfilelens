from django.db import migrations, models
from django.db.models import F


def split_legacy_node_limit(apps, schema_editor):
    del schema_editor
    license_model = apps.get_model("subscription", "License")
    license_model.objects.update(
        max_source_hosts=F("max_nodes"),
        max_proxies=F("max_nodes"),
    )
    history_model = apps.get_model("subscription", "LicenseHistory")
    history_model.objects.update(
        max_source_hosts=F("max_nodes"),
        max_proxies=F("max_nodes"),
    )


def restore_legacy_node_limit(apps, schema_editor):
    """Recreate the old combined value when this migration is reversed."""
    del schema_editor
    license_model = apps.get_model("subscription", "License")
    license_model.objects.update(max_nodes=F("max_source_hosts"))
    history_model = apps.get_model("subscription", "LicenseHistory")
    history_model.objects.update(max_nodes=F("max_source_hosts"))


class Migration(migrations.Migration):
    dependencies = [("subscription", "0011_storage_quota_bytes")]

    operations = [
        migrations.AddField(
            model_name="license",
            name="max_source_hosts",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="license",
            name="max_proxies",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_source_hosts",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_proxies",
            field=models.IntegerField(default=200),
        ),
        migrations.RunPython(split_legacy_node_limit, restore_legacy_node_limit),
        migrations.RemoveField(
            model_name="license",
            name="max_nodes",
        ),
        migrations.RemoveField(
            model_name="licensehistory",
            name="max_nodes",
        ),
    ]
