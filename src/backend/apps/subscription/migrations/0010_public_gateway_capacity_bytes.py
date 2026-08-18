from django.db import migrations, models


GIB = 1024**3


def _bytes_to_legacy_gib(value):
    """Round positive byte limits up so rollback cannot empty a usable pool."""
    if value <= 0:
        return value
    return (value + GIB - 1) // GIB


def capacity_gib_to_bytes(apps, schema_editor):
    del schema_editor
    for model_name in ("License", "LicenseHistory"):
        model = apps.get_model("subscription", model_name)
        for row in model.objects.exclude(
            max_public_gateway_capacity_bytes=-1
        ).iterator():
            row.max_public_gateway_capacity_bytes *= GIB
            row.save(update_fields=["max_public_gateway_capacity_bytes"])


def capacity_bytes_to_gib(apps, schema_editor):
    del schema_editor
    for model_name in ("License", "LicenseHistory"):
        model = apps.get_model("subscription", model_name)
        for row in model.objects.exclude(
            max_public_gateway_capacity_bytes=-1
        ).iterator():
            row.max_public_gateway_capacity_bytes = _bytes_to_legacy_gib(
                row.max_public_gateway_capacity_bytes
            )
            row.save(update_fields=["max_public_gateway_capacity_bytes"])


class Migration(migrations.Migration):
    dependencies = [("subscription", "0009_license_features")]

    operations = [
        migrations.RenameField(
            model_name="license",
            old_name="max_public_gateway_capacity_gb",
            new_name="max_public_gateway_capacity_bytes",
        ),
        migrations.RenameField(
            model_name="licensehistory",
            old_name="max_public_gateway_capacity_gb",
            new_name="max_public_gateway_capacity_bytes",
        ),
        migrations.AlterField(
            model_name="license",
            name="max_public_gateway_capacity_bytes",
            field=models.BigIntegerField(default=5368709120000),
        ),
        migrations.AlterField(
            model_name="licensehistory",
            name="max_public_gateway_capacity_bytes",
            field=models.BigIntegerField(default=5368709120000),
        ),
        migrations.RunPython(capacity_gib_to_bytes, capacity_bytes_to_gib),
    ]
