from django.db import migrations, models


GIB = 1024**3
UNLIMITED = -1


def storage_gib_to_bytes(apps, schema_editor):
    for model_name in ("License", "LicenseHistory"):
        model = apps.get_model("subscription", model_name)
        for row in model.objects.exclude(max_storage_bytes=UNLIMITED).iterator():
            row.max_storage_bytes = int(row.max_storage_bytes) * GIB
            row.save(update_fields=["max_storage_bytes"])


def storage_bytes_to_gib(apps, schema_editor):
    for model_name in ("License", "LicenseHistory"):
        model = apps.get_model("subscription", model_name)
        for row in model.objects.exclude(max_storage_bytes=UNLIMITED).iterator():
            row.max_storage_bytes = int(row.max_storage_bytes) // GIB
            row.save(update_fields=["max_storage_bytes"])


class Migration(migrations.Migration):
    dependencies = [("subscription", "0010_public_gateway_capacity_bytes")]

    operations = [
        migrations.RenameField(
            model_name="license",
            old_name="max_storage_gb",
            new_name="max_storage_bytes",
        ),
        migrations.RenameField(
            model_name="licensehistory",
            old_name="max_storage_gb",
            new_name="max_storage_bytes",
        ),
        migrations.AlterField(
            model_name="license",
            name="max_storage_bytes",
            field=models.BigIntegerField(default=5000 * GIB),
        ),
        migrations.AlterField(
            model_name="licensehistory",
            name="max_storage_bytes",
            field=models.BigIntegerField(),
        ),
        migrations.RunPython(storage_gib_to_bytes, storage_bytes_to_gib),
    ]
