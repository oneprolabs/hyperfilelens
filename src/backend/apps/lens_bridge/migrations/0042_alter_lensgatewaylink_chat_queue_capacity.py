from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0041_gateway_organization_ownership"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lensgatewaylink",
            name="chat_queue_capacity",
            field=models.PositiveIntegerField(default=20),
        ),
    ]
