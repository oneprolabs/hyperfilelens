from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("node", "0015_node_installation_mode"),
    ]

    operations = [
        # Reinstallation preserves old console records, so multiple records
        # may intentionally correlate to the same physical host.
        migrations.RemoveConstraint(
            model_name="node",
            name="node_unique_active_host_fingerprint",
        ),
        migrations.AlterField(
            model_name="node",
            name="host_fingerprint",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text=(
                    "Non-authoritative product-scoped digest used only for host "
                    "correlation; duplicate values are expected after reinstallation."
                ),
                max_length=64,
            ),
        ),
    ]
