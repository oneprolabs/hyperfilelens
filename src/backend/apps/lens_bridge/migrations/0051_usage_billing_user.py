from django.db import migrations, models


def preserve_known_users(apps, schema_editor):
    Ledger = apps.get_model("lens_bridge", "LensUsageLedger")
    Ledger.objects.using(schema_editor.connection.alias).filter(
        billing_user_id__isnull=True, hfl_user_id__isnull=False,
    ).update(billing_user_id=models.F("hfl_user_id"))


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0050_run_submission_deep_thinking"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensusageledger",
            name="billing_user_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(preserve_known_users, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="lensusageledger",
            index=models.Index(
                fields=["organization", "billing_user_id"],
                name="lens_busg_org_billusr_idx",
            ),
        ),
    ]
