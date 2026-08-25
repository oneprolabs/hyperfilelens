from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("node", "0018_add_user_continuous_mode")]

    operations = [
        migrations.AddField(
            model_name="nodetoken",
            name="installation_mode_policy",
            field=models.CharField(
                choices=[("fixed", "Fixed"), ("auto", "Automatic")],
                db_index=True,
                default="fixed",
                help_text=(
                    "Fixed authorizes installation_mode. Automatic authorizes the "
                    "platform-specific mode selected from the install process identity."
                ),
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="nodetoken",
            name="target_platform",
            field=models.CharField(
                blank=True,
                choices=[
                    ("linux", "Linux"),
                    ("windows", "Windows"),
                    ("macos", "macOS"),
                ],
                default="",
                help_text="Operating system bound to an automatic source-Agent token.",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="nodetoken",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        installation_mode_policy="fixed",
                        target_platform="",
                    )
                    | models.Q(
                        installation_mode_policy="auto",
                        role="agent",
                        target_platform__in=["linux", "windows", "macos"],
                    )
                ),
                name="node_token_auto_mode_platform",
            ),
        ),
    ]
