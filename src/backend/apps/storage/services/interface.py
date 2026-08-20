from __future__ import annotations

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.storage.repositories.models import Credential, Repository
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
    check_s3_repository,
)
from apps.storage.services.internal.nas_repository import (
    NASRepositoryError,
    check_proxy_nas_repository,
    nas_mount_point,
)
from apps.storage.services.internal.proxy_fs_repository import (
    ProxyFSRepositoryError,
    check_proxy_fs_repository,
    configure_managed_proxy_fs_path,
)
from apps.storage.services.internal.repository_create import (
    enqueue_repository_create_task,
    preflight_bound_proxy,
    retry_repository_create,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
    REPOSITORY_ALREADY_EXISTS_MESSAGE,
)
from apps.storage.services.internal.repository_health import (
    is_repository_ownership_failure,
    probe_unbound_nas_repository_health,
)
from apps.storage.services.internal.repository_location import (
    RepositoryLocationConflict,
    invalidate_repository_location_ownership,
    reserve_repository_location,
)
from apps.storage.services.internal.repository_cleanup import (
    RepositoryCleanupBlocked,
    create_direct_nas_target_cleanup_task,
    direct_nas_cleanup_target_ids,
    repository_active_task_blockers,
    repository_cleanup_task_payload,
    run_repository_cleanup_task,
)
from apps.storage.services.internal.repository_usage import (
    apply_capacity_from_config,
    capacity_bytes_from_config,
    enqueue_repository_usage_refresh,
    sync_repository_usage,
)
from apps.storage.services.internal.repository_secrets import (
    build_credential_metadata,
    build_secret_payload,
    sanitize_repository_config,
    scrub_secrets,
)
from apps.task.models import Task
from common.errors import AppError
from apps.storage.provider_catalog.operations import (
    export_catalog as export_provider_catalog,
    import_apply as apply_provider_catalog_import,
    import_diff as diff_provider_catalog_import,
    import_review as review_provider_catalog_import,
    reset_confirm as confirm_provider_catalog_reset,
    reset_review as review_provider_catalog_reset,
)

__all__ = [
    "RepositoryCleanupBlocked",
    "create_direct_nas_target_cleanup_task",
    "direct_nas_cleanup_target_ids",
    "repository_active_task_blockers",
    "repository_cleanup_task_payload",
    "retry_repository_create",
    "run_repository_cleanup_task",
    "apply_provider_catalog_import",
    "confirm_provider_catalog_reset",
    "diff_provider_catalog_import",
    "export_provider_catalog",
    "review_provider_catalog_import",
    "review_provider_catalog_reset",
]


def _set_nas_proxy_mount_path(repository: Repository) -> bool:
    if (
        repository.repo_type != Repository.Type.NAS
        or repository.bind_node_type != Repository.BindNodeType.PROXY
        or not repository.bind_node_id
    ):
        return False
    config = dict(repository.config or {})
    existing = str(config.get("proxy_mount_path") or "").strip()
    if existing and not existing.startswith("/proxy-data/"):
        return False
    mount_path = nas_mount_point(repository, node_id=int(repository.bind_node_id))
    if config.get("proxy_mount_path") == mount_path:
        return False
    config["proxy_mount_path"] = mount_path
    repository.config = config
    return True


def _with_default_kopia_password(config: dict | None) -> dict:
    normalized = {**(config or {})}
    return normalized


def _sanitize_secret_message(message: str, config: dict | None) -> str:
    return str(
        scrub_secrets(
            message, extra_values=[str(v) for v in (config or {}).values() if v]
        )
    )


def _repository_already_exists_app_error(repository: Repository) -> AppError:
    return AppError(
        code=REPOSITORY_ALREADY_EXISTS_CODE,
        status=409,
        retryable=False,
        title="Repository already exists",
        diagnostic=REPOSITORY_ALREADY_EXISTS_MESSAGE,
        meta={"repository_type": repository.repo_type},
    )


