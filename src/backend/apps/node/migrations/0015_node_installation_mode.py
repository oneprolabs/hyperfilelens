from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("node", "0014_nodetask_parent_task"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="installation_mode",
            field=models.CharField(
                choices=[("system", "System Service"), ("user", "Current User")],
                db_index=True,
                default="system",
                help_text="Immutable user-level or system-level installation mode.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="nodetoken",
            name="installation_mode",
            field=models.CharField(
                choices=[("system", "System Service"), ("user", "Current User")],
                db_index=True,
                default="system",
                help_text="Installation and runtime mode authorized by this token.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="node",
            name="host_fingerprint",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text=(
                    "Product-scoped digest used to prevent duplicate host installations."
                ),
                max_length=64,
            ),
        ),
        migrations.AddConstraint(
            model_name="node",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(installation_mode="system") | models.Q(role="agent")
                ),
                name="node_user_mode_source_agent_only",
            ),
        ),
        migrations.AddConstraint(
            model_name="nodetoken",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(installation_mode="system") | models.Q(role="agent")
                ),
                name="node_token_user_mode_agent_only",
            ),
        ),
        migrations.AddConstraint(
            model_name="node",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_deleted=False) & ~models.Q(host_fingerprint=""),
                fields=("host_fingerprint",),
                name="node_unique_active_host_fingerprint",
            ),
        ),
    ]
