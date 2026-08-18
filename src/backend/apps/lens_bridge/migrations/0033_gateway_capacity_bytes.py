from django.db import migrations, models


GIB = 1024**3


def _bytes_to_legacy_gib(value):
    """Round positive byte limits up so rollback cannot empty a usable pool."""
    if value <= 0:
        return value
    return (value + GIB - 1) // GIB


def capacity_gib_to_bytes(apps, schema_editor):
    del schema_editor
    link_model = apps.get_model("lens_bridge", "LensGatewayLink")
    for link in link_model.objects.exclude(capacity_bytes=-1).iterator():
        link.capacity_bytes *= GIB
        link.save(update_fields=["capacity_bytes"])


def capacity_bytes_to_gib(apps, schema_editor):
    del schema_editor
    link_model = apps.get_model("lens_bridge", "LensGatewayLink")
    for link in link_model.objects.exclude(capacity_bytes=-1).iterator():
        link.capacity_bytes = _bytes_to_legacy_gib(link.capacity_bytes)
        link.save(update_fields=["capacity_bytes"])


class Migration(migrations.Migration):
    dependencies = [("lens_bridge", "0032_lens_run_submission_attachments")]

    operations = [
        migrations.RenameField(
            model_name="lensgatewaylink",
            old_name="capacity_gb",
            new_name="capacity_bytes",
        ),
        migrations.AlterField(
            model_name="lensgatewaylink",
            name="capacity_bytes",
            field=models.BigIntegerField(default=-1),
        ),
        migrations.RunPython(capacity_gib_to_bytes, capacity_bytes_to_gib),
    ]
