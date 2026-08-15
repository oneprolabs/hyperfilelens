import hashlib
import json
import posixpath
import secrets
import uuid
from urllib.parse import urlparse

import apps.storage.repositories.models
from django.db import migrations, models
import django.db.models.deletion


def _namespace_key(identity):
    payload = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parts(value):
    return tuple(
        part for part in str(value or "").strip().replace("\\", "/").split("/") if part
    )


def _root(value):
    return "/".join(_parts(value))


def _roots_overlap(left, right):
    left_parts = _parts(left)
    right_parts = _parts(right)
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def _share(value):
    return ("/" + "/".join(_parts(value))).rstrip("/") or "/"


def _host(value):
    raw = str(value or "").strip()
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    return str(parsed.netloc or parsed.path).strip().rstrip("/").lower().rstrip(".")


def _claim_state(repository):
    # A repository that was being removed when the migration runs has an
    # uncertain physical outcome. Keep its location quarantined until an
    # ownership check or an explicit cleanup resolves it; only a stable
    # created repository is safe to backfill as owned.
    if repository.status == "created":
        return "owned"
    return "residual"


def backfill_repository_location_claims(apps, schema_editor):
    Repository = apps.get_model("storage", "Repository")
    DeploymentIdentity = apps.get_model("storage", "RepositoryDeploymentIdentity")
    Shard = apps.get_model("storage", "RepositoryUsageShard")
    Namespace = apps.get_model("storage", "RepositoryLocationNamespace")
    Claim = apps.get_model("storage", "RepositoryLocationClaim")

    DeploymentIdentity.objects.get_or_create(
        pk=1,
        defaults={
            "deployment_uuid": uuid.uuid4(),
            "ownership_signing_key": secrets.token_hex(32),
        },
    )
    for repository in Repository.objects.filter(
        repository_uuid__isnull=True
    ).iterator():
        repository.repository_uuid = uuid.uuid4()
        repository.save(update_fields=["repository_uuid"])

    repositories = Repository.objects.exclude(
        status="removed",
        cleanup_result="deleted",
    )
    for repository in repositories.iterator():
        config = repository.config if isinstance(repository.config, dict) else {}
        definitions = []
        if repository.repo_type == "s3":
            endpoint = (
                str(
                    config.get("endpoint")
                    or config.get("external_endpoint")
                    or config.get("internal_endpoint")
                    or ""
                )
                .strip()
                .rstrip("/")
                .lower()
            )
            bucket = str(repository.s3_bucket or "").strip().lower()
            identity = {
                "kind": "s3",
                "endpoint": _host(endpoint),
                "bucket": bucket,
            }
            definitions.append(
                (
                    "s3",
                    identity,
                    "/".join(part for part in (endpoint, bucket) if part),
                    _root(config.get("prefix")),
                    "repository",
                    None,
                    _claim_state(repository),
                )
            )
        elif repository.repo_type == "nas":
            server = (
                str(config.get("server_address") or "")
                .strip()
                .rstrip("/")
                .lower()
                .rstrip(".")
            )
            share = _share(config.get("share_path"))
            protocol = str(repository.nas_protocol or "").strip().lower()
            if protocol == "smb":
                share = share.lower()
            base_identity = {
                "kind": "nas",
                "protocol": protocol,
                "server": server,
                "share": share,
            }
            if repository.bind_node_id:
                identity = {
                    **base_identity,
                    "execution_node_id": int(repository.bind_node_id),
                }
                definitions.append(
                    (
                        "nas",
                        identity,
                        "/".join(part for part in (server, share.strip("/")) if part),
                        f"hp-repos/storage-{int(repository.id)}",
                        "repository",
                        None,
                        _claim_state(repository),
                    )
                )
            for shard in Shard.objects.filter(repository_id=repository.id).iterator():
                identity = {
                    **base_identity,
                    "execution_node_id": int(shard.node_id),
                }
                state = (
                    "owned"
                    if not repository.bind_node_id
                    and repository.status != "removed"
                    and shard.is_active
                    and shard.last_success_checked_at
                    else "residual"
                )
                definitions.append(
                    (
                        "nas",
                        identity,
                        "/".join(part for part in (server, share.strip("/")) if part),
                        _root(shard.repository_subdir),
                        "direct_nas_agent",
                        shard.node_id,
                        state,
                    )
                )
        elif repository.repo_type == "proxy_fs":
            base = posixpath.normpath(
                str(
                    config.get("proxy_node_base_dir")
                    or config.get("proxy_node_dir")
                    or ""
                ).strip()
            )
            identity = {
                "kind": "proxy_fs",
                "node_id": int(repository.bind_node_id or 0),
            }
            definitions.append(
                (
                    "proxy_fs",
                    identity,
                    f"Proxy #{int(repository.bind_node_id or 0)}: {base}",
                    posixpath.normpath(str(config.get("proxy_node_dir") or "").strip()),
                    "repository",
                    int(repository.bind_node_id or 0) or None,
                    _claim_state(repository),
                )
            )

        for kind, identity, hint, root_path, scope, node_id, state in definitions:
            if kind == "s3":
                # Older releases allowed a Kopia repository at the Bucket root.
                # Treat it as the parent of every Prefix so a later repository
                # cannot be initialized inside the same physical namespace.
                root_path = root_path or "/"
            elif not root_path or root_path in {".", "/"}:
                continue
            namespace, _created = Namespace.objects.get_or_create(
                namespace_key=_namespace_key(identity),
                defaults={"kind": kind, "display_hint": hint[:700]},
            )
            claim = Claim.objects.create(
                organization_id=repository.organization_id,
                repository_id=repository.id,
                namespace_id=namespace.id,
                scope=scope,
                root_path=root_path,
                owner_node_id=node_id,
                state=state,
                legacy_adoption_required=(state == "owned"),
                initialized_at=(repository.created_at if state == "owned" else None),
                last_verified_at=(
                    repository.last_checked_at if state == "owned" else None
                ),
            )
            overlapping_ids = [
                other.id
                for other in Claim.objects.filter(namespace_id=namespace.id)
                .exclude(id=claim.id)
                .exclude(state="released")
                if _roots_overlap(root_path, other.root_path)
            ]
            if overlapping_ids:
                Claim.objects.filter(id__in=[claim.id, *overlapping_ids]).update(
                    state="residual",
                    initialized_at=None,
                    last_verified_at=None,
                )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0016_merge_0015_storage_branches"),
    ]

    operations = [
        migrations.AddField(
            model_name="repository",
            name="repository_uuid",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.CreateModel(
            name="RepositoryDeploymentIdentity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "deployment_uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "ownership_signing_key",
                    models.CharField(
                        default=apps.storage.repositories.models.repository_ownership_signing_key,
                        editable=False,
                        max_length=64,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "storage_repository_deployment_identity"},
        ),
        migrations.CreateModel(
            name="RepositoryLocationNamespace",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("namespace_key", models.CharField(max_length=64, unique=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("s3", "S3 bucket"),
                            ("nas", "NAS share"),
                            ("proxy_fs", "Proxy filesystem"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "display_hint",
                    models.CharField(blank=True, default="", max_length=700),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "storage_repository_location_namespace",
                "ordering": ["kind", "namespace_key"],
            },
        ),
        migrations.CreateModel(
            name="RepositoryLocationClaim",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("organization_id", models.BigIntegerField(db_index=True)),
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("repository", "Repository"),
                            ("direct_nas_agent", "Direct NAS Agent"),
                        ],
                        default="repository",
                        max_length=40,
                    ),
                ),
                ("root_path", models.CharField(max_length=1000)),
                (
                    "owner_node_id",
                    models.BigIntegerField(blank=True, db_index=True, null=True),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("reserved", "Reserved"),
                            ("initializing", "Initialization dispatched"),
                            ("owned", "Owned"),
                            ("residual", "Residual data retained"),
                            ("released", "Released"),
                        ],
                        db_index=True,
                        default="reserved",
                        max_length=20,
                    ),
                ),
                ("initialized_at", models.DateTimeField(blank=True, null=True)),
                ("last_verified_at", models.DateTimeField(blank=True, null=True)),
                ("namespace_resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "ownership_verified_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "legacy_adoption_required",
                    models.BooleanField(default=False),
                ),
                ("released_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "namespace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="claims",
                        to="storage.repositorylocationnamespace",
                    ),
                ),
                (
                    "repository",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="location_claims",
                        to="storage.repository",
                    ),
                ),
            ],
            options={
                "db_table": "storage_repository_location_claim",
                "ordering": ["namespace_id", "root_path", "id"],
                "indexes": [
                    models.Index(
                        fields=["repository", "scope", "state"],
                        name="stor_rlc_repo_scope_state_idx",
                    ),
                    models.Index(
                        fields=["namespace", "state"],
                        name="stor_rlc_namespace_state_idx",
                    ),
                ],
            },
        ),
        migrations.RunPython(backfill_repository_location_claims, noop_reverse),
        migrations.AlterField(
            model_name="repository",
            name="repository_uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
