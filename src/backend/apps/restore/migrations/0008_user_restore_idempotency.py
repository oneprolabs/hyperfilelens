from django.db import migrations, models
from django.db.models import Count


def clear_duplicate_user_restore_keys(apps, schema_editor):
    RestoreRecord = apps.get_model("restore", "RestoreRecord")
    duplicate_keys = (
        RestoreRecord.objects.filter(purpose="user_data")
        .exclude(idempotency_key="")
        .values("organization_id", "purpose", "idempotency_key")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for duplicate in duplicate_keys.iterator():
        records = RestoreRecord.objects.filter(
            organization_id=duplicate["organization_id"],
            purpose=duplicate["purpose"],
            idempotency_key=duplicate["idempotency_key"],
        ).order_by("created_at", "id")
        duplicate_ids = list(records.values_list("id", flat=True)[1:])
        if duplicate_ids:
            RestoreRecord.objects.filter(id__in=duplicate_ids).update(
                idempotency_key=""
            )


class Migration(migrations.Migration):
    dependencies = [
        ("restore", "0007_direct_nas_mount_lease"),
    ]

    operations = [
        migrations.RunPython(
            clear_duplicate_user_restore_keys,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="restorerecord",
            name="uniq_restore_org_purpose_idem",
        ),
        migrations.AddConstraint(
            model_name="restorerecord",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("organization_id", "purpose", "idempotency_key"),
                name="uniq_restore_org_purpose_idem",
            ),
        ),
    ]
