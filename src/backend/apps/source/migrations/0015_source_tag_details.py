from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("source", "0014_source_tags")]

    operations = [
        migrations.AddField(
            model_name="sourcetag",
            name="description",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="sourcetag",
            name="color",
            field=models.CharField(default="neutral", max_length=16),
        ),
    ]