def create_credential(
    *,
    organization_id: int,
    credential_type: str,
    secret: str,
    metadata: dict | None = None,
) -> Credential:
    credential = Credential(
        organization_id=organization_id,
        credential_type=credential_type,
        metadata=metadata or {},
    )
    credential.set_secret(secret)
    credential.save()
    return credential


def create_credential_payload(
    *,
    organization_id: int,
    credential_type: str,
    secret_payload: dict,
    metadata: dict | None = None,
) -> Credential:
    credential = Credential(
        organization_id=organization_id,
        credential_type=credential_type,
        metadata=metadata or {},
    )
    credential.set_secret_payload(secret_payload)
    credential.save()
    return credential


def update_credential(
    *,
    credential: Credential,
    secret: str | None = None,
    metadata: dict | None = None,
) -> Credential:
    if secret is not None:
        credential.set_secret(secret)
    if metadata is not None:
        credential.metadata = metadata
    credential.save(update_fields=["secret_cipher", "metadata", "updated_at"])
    return credential


def update_credential_payload(
    *,
    credential: Credential,
    secret_payload: dict | None = None,
    metadata: dict | None = None,
) -> Credential:
    update_fields = ["updated_at"]
    if secret_payload is not None:
        credential.set_secret_payload(secret_payload)
        update_fields.append("secret_cipher")
    if metadata is not None:
        credential.metadata = metadata
        update_fields.append("metadata")
    credential.save(update_fields=update_fields)
    return credential


def delete_credential(*, credential: Credential) -> None:
    credential.delete()


def create_repository(
    *,
    organization_id: int,
    name: str,
    repo_type: str,
    config: dict | None = None,
    s3_platform: str | None = None,
    s3_bucket: str | None = None,
    s3_bucket_mode: str | None = None,
    nas_protocol: str | None = None,
    bind_node_type: str | None = None,
    bind_node_id: int | None = None,
    credential_payload: dict | None = None,
    requested_by=None,
) -> Repository:
    config = _with_default_kopia_password(config)
    secret_payload = build_secret_payload(
        repository_type=repo_type,
        nas_protocol=nas_protocol,
        config=config,
        credential_payload=credential_payload,
    )
    sanitized_config = sanitize_repository_config(config)
    credential_type = _credential_type_for_repository(repo_type, nas_protocol)
    credential_metadata = build_credential_metadata(
        repository_type=repo_type,
        config=sanitized_config,
        credential_payload=credential_payload,
    )
    initial_status = Repository.Status.CREATED
    if (
        repo_type == Repository.Type.S3
        or (repo_type == Repository.Type.NAS and bind_node_type)
        or repo_type == Repository.Type.PROXY_FS
    ):
        initial_status = Repository.Status.CREATING
    initial_health = (
        Repository.Health.UNVERIFIED
        if repo_type == Repository.Type.NAS and not bind_node_type
        else Repository.Health.OFFLINE
    )
    config_dict = sanitized_config if isinstance(sanitized_config, dict) else {}
    capacity_bytes = capacity_bytes_from_config(config_dict)

    from apps.subscription.services.interface import enforce_repository_type_quota
    from apps.iam.models import Organization

    org = Organization.objects.filter(id=organization_id).first()
    try:
        with transaction.atomic():
            if org is not None:
                # Keep the quota check in the same transaction as creation so
                # concurrent repository requests observe one serialized fact.
                enforce_repository_type_quota(organization=org, repo_type=repo_type)
            credential = create_credential_payload(
                organization_id=organization_id,
                credential_type=credential_type,
                secret_payload=secret_payload,
                metadata=credential_metadata,
            )
            repository = Repository.objects.create(
                organization_id=organization_id,
                name=name,
                repo_type=repo_type,
                status=initial_status,
                health=initial_health,
                config=sanitized_config,
                credential_id=credential.id,
                capacity_bytes=capacity_bytes,
                s3_platform=s3_platform or None,
                s3_bucket=s3_bucket or None,
                s3_bucket_mode=s3_bucket_mode or Repository.S3BucketMode.EXISTING,
                nas_protocol=nas_protocol or None,
                bind_node_type=bind_node_type or None,
                bind_node_id=bind_node_id,
            )

            initializes_on_create = repository.repo_type in {
                Repository.Type.S3,
                Repository.Type.PROXY_FS,
            } or (
                repository.repo_type == Repository.Type.NAS
                and bool(repository.bind_node_type)
            )
            if initializes_on_create:
                if repository.repo_type == Repository.Type.NAS:
                    if _set_nas_proxy_mount_path(repository):
                        repository.save(update_fields=["config", "updated_at"])
                    preflight_bound_proxy(repository=repository)
                elif repository.repo_type == Repository.Type.PROXY_FS:
                    if configure_managed_proxy_fs_path(repository):
                        repository.save(update_fields=["config", "updated_at"])
                    preflight_bound_proxy(repository=repository)
                reserve_repository_location(repository)
                enqueue_repository_create_task(
                    repository=repository,
                    requested_by=requested_by,
                )
    except RepositoryLocationConflict as exc:
        raise DRFValidationError({"detail": str(exc.messages[0])}) from exc
    except ValidationError as exc:
        raise DRFValidationError(
            {"detail": _sanitize_secret_message(str(exc), {**config, **secret_payload})}
        ) from exc

    if repository.repo_type == Repository.Type.NAS and not repository.bind_node_type:
        enqueue_repository_usage_refresh(
            organization_id=repository.organization_id,
            repository_ids=[repository.id],
            force=True,
            trigger="storage.repository.create_nas",
        )
    elif repository.repo_type not in {
        Repository.Type.S3,
        Repository.Type.NAS,
        Repository.Type.PROXY_FS,
    }:
        return sync_repository_usage(repository)
    return repository


