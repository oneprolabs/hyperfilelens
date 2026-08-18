import hashlib
import json

from django.db import migrations


ACTIVE_STATES = {"reserved", "initializing", "owned", "residual"}


def _namespace_key(identity):
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parts(value):
    return tuple(
        part for part in str(value or "").strip().replace("\\", "/").split("/") if part
    )


def _share(value):
    return ("/" + "/".join(_parts(value))).rstrip("/") or "/"


def _roots_overlap(left, right):
    left_parts = _parts(left)
    right_parts = _parts(right)
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def repair_bound_nas_location_owners(apps, schema_editor):
    Repository = apps.get_model("storage", "Repository")
    Claim = apps.get_model("storage", "RepositoryLocationClaim")

    repositories = Repository.objects.filter(
        repo_type="nas",
        status="created",
        bind_node_id__isnull=False,
    ).exclude(bind_node_id=0)
    for repository in repositories.iterator():
        config = repository.config if isinstance(repository.config, dict) else {}
        protocol = str(repository.nas_protocol or "").strip().lower()
        server = (
            str(config.get("server_address") or "")
            .strip()
            .rstrip("/")
            .lower()
            .rstrip(".")
        )
        share = _share(config.get("share_path"))
        if protocol == "smb":
            share = share.lower()
        expected_namespace_key = _namespace_key(
            {
                "kind": "nas",
                "execution_node_id": int(repository.bind_node_id),
                "protocol": protocol,
                "server": server,
                "share": share,
            }
        )
        expected_root = f"hp-repos/storage-{int(repository.id)}"
        claim = (
            Claim.objects.filter(
                repository_id=repository.id,
                scope="repository",
                root_path=expected_root,
                owner_node_id__isnull=True,
                namespace__namespace_key=expected_namespace_key,
                state__in=ACTIVE_STATES,
            )
            .order_by("id")
            .first()
        )
        if claim is None:
            continue
        conflicting = False
        for other in Claim.objects.filter(
            namespace_id=claim.namespace_id,
            state__in=ACTIVE_STATES,
        ).exclude(id=claim.id):
            if _roots_overlap(claim.root_path, other.root_path):
                conflicting = True
                break
        if conflicting:
            continue
        updates = {
            "owner_node_id": int(repository.bind_node_id),
        }
        if claim.state == "owned":
            updates.update(
                legacy_adoption_required=True,
                initialized_at=claim.initialized_at or repository.created_at,
                last_verified_at=claim.last_verified_at
                or repository.last_checked_at,
                ownership_verified_at=None,
                released_at=None,
            )
        Claim.objects.filter(id=claim.id, owner_node_id__isnull=True).update(
            **updates
        )


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0017_repository_location_claims"),
    ]

    operations = [
        migrations.RunPython(
            repair_bound_nas_location_owners,
            migrations.RunPython.noop,
        ),
    ]
