from django.db import migrations, models


def grant_existing_licenses_all_features(apps, schema_editor):
    """Preserve pre-feature-licensing behavior for existing deployments."""
    License = apps.get_model("subscription", "License")
    LicenseHistory = apps.get_model("subscription", "LicenseHistory")
    License.objects.filter(features=[]).update(features=["*"])
    LicenseHistory.objects.filter(features=[]).update(features=["*"])


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0008_workload_license_limits"),
    ]

    operations = [
        migrations.AddField(
            model_name="license",
            name="features",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="licensehistory",
            name="features",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(
            grant_existing_licenses_all_features,
            migrations.RunPython.noop,
        ),
    ]