def update_repository(
    *,
    repository: Repository,
    name: str | None = None,
    config: dict | None = None,
    s3_platform: str | None = None,
    s3_bucket: str | None = None,
    nas_protocol: str | None = None,
    bind_node_type: str | None = None,
    bind_node_id: int | None = None,
    credential_payload: dict | None = None,
    requested_by=None,
) -> Repository:
    _validate_repository_update_status(repository)
    if repository.repo_type == Repository.Type.S3 and credential_payload is not None:
        from apps.storage.services.internal.repository_credential_rotation import (
            enqueue_repository_credential_rotation,
        )

        try:
            repository_task = enqueue_repository_credential_rotation(
                repository=repository,
                name=name,
                config=config,
                credential_payload=credential_payload,
                requested_by=requested_by,
            )
        except ValidationError as exc:
            raise DRFValidationError({"detail": exc.messages}) from exc
        repository.credential_rotation_task = repository_task
        return repository

    with transaction.atomic():
        repository = Repository.objects.select_for_update().get(
            pk=repository.id,
            organization_id=repository.organization_id,
        )
        _validate_repository_update_status(repository)
        if name is not None:
            repository.name = name
        if config is not None:
            # Shallow-merge: keep existing keys that the caller did not pass so
            # credentials and other immutable fields are preserved on partial updates.
            merged = {**(repository.config or {}), **config}
            merged = _with_default_kopia_password(merged)
            if repository.credential_id or credential_payload is not None:
                repository.config = sanitize_repository_config(merged)
            else:
                repository.config = merged
        if s3_platform is not None:
            repository.s3_platform = s3_platform or None
        if s3_bucket is not None:
            repository.s3_bucket = s3_bucket or None
        if nas_protocol is not None:
            repository.nas_protocol = nas_protocol or None
        if bind_node_type is not None:
            repository.bind_node_type = bind_node_type or None
        if bind_node_id is not None:
            repository.bind_node_id = bind_node_id

        apply_capacity_from_config(repository)
        _set_nas_proxy_mount_path(repository)
        previous_credential_id = repository.credential_id
        if credential_payload is not None:
            staged_credential = _stage_repository_credential_rotation(
                repository=repository,
                config={**(repository.config or {}), **(config or {})},
                credential_payload=credential_payload,
            )
            repository.credential_id = staged_credential.id
            _verify_repository_credential_rotation(repository)
        repository.save()
        if (
            credential_payload is not None
            and previous_credential_id
            and previous_credential_id != repository.credential_id
            and not Repository.objects.filter(
                organization_id=repository.organization_id,
                credential_id=previous_credential_id,
            ).exists()
        ):
            Credential.objects.filter(
                id=previous_credential_id,
                organization_id=repository.organization_id,
            ).delete()

    enqueue_repository_usage_refresh(
        organization_id=repository.organization_id,
        repository_ids=[repository.id],
        force=True,
        trigger="storage.repository.update",
    )
    return repository


