from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0007_ai_insights_quota_token_scale"),
    ]

    operations = [
        migrations.AddField(
            model_name="license",
            name="max_source_nas",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="license",
            name="max_object_storage",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="license",
            name="max_target_nas",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="license",
            name="max_standalone_disk",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="license",
            name="max_protected_sources",
            field=models.IntegerField(default=500),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_source_nas",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_object_storage",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_target_nas",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_standalone_disk",
            field=models.IntegerField(default=200),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="max_protected_sources",
            field=models.IntegerField(default=500),
        ),
    ]
