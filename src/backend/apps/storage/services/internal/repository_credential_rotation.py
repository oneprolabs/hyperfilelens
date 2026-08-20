"""Durable S3 credential verification and atomic activation."""

from __future__ import annotations

from copy import copy
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.storage.repositories.models import (
    Credential,
    Repository,
    RepositoryExecutionTarget,
    RepositoryTask,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
    check_s3_repository,
)
from apps.storage.services.internal.repository_execution_lock import (
    repository_execution_lock,
)
from apps.storage.services.internal.repository_secrets import (
    SECRET_CONFIG_FIELDS,
    build_credential_metadata,
    build_secret_payload,
    resolve_repository_secrets,
    sanitize_repository_config,
    scrub_secrets,
    secret_values_for_scrub,
)
from apps.storage.services.internal.repository_usage import (
    enqueue_repository_usage_refresh,
)
from apps.storage.services.internal.s3_validation_errors import (
    classify_s3_validation_error,
)
from apps.task.models import Task, TaskResource, TaskStep
from apps.task.services.interface import complete_task, create_task, start_task


ROTATION_STEPS = (
    "prepare_candidate_credentials",
    "verify_storage_access",
    "verify_repository_identity",
    "activate_credentials",
)
_ACTIVE_STATUSES = (Task.Status.PENDING, Task.Status.RUNNING)
_ROTATION_METADATA_KEY = "repository_credential_rotation"


def enqueue_repository_credential_rotation(
    *,
    repository: Repository,
    name: str | None,
    config: dict | None,
    credential_payload: dict,
    requested_by=None,
) -> RepositoryTask:
    """Stage encrypted credentials and enqueue deep verification."""
    if repository.repo_type != Repository.Type.S3:
        raise ValidationError("Credential rotation is only supported for S3.")

    with transaction.atomic():
        locked = Repository.objects.select_for_update().get(
            pk=repository.id,
            organization_id=repository.organization_id,
        )
        conflicting = (
            RepositoryTask.objects.filter(
                repository=locked,
                task__status__in=_ACTIVE_STATUSES,
            )
            .select_related("task")
            .order_by("created_at", "id")
            .first()
        )
        if conflicting is not None:
            raise ValidationError(
                "Repository already has an active operation. Wait for it to finish."
            )
        if locked.status != Repository.Status.CREATED:
            raise ValidationError(
                "Only a created repository can update its credentials."
            )

        incoming_config = dict(config or {})
        merged_config = {**(locked.config or {}), **incoming_config}
        candidate_credential_payload = dict(credential_payload or {})
        for key in SECRET_CONFIG_FIELDS:
            if key not in candidate_credential_payload and key in incoming_config:
                candidate_credential_payload[key] = incoming_config[key]
        desired_config = sanitize_repository_config(merged_config)
        candidate = _create_candidate_credential(
            repository=locked,
            desired_name=name if name is not None else locked.name,
            desired_config=desired_config,
            credential_payload=candidate_credential_payload,
        )
        task = create_task(
            organization_id=locked.organization_id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name=f"Update Credentials · {locked.name}",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "repository_id": locked.id,
                "operation_type": RepositoryTask.OperationType.CREDENTIAL_ROTATE,
                "candidate_credential_id": candidate.id,
            },
            resources=[
                {
                    "resource_type": TaskResource.Type.REPOSITORY,
                    "resource_id": locked.id,
                    "is_primary": True,
                }
            ],
            steps=list(ROTATION_STEPS),
        )
        repository_task = RepositoryTask.objects.create(
            task=task,
            repository=locked,
            operation_type=RepositoryTask.OperationType.CREDENTIAL_ROTATE,
            owner_type=RepositoryExecutionTarget.OwnerType.CONTROLLER,
            owner_identity="hfl-credential-rotation@worker",
            requested_by_id=getattr(requested_by, "id", None),
        )
        transaction.on_commit(
            lambda: _dispatch_repository_operation(repository_task.id)
        )
        return repository_task


def run_repository_credential_rotation_task(
    *,
    repository_task_id: int,
) -> dict[str, Any]:
    """Verify a candidate credential and atomically make it authoritative."""
    with repository_execution_lock(
        operation="repository-credential-rotation",
        operation_id=repository_task_id,
    ) as acquired:
        if not acquired:
            return {
                "status": "locked",
                "repository_task_id": repository_task_id,
                "idempotent": True,
            }
        return _run_repository_credential_rotation_task_locked(
            repository_task_id=repository_task_id
        )


