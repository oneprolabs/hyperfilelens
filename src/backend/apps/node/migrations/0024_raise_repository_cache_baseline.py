"""Raise existing repository cache budgets once, without new resource limits."""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def raise_repository_cache_baseline(apps, schema_editor):
    Node = apps.get_model("node", "Node")
    nodes = (
        Node.objects.using(schema_editor.connection.alias)
        .select_for_update()
        .filter(is_deleted=False, role__in=["agent", "proxy"])
        .only("pk", "metadata")
        .order_by("pk")
    )
    for node in nodes.iterator(chunk_size=500):
        metadata = deepcopy(node.metadata) if isinstance(node.metadata, dict) else {}
        policy = metadata.get("performance_policy")
        active = policy.get("settings") if isinstance(policy, dict) else None
        display = metadata.get("perf_settings")
        display = display if isinstance(display, dict) else {}
        source = active if isinstance(active, dict) else display
        old = source.get("kopiaCacheMb", source.get("kopia_cache_mb"))
        target = max(old, 8192) if type(old) is int and 0 <= old <= 65536 else 8192

        display["kopiaCacheMb"] = target
        if "kopia_cache_mb" in display:
            display["kopia_cache_mb"] = target
        metadata["perf_settings"] = display
        if isinstance(active, dict) and (
            type(active.get("kopiaCacheMb")) is not int
            or active.get("kopiaCacheMb") != target
        ):
            active["kopiaCacheMb"] = target
            if "kopia_cache_mb" in active:
                active["kopia_cache_mb"] = target
            report = metadata.get("performance_status")
            observed = report.get("observed_revision") if isinstance(report, dict) else None
            revisions = [
                value for value in (policy.get("revision"), observed)
                if type(value) is int and 0 <= value <= 1_000_000_000
            ]
            policy["revision"] = max(revisions, default=0) + 1
            policy["saved_at"] = timezone.now().isoformat()
            metadata.pop("performance_status", None)
        if metadata != node.metadata:
            Node.objects.using(schema_editor.connection.alias).filter(pk=node.pk).update(
                metadata=metadata,
            )


class Migration(migrations.Migration):
    dependencies = [("node", "0023_platform_token_owner_constraint")]
    operations = [
        migrations.RunPython(raise_repository_cache_baseline, migrations.RunPython.noop),
    ]
