from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("task", "0016_normalize_retry_trigger_sources")]
    operations = [migrations.AddField(
        model_name="task", name="initiator_context",
        field=models.JSONField(blank=True, default=dict),
    )]