def _run_repository_credential_rotation_task_locked(
    *,
    repository_task_id: int,
) -> dict[str, Any]:
    repository_task = RepositoryTask.objects.select_related("task", "repository").get(
        pk=repository_task_id
    )
    task = repository_task.task
    if repository_task.operation_type != RepositoryTask.OperationType.CREDENTIAL_ROTATE:
        raise ValidationError("Repository task is not a credential rotation.")
    if task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return task.result_payload or {"status": task.status}
    if task.status == Task.Status.PENDING:
        start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)

    repository = repository_task.repository
    candidate_id = int((task.request_payload or {}).get("candidate_credential_id") or 0)
    candidate = Credential.objects.filter(
        id=candidate_id,
        organization_id=repository.organization_id,
        credential_type=Credential.Type.S3,
    ).first()
    if repository.credential_id == candidate_id and candidate is not None:
        return _complete_rotation_success(repository_task)
    if candidate is None:
        return _complete_rotation_failure(
            repository_task,
            error_code="REPOSITORY_CREDENTIAL_CANDIDATE_MISSING",
            message="Candidate repository credentials are unavailable.",
        )

    secrets_payload: dict = {}
    try:
        secrets_payload = candidate.get_secret_payload()
        metadata = _rotation_metadata(candidate, repository=repository)
        _set_step(
            task,
            "prepare_candidate_credentials",
            TaskStep.Status.SUCCESS,
            20,
        )
        candidate_repository = copy(repository)
        candidate_repository.name = metadata["name"]
        candidate_repository.config = metadata["config"]
        candidate_repository.credential_id = candidate.id

        _set_step(task, "verify_storage_access", TaskStep.Status.RUNNING, 35)
        check_s3_repository(
            candidate_repository,
            # The existing physical ownership marker is the authority for a
            # credential rotation. Do not mutate the database namespace with
            # candidate credentials before that proof succeeds.
            refresh_namespace=False,
            adopt_legacy_ownership=False,
        )
        _set_step(task, "verify_storage_access", TaskStep.Status.SUCCESS, 65)
        _set_step(
            task,
            "verify_repository_identity",
            TaskStep.Status.SUCCESS,
            80,
        )
        _set_step(task, "activate_credentials", TaskStep.Status.RUNNING, 90)
        with transaction.atomic():
            locked = Repository.objects.select_for_update().get(
                pk=repository.id,
                organization_id=repository.organization_id,
            )
            expected_credential_id = metadata["expected_credential_id"]
            if locked.credential_id == candidate.id:
                pass
            elif locked.credential_id != expected_credential_id:
                raise ValidationError(
                    "Repository credentials changed while verification was running."
                )
            elif (
                locked.name != metadata["expected_name"]
                or sanitize_repository_config(locked.config)
                != metadata["expected_config"]
            ):
                raise ValidationError(
                    "Repository settings changed while verification was running. "
                    "Review the latest settings and try again."
                )
            else:
                previous_credential_id = locked.credential_id
                locked.name = metadata["name"]
                locked.config = metadata["config"]
                locked.credential_id = candidate.id
                locked.save(
                    update_fields=[
                        "name",
                        "config",
                        "credential_id",
                        "updated_at",
                    ]
                )
                candidate_metadata = (
                    dict(candidate.metadata)
                    if isinstance(candidate.metadata, dict)
                    else {}
                )
                candidate_metadata.pop(_ROTATION_METADATA_KEY, None)
                Credential.objects.filter(
                    id=candidate.id,
                    organization_id=locked.organization_id,
                ).update(metadata=candidate_metadata)
                _delete_unused_credential(
                    organization_id=locked.organization_id,
                    credential_id=previous_credential_id,
                )
    except Exception as exc:
        if isinstance(exc, RepositoryInitializationError):
            failure = classify_s3_validation_error(exc, operation="bucket_access")
            error_code = failure.code
            message = failure.message
        else:
            error_code = "REPOSITORY_CREDENTIAL_ROTATION_FAILED"
            message = str(exc)
        safe_message = str(
            scrub_secrets(
                message,
                extra_values=secret_values_for_scrub(
                    repository,
                    secrets_payload,
                ),
            )
        )
        live_credential_id = (
            Repository.objects.filter(
                pk=repository.id,
                organization_id=repository.organization_id,
            )
            .values_list("credential_id", flat=True)
            .first()
        )
        if live_credential_id != candidate.id:
            candidate.delete()
        return _complete_rotation_failure(
            repository_task,
            error_code=error_code,
            message=safe_message,
        )
    # Credential activation is already authoritative. If task finalization is
    # interrupted, leave the candidate intact so the durable retry path can
    # observe the live credential and converge to success.
    _set_step(task, "activate_credentials", TaskStep.Status.SUCCESS, 100)
    return _complete_rotation_success(repository_task)


