from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0049_platform_gateway_owner_constraint"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensrunsubmission",
            name="deep_thinking",
            field=models.BooleanField(blank=True, default=None, null=True),
        ),
    ]
