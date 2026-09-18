from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0042_alter_lensgatewaylink_chat_queue_capacity"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lenssessionlink",
            name="active_run_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
