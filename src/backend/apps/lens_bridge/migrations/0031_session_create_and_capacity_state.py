import math

from django.db import migrations, models

_MAX_BIGINT = 2**63 - 1
_MIN_BIGINT = -(2**63)


def prepare_incomplete_session_reservations(apps, schema_editor):
    """Make every in-flight legacy Chat rebuild its trusted reservation."""

    del schema_editor
    session_model = apps.get_model("lens_bridge", "LensSessionLink")
    # Every row present while this migration runs predates durable reservations.
    # Existing workspaces remain the usage authority until a new/retried Chat
    # explicitly builds and reserves a trusted summary.
    session_model.objects.update(
        capacity_reservation_status="released",
        capacity_reserved_bytes=0,
        capacity_reserved_at=None,
    )
    rows = session_model.objects.filter(
        lifecycle_status__in=("provisioning", "failed"),
    ).only("id", "source_scopes_json")
    for row in rows.iterator(chunk_size=500):
        scopes = list(row.source_scopes_json or [])
        summaries_complete = bool(scopes) and all(
            _has_nonnegative_summary(scope) for scope in scopes
        )
        session_model.objects.filter(pk=row.pk).update(
            scope_resolution_status=(
                "resolved" if summaries_complete else "pending"
            ),
            capacity_reservation_status="pending",
            capacity_reserved_bytes=0,
            capacity_reserved_at=None,
        )


def _has_nonnegative_summary(scope):
    if not isinstance(scope, dict):
        return False
    path_type = str(scope.get("path_type") or "").lower()
    if path_type not in {"file", "dir"}:
        return False
    try:
        file_count = _exact_int(scope["file_count"])
        size_bytes = _exact_int(scope["size_bytes"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if file_count < 0 or size_bytes < 0:
        return False
    return path_type != "file" or file_count == 1


def _exact_int(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer value")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("value must be an exact integer")
        parsed = int(value)
    elif isinstance(value, str):
        parsed = int(value.strip())
    else:
        raise TypeError("value must be an integer")
    if parsed < _MIN_BIGINT or parsed > _MAX_BIGINT:
        raise OverflowError("value is outside the database integer range")
    return parsed


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0030_lens_run_submission"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lenssessionlink",
            name="provision_phase",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("resolving_scope", "Validating selected data"),
                    ("reserving_capacity", "Reserving gateway capacity"),
                    ("restoring", "Restoring backup data"),
                    ("converting", "Extracting document content"),
                    ("creating_knowledge_source", "Creating knowledge source"),
                    ("creating_assistant", "Creating assistant"),
                    ("granting_assistant", "Granting assistant"),
                    ("creating_session", "Creating chat session"),
                    ("ready", "Ready"),
                    ("cleaning_up", "Cleaning up"),
                    ("deleting", "Deleting"),
                    ("deleted", "Deleted"),
                ],
                db_index=True,
                default="ready",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="create_idempotency_key",
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="create_request_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="scope_resolution_status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("resolved", "Resolved")],
                db_index=True,
                default="resolved",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="capacity_reservation_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("reserved", "Reserved"),
                    ("released", "Released"),
                ],
                db_index=True,
                default="reserved",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="capacity_reserved_bytes",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="capacity_reserved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="lenssessionlink",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    create_idempotency_key__isnull=False,
                    is_deleted=False,
                ),
                fields=("organization", "hfl_user", "create_idempotency_key"),
                name="uniq_lens_session_create_key",
            ),
        ),
        # PostgreSQL cannot create the constraint's backing index while the
        # preceding data updates have pending deferred trigger events. Keep all
        # schema DDL before the backfill so this migration remains atomic.
        migrations.RunPython(
            prepare_incomplete_session_reservations,
            migrations.RunPython.noop,
        ),
    ]
