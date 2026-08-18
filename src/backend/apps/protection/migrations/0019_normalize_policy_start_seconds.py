from __future__ import annotations

from datetime import datetime

from django.db import migrations


MINUTE_START_FORMAT = "%Y-%m-%dT%H:%M"


def is_valid_minute_start(value):
    try:
        return datetime.strptime(value, MINUTE_START_FORMAT).strftime(MINUTE_START_FORMAT) == value
    except (TypeError, ValueError):
        return False


def normalize_policy_start_seconds(apps, schema_editor):
    BackupPolicy = apps.get_model("protection", "BackupPolicy")
    for policy in BackupPolicy.objects.iterator():
        schedule = policy.schedule
        if not isinstance(schedule, dict) or not schedule.get("mode"):
            continue
        starts_at = schedule.get("starts_at")
        if not isinstance(starts_at, str) or not is_valid_minute_start(starts_at):
            continue
        policy.schedule = {**schedule, "starts_at": f"{starts_at}:00"}
        policy.save(update_fields=["schedule"])


class Migration(migrations.Migration):
    dependencies = [("protection", "0018_backup_directory_repository_locator")]

    operations = [
        migrations.RunPython(normalize_policy_start_seconds, migrations.RunPython.noop),
    ]
