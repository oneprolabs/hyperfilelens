"""Remove the unused backup task concurrency configuration rows."""

from django.db import migrations


OBSOLETE_KEY = "file_dr.dr_task_concurrency"


def remove_obsolete_rows(apps, schema_editor):
    GlobalConfig = apps.get_model("app_config", "GlobalConfig")
    database_alias = schema_editor.connection.alias
    GlobalConfig.objects.using(database_alias).filter(key=OBSOLETE_KEY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app_config", "0003_platform_runtime_setting_state"),
    ]

    operations = [
        migrations.RunPython(remove_obsolete_rows, migrations.RunPython.noop),
    ]
