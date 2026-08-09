from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0005_drop_detached_ledger_foreign_keys"),
    ]

    operations = [
        migrations.AddField(
            model_name="license",
            name="max_public_gateways",
            field=models.IntegerField(default=20),
        ),
        migrations.AddField(
            model_name="license",
            name="max_public_gateway_capacity_gb",
            field=models.IntegerField(default=5000),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_public_gateways",
            field=models.IntegerField(default=20),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_public_gateway_capacity_gb",
            field=models.IntegerField(default=5000),
        ),
    ]
