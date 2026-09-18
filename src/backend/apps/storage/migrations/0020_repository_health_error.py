from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0019_repository_credential_rotation"),
    ]

    operations = [
        migrations.AddField(
            model_name="repository",
            name="health_error_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="repository",
            name="health_error_message",
            field=models.CharField(blank=True, default="", max_length=1000),
        ),
    ]
