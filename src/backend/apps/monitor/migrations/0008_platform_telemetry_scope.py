import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0007_repository_usage_observed_at"),
    ]
    operations = [
        migrations.AlterField(
            model_name="operationalevent", name="organization",
            field=models.ForeignKey(
                to="iam.organization", on_delete=django.db.models.deletion.CASCADE,
                related_name="operational_events", null=True, blank=True,
            ),
        ),
        migrations.AlterField(
            model_name="resourcemetric", name="organization",
            field=models.ForeignKey(
                to="iam.organization", on_delete=django.db.models.deletion.CASCADE,
                related_name="resource_metrics", null=True, blank=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="operationalevent",
            constraint=models.UniqueConstraint(
                fields=["dedup_key"],
                condition=models.Q(organization__isnull=True) & ~models.Q(dedup_key=""),
                name="mon_platform_event_dedup",
            ),
        ),
    ]
