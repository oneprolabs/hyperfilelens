from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("alerts", "0007_align_alert_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name="alertpolicy",
            name="default_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="alertpolicy",
            constraint=models.UniqueConstraint(
                condition=Q(("owner_scope", "organization"), ("default_key__isnull", False)),
                fields=("organization", "default_key"),
                name="alert_pol_org_default_key_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="alertpolicy",
            constraint=models.UniqueConstraint(
                condition=Q(("owner_scope", "platform"), ("default_key__isnull", False)),
                fields=("default_key",),
                name="alert_pol_platform_default_key_uniq",
            ),
        ),
    ]