def _validate_repository_update_status(repository: Repository) -> None:
    if repository.status == Repository.Status.CREATING:
        raise DRFValidationError(
            {"detail": "Repository create or remount is still in progress."}
        )
    if repository.status in {
        Repository.Status.REMOVING,
        Repository.Status.REMOVED,
    }:
        raise DRFValidationError(
            {"detail": "Repository cannot be updated during or after removal."}
        )


def _credential_type_for_repository(
    repo_type: str, nas_protocol: str | None = None
) -> str:
    if repo_type == Repository.Type.S3:
        return Credential.Type.S3
    if repo_type == Repository.Type.NAS and nas_protocol == Repository.NasProtocol.SMB:
        return Credential.Type.SMB
    return Credential.Type.REPO_PASSWORD


def _stage_repository_credential_rotation(
    *,
    repository: Repository,
    config: dict,
    credential_payload: dict,
) -> Credential:
    existing_payload: dict = {}
    if repository.credential_id:
        credential = Credential.objects.filter(
            id=repository.credential_id,
            organization_id=repository.organization_id,
        ).first()
        if credential is not None:
            existing_payload = credential.get_secret_payload()
    secret_payload = build_secret_payload(
        repository_type=repository.repo_type,
        nas_protocol=repository.nas_protocol,
        config=config,
        credential_payload=credential_payload,
        existing_secrets=existing_payload,
    )
    metadata = build_credential_metadata(
        repository_type=repository.repo_type,
        config=repository.config,
        credential_payload=credential_payload,
    )
    credential_type = _credential_type_for_repository(
        repository.repo_type, repository.nas_protocol
    )
    return create_credential_payload(
        organization_id=repository.organization_id,
        credential_type=credential_type,
        secret_payload=secret_payload,
        metadata=metadata,
    )


def _verify_repository_credential_rotation(repository: Repository) -> None:
    """Prove new credentials reach the same owned physical repository."""
    try:
        if repository.repo_type == Repository.Type.S3:
            check_s3_repository(
                repository,
                refresh_namespace=True,
                adopt_legacy_ownership=False,
            )
            return
        if repository.repo_type == Repository.Type.NAS:
            if repository.bind_node_id:
                check_proxy_nas_repository(
                    repository,
                    health_only=True,
                    adopt_legacy_ownership=False,
                )
            else:
                health = probe_unbound_nas_repository_health(
                    repository,
                    adopt_legacy_ownership=False,
                )
                if health != Repository.Health.ONLINE:
                    raise ValidationError(
                        "No active repository location accepted the new NAS credentials."
                    )
            return
        if repository.repo_type == Repository.Type.PROXY_FS:
            check_proxy_fs_repository(
                repository,
                health_only=True,
                adopt_legacy_ownership=False,
            )
            return
    except (
        NASRepositoryError,
        ProxyFSRepositoryError,
        RepositoryInitializationError,
        ValidationError,
    ) as exc:
        raise DRFValidationError(
            {
                "credential_payload": (
                    "The new credentials could not open the existing owned repository."
                )
            }
        ) from exc
    raise DRFValidationError({"credential_payload": "Unsupported repository type."})


