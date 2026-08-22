import math

from django.db import migrations, models


_MAX_BIGINT = 2**63 - 1
_MIN_BIGINT = -(2**63)


def _normalized_path(value):
    normalized = str(value or "").replace("\\", "/").strip()
    return normalized.rstrip("/") or "/"


def _summary_bytes(scope):
    if not isinstance(scope, dict):
        return None
    path_type = str(scope.get("path_type") or "").lower()
    if path_type not in {"file", "dir"}:
        return None
    try:
        file_count = _exact_int(scope["file_count"])
        size_bytes = _exact_int(scope["size_bytes"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if file_count < 0 or size_bytes < 0 or (path_type == "file" and file_count != 1):
        return None
    return size_bytes


def _scope_directory_identity(scope):
    value = scope.get("backup_snapshot_directory_id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value) if value is not None else None


def _canonical_scopes(scopes):
    retained = []
    ordered = sorted(
        enumerate(scopes),
        key=lambda row: (len(_normalized_path(row[1].get("source_path"))), row[0]),
    )
    for _index, candidate in ordered:
        candidate_path = _normalized_path(candidate.get("source_path"))
        candidate_directory = _scope_directory_identity(candidate)
        covered = False
        for existing in retained:
            if _scope_directory_identity(existing) != candidate_directory:
                continue
            existing_path = _normalized_path(existing.get("source_path"))
            prefix = "/" if existing_path == "/" else f"{existing_path}/"
            if candidate_path == existing_path or candidate_path.startswith(prefix):
                covered = True
                break
        if not covered:
            retained.append(candidate)
    return retained


def _exact_int(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
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


def backfill_workspace_capacity(apps, schema_editor):
    binding_model = apps.get_model("lens_bridge", "LensWorkspaceBinding")
    directory_model = apps.get_model(
        "protection",
        "BackupSourceSnapshotDirectory",
    )
    rows = binding_model.objects.select_related("knowledge_source").all()
    for binding in rows.iterator(chunk_size=200):
        if binding.workspace_kind == "gateway_local":
            accounted_bytes = 0
            status = "exact"
        else:
            scopes = _canonical_scopes(
                [
                    scope
                    for scope in list(
                        binding.knowledge_source.source_scopes_json or []
                    )
                    if isinstance(scope, dict)
                ]
            )
            if not scopes:
                accounted_bytes = 0
                status = "unknown"
            elif all(_summary_bytes(scope) is not None for scope in scopes):
                accounted_bytes = sum(_summary_bytes(scope) or 0 for scope in scopes)
                status = "exact"
            else:
                grouped = {}
                ungrouped = []
                for scope in scopes:
                    try:
                        directory_id = int(scope.get("backup_snapshot_directory_id"))
                    except (TypeError, ValueError):
                        ungrouped.append(scope)
                        continue
                    grouped.setdefault(directory_id, []).append(scope)
                accounted_bytes = sum(
                    value
                    for value in (_summary_bytes(scope) for scope in ungrouped)
                    if value is not None
                )
                status = "unknown" if ungrouped else "exact"
                directories = {
                    int(directory.id): directory
                    for directory in directory_model.objects.filter(
                        id__in=list(grouped),
                        organization_id=binding.organization_id,
                    )
                }
                for directory_id, directory_scopes in grouped.items():
                    directory = directories.get(directory_id)
                    if directory is None:
                        known = [
                            value
                            for value in (
                                _summary_bytes(scope) for scope in directory_scopes
                            )
                            if value is not None
                        ]
                        accounted_bytes += sum(known)
                        if len(known) != len(directory_scopes):
                            status = "unknown"
                        continue
                    known = [_summary_bytes(scope) for scope in directory_scopes]
                    if all(value is not None for value in known):
                        accounted_bytes += sum(value or 0 for value in known)
                        continue
                    accounted_bytes += max(0, int(directory.size_bytes or 0))
                    selects_root = any(
                        _normalized_path(scope.get("source_path"))
                        == _normalized_path(directory.source_path)
                        for scope in directory_scopes
                    )
                    if status != "unknown" and not selects_root:
                        status = "conservative"
        if accounted_bytes > _MAX_BIGINT:
            status = "unknown"
        binding_model.objects.filter(pk=binding.pk).update(
            capacity_accounted_bytes=min(
                _MAX_BIGINT,
                max(0, int(accounted_bytes)),
            ),
            capacity_accounting_status=status,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0037_gateway_chat_queue"),
        ("protection", "0022_snapshot_storage_efficiency"),
    ]

    operations = [
        migrations.AddField(
            model_name="lenssessionlink",
            name="lifecycle_error_state_json",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="lensworkspacebinding",
            name="capacity_accounted_bytes",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="lensworkspacebinding",
            name="capacity_accounting_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("exact", "Exact"),
                    ("conservative", "Conservative"),
                    ("unknown", "Unknown"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.RunPython(
            backfill_workspace_capacity,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="lensworkspacebinding",
            constraint=models.CheckConstraint(
                condition=models.Q(capacity_accounted_bytes__gte=0),
                name="lens_bws_capacity_nonnegative_ck",
            ),
        ),
    ]
