"""Rescale legacy License AI request-count defaults to lifetime token budgets.

Org ``ee_quota`` rows are rescaled in EE ``subscription_gov.0003`` (historical
apps cannot see that model from this OSS migration).
"""

from django.db import migrations, models

_OLD_REQUEST_DEFAULT = 500
_NEW_TOKEN_DEFAULT = 50_000_000


def forwards_rescale_license_ai(apps, schema_editor):
    License = apps.get_model("subscription", "License")
    LicenseHistory = apps.get_model("subscription", "LicenseHistory")
    License.objects.filter(ai_insights_quota=_OLD_REQUEST_DEFAULT).update(
        ai_insights_quota=_NEW_TOKEN_DEFAULT
    )
    LicenseHistory.objects.filter(ai_insights_quota=_OLD_REQUEST_DEFAULT).update(
        ai_insights_quota=_NEW_TOKEN_DEFAULT
    )


def backwards_noop(apps, schema_editor):
    # Do not shrink token budgets back to request-count defaults.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0006_public_gateway_license_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="license",
            name="ai_insights_quota",
            field=models.IntegerField(default=50_000_000),
        ),
        migrations.RunPython(forwards_rescale_license_ai, backwards_noop),
    ]
