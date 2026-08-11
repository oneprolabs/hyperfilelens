from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0028_gateway_link_capacity_gb"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensusageledger",
            name="source_synced_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="lensusageledger",
            name="reconciliation_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="lensusageledger",
            name="reconciliation_claim_token",
            field=models.UUIDField(blank=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="lensusageledger",
            name="reconciliation_claimed_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="lensusageledger",
            name="reconciliation_next_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="lensusageledger",
            name="reconciliation_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddIndex(
            model_name="lensusageledger",
            index=models.Index(
                fields=["run_status", "reconciliation_next_at"],
                name="lens_busg_st_recon_idx",
            ),
        ),
    ]
