from django.db import migrations, models

from apps.node.models.base import NodeInstallationMode


class Migration(migrations.Migration):
    dependencies = [
        ("node", "0017_add_specified_user_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="node",
            name="installation_mode",
            field=models.CharField(
                choices=NodeInstallationMode.choices,
                db_index=True,
                default=NodeInstallationMode.SYSTEM,
                help_text="Immutable protection mode selected for one Agent installation.",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="nodetoken",
            name="installation_mode",
            field=models.CharField(
                choices=NodeInstallationMode.choices,
                db_index=True,
                default=NodeInstallationMode.SYSTEM,
                help_text="Installation and runtime mode authorized by this token.",
                max_length=16,
            ),
        ),
    ]
