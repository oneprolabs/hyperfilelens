from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("alerts", "0003_remove_alert_notification_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="alertpolicy",
            name="last_evaluated_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