def _create_candidate_credential(
    *,
    repository: Repository,
    desired_name: str,
    desired_config: dict,
    credential_payload: dict,
) -> Credential:
    existing_secrets = resolve_repository_secrets(repository)
    secret_payload = build_secret_payload(
        repository_type=repository.repo_type,
        config=desired_config,
        credential_payload=credential_payload,
        existing_secrets=existing_secrets,
    )
    metadata = build_credential_metadata(
        repository_type=repository.repo_type,
        config=desired_config,
        credential_payload=credential_payload,
    )
    metadata[_ROTATION_METADATA_KEY] = {
        "repository_id": repository.id,
        "expected_credential_id": repository.credential_id,
        "expected_name": repository.name,
        "expected_config": sanitize_repository_config(repository.config),
        "name": str(desired_name).strip(),
        "config": desired_config,
    }
    candidate = Credential(
        organization_id=repository.organization_id,
        credential_type=Credential.Type.S3,
        metadata=metadata,
    )
    candidate.set_secret_payload(secret_payload)
    candidate.save()
    return candidate


def _rotation_metadata(
    candidate: Credential,
    *,
    repository: Repository,
) -> dict[str, Any]:
    metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
    rotation = metadata.get(_ROTATION_METADATA_KEY)
    if not isinstance(rotation, dict):
        raise ValidationError("Candidate repository credentials are invalid.")
    if int(rotation.get("repository_id") or 0) != repository.id:
        raise ValidationError("Candidate credentials belong to another repository.")
    config = rotation.get("config")
    expected_config = rotation.get("expected_config")
    if not isinstance(config, dict) or not isinstance(expected_config, dict):
        raise ValidationError("Candidate repository configuration is invalid.")
    return {
        "expected_credential_id": rotation.get("expected_credential_id"),
        "expected_name": str(rotation.get("expected_name") or repository.name),
        "expected_config": sanitize_repository_config(expected_config),
        "name": str(rotation.get("name") or repository.name).strip(),
        "config": sanitize_repository_config(config),
    }


def _complete_rotation_success(repository_task: RepositoryTask) -> dict[str, Any]:
    repository_task.repository.refresh_from_db()
    _converge_rotation_steps_success(repository_task.task)
    complete_task(
        task_uuid=repository_task.task.task_uuid,
        organization_id=repository_task.task.organization_id,
        status=Task.Status.SUCCESS,
        result_payload={
            "status": "success",
            "repository_id": repository_task.repository_id,
            "credential_updated": True,
        },
    )
    enqueue_repository_usage_refresh(
        organization_id=repository_task.repository.organization_id,
        repository_ids=[repository_task.repository_id],
        force=True,
        trigger="storage.repository.credential_rotation",
    )
    return {"status": "success", "repository_task_id": repository_task.id}


def _converge_rotation_steps_success(task: Task) -> None:
    """Make recovery and normal completion expose one terminal step state."""
    TaskStep.objects.filter(
        task=task,
        step_name__in=ROTATION_STEPS,
    ).exclude(status=TaskStep.Status.SUCCESS).update(
        status=TaskStep.Status.SUCCESS,
        progress=100,
    )
    task.current_step = ROTATION_STEPS[-1]
    task.progress = 100
    task.save(update_fields=["current_step", "progress", "updated_at"])


def _complete_rotation_failure(
    repository_task: RepositoryTask,
    *,
    error_code: str,
    message: str,
) -> dict[str, Any]:
    task = repository_task.task
    current_step = task.current_step or ROTATION_STEPS[0]
    _set_step(task, current_step, TaskStep.Status.FAILED, int(task.progress or 0))
    complete_task(
        task_uuid=task.task_uuid,
        organization_id=task.organization_id,
        status=Task.Status.FAILED,
        progress=int(task.progress or 0),
        error_code=error_code,
        error_message=message,
    )
    return {
        "status": "failed",
        "repository_task_id": repository_task.id,
        "error_code": error_code,
        "error": message,
    }


def _delete_unused_credential(
    *,
    organization_id: int,
    credential_id: int | None,
) -> None:
    if not credential_id:
        return
    if Repository.objects.filter(
        organization_id=organization_id,
        credential_id=credential_id,
    ).exists():
        return
    Credential.objects.filter(
        organization_id=organization_id,
        id=credential_id,
    ).delete()


def _set_step(task: Task, step_name: str, status: str, progress: int) -> None:
    from apps.storage.services.internal.repository_operations import set_task_step

    set_task_step(task, step_name, status=status, progress=progress)


def _dispatch_repository_operation(repository_task_id: int) -> None:
    from apps.storage.tasks import execute_repository_operation

    execute_repository_operation.apply_async(
        kwargs={"repository_task_id": repository_task_id}
    )


__all__ = [
    "enqueue_repository_credential_rotation",
    "run_repository_credential_rotation_task",
]
