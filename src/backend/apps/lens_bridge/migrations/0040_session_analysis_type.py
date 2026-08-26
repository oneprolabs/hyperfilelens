from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0039_session_analysis_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="lenssessionlink",
            name="analysis_type",
            field=models.CharField(
                choices=[
                    ("knowledge_qa", "Knowledge Q&A"),
                    ("code_analysis", "Code Analysis"),
                ],
                db_index=True,
                blank=True,
                default=None,
                max_length=32,
                null=True,
            ),
        ),
    ]
