from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lens_bridge", "0051_usage_billing_user")]

    operations = [
        migrations.AddField(
            model_name="lensusageledger",
            name="provider_error_state_json",
            field=models.JSONField(blank=True, default=dict, db_default={}),
        ),
        migrations.AddIndex(
            model_name="lensusageledger",
            index=models.Index(
                fields=["id"],
                condition=models.Q(provider_error_state_json__status="pending"),
                name="lens_busg_provider_pending_idx",
            ),
        ),
    ]