def delete_repository(*, repository: Repository) -> None:
    backup_config_model = apps.get_model("protection", "BackupConfig")
    backup_source_snapshot_model = apps.get_model("protection", "BackupSourceSnapshot")
    if backup_config_model.objects.filter(
        organization_id=repository.organization_id,
        repository_id=repository.id,
    ).exists():
        raise ValidationError("Repository has backup configs and cannot be deleted.")
    if backup_source_snapshot_model.objects.filter(
        organization_id=repository.organization_id,
        repository_id=repository.id,
    ).exists():
        raise ValidationError("Repository has backup snapshots and cannot be deleted.")

    with transaction.atomic():
        repository_operation_tasks = Task.objects.filter(
            organization_id=repository.organization_id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            repository_operation__repository_id=repository.id,
        )
        if repository_operation_tasks.filter(
            status__in=[Task.Status.PENDING, Task.Status.RUNNING],
        ).exists():
            raise ValidationError(
                "Repository has maintenance tasks in progress and cannot be deleted."
            )

        # A RepositoryTask protects its execution target from deletion. Remove
        # terminal operation tasks first so their metadata, resources, steps,
        # and events are deleted together before the repository cascades to its
        # execution targets.
        repository_operation_tasks.delete()

        credential_id = repository.credential_id
        organization_id = repository.organization_id
        repository.delete()
        if (
            credential_id
            and not Repository.objects.filter(
                organization_id=organization_id,
                credential_id=credential_id,
            ).exists()
        ):
            Credential.objects.filter(
                organization_id=organization_id,
                id=credential_id,
            ).delete()


def check_repository(*, repository: Repository) -> Repository:
    if repository.repo_type == Repository.Type.S3:
        try:
            check_s3_repository(repository)
        except RepositoryInitializationError as exc:
            if is_repository_ownership_failure(exc):
                invalidate_repository_location_ownership(repository)
            repository.health = Repository.Health.OFFLINE
            repository.last_checked_at = timezone.now()
            repository.save(update_fields=["health", "last_checked_at", "updated_at"])
            raise DRFValidationError(
                {"detail": str(exc), "repository_id": repository.id}
            ) from exc
        repository.health = Repository.Health.ONLINE
    elif repository.repo_type == Repository.Type.NAS:
        if not repository.bind_node_type:
            repository.health = probe_unbound_nas_repository_health(repository)
        else:
            try:
                check_proxy_nas_repository(repository)
            except (NASRepositoryError, ValidationError) as exc:
                if is_repository_ownership_failure(exc):
                    invalidate_repository_location_ownership(repository)
                repository.health = Repository.Health.OFFLINE
                repository.last_checked_at = timezone.now()
                repository.save(
                    update_fields=["health", "last_checked_at", "updated_at"]
                )
                raise DRFValidationError(
                    {
                        "detail": _sanitize_secret_message(str(exc), repository.config),
                        "repository_id": repository.id,
                    }
                ) from exc
            repository.health = Repository.Health.ONLINE
    elif repository.repo_type == Repository.Type.PROXY_FS:
        try:
            check_proxy_fs_repository(repository)
        except (ProxyFSRepositoryError, ValidationError) as exc:
            if is_repository_ownership_failure(exc):
                invalidate_repository_location_ownership(repository)
            repository.health = Repository.Health.OFFLINE
            repository.last_checked_at = timezone.now()
            repository.save(update_fields=["health", "last_checked_at", "updated_at"])
            raise DRFValidationError(
                {
                    "detail": _sanitize_secret_message(str(exc), repository.config),
                    "repository_id": repository.id,
                }
            ) from exc
        repository.health = Repository.Health.ONLINE
    repository.last_checked_at = timezone.now()
    repository.save(update_fields=["health", "last_checked_at", "updated_at"])
    return sync_repository_usage(repository)
