"""Add explicit Chat cleanup state and provision poll fencing."""

from django.db import migrations, models
from django.db.models.functions import Coalesce


def migrate_chat_lifecycle_state(apps, schema_editor):
    KnowledgeSource = apps.get_model("lens_bridge", "LensKnowledgeSource")
    SessionLink = apps.get_model("lens_bridge", "LensSessionLink")

    chat_knowledge_source_ids = SessionLink.objects.filter(
        knowledge_source_id__isnull=False
    ).values_list("knowledge_source_id", flat=True)
    KnowledgeSource.objects.filter(
        pk__in=chat_knowledge_source_ids,
        backup_source_snapshot_id__isnull=False,
    ).update(
        linked_version_mode="pinned",
        pinned_snapshot_id=Coalesce(
            "pinned_snapshot_id",
            "backup_source_snapshot_id",
        ),
    )

    for session in SessionLink.objects.filter(lifecycle_status="deleting").iterator():
        state = session.teardown_state_json or {}
        intent = str(state.get("intent") or "delete_session")
        if intent not in {"reset_for_retry", "delete_session"}:
            intent = "delete_session"
        values = {
            "cleanup_intent": intent,
            "cleanup_status": "pending",
        }
        if intent == "reset_for_retry":
            values.update(
                lifecycle_status="failed",
                status="active",
                provision_phase="cleaning_up",
            )
        SessionLink.objects.filter(pk=session.pk).update(
            **values,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("lens_bridge", "0033_gateway_capacity_bytes"),
    ]

    operations = [
        migrations.AddField(
            model_name="lenssessionlink",
            name="cleanup_intent",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("reset_for_retry", "Reset for retry"),
                    ("delete_session", "Delete session"),
                ],
                db_index=True,
                default="none",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="cleanup_status",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("blocked", "Blocked"),
                    ("complete", "Complete"),
                ],
                db_index=True,
                default="none",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="provision_generation",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="provision_poll_sequence",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RunPython(
            migrate_chat_lifecycle_state,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
