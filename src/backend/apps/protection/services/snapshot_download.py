from __future__ import annotations

import hashlib
import logging
import os
import threading
import zipfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.text import get_valid_filename

from apps.protection.models import BackupSourceSnapshotDirectory, SnapshotDownloadArtifact
from apps.protection.services.snapshot_browser import (
    SnapshotArtifactUploadUnsupported,
    SnapshotFileDownload,
    SnapshotBrowserError,
    SnapshotBrowserForbidden,
    _clean_relative_path,
    _get_directory,
    download_snapshot_file,
)
from apps.task.models import Task, TaskResource, TaskStep
from apps.task.services.interface import append_task_step_event, complete_task, create_task, start_task

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_DOWNLOAD_EXPIRES_HOURS = 24
DEFAULT_SNAPSHOT_DOWNLOAD_MAX_BYTES = 200 * 1024 * 1024
DEFAULT_SNAPSHOT_DOWNLOAD_TIMEOUT_SECONDS = 15 * 60
DEFAULT_SNAPSHOT_BATCH_DOWNLOAD_MAX_PATHS = 100
SNAPSHOT_DOWNLOAD_UPLOAD_PATH = "/api/v1/protection/snapshot-download-artifacts/{artifact_id}/content/"
SNAPSHOT_DOWNLOAD_UPLOAD_MAX_AGE_SECONDS = 60 * 60
_SNAPSHOT_DOWNLOAD_UPLOAD_SIGNING_SALT = "protection-snapshot-download-upload"
SNAPSHOT_DOWNLOAD_FILE_TOKEN_MAX_AGE_SECONDS = 5 * 60
_SNAPSHOT_DOWNLOAD_FILE_SIGNING_SALT = "protection-snapshot-download-file"


class SnapshotArtifactUploadError(ValueError):
    """Raised when an Agent artifact upload cannot be accepted safely."""


def _artifact_root() -> Path:
    """Return the runtime directory for generated snapshot downloads."""
    return Path(settings.MEDIA_ROOT) / "snapshot-downloads"


def _safe_filename(value: str, fallback: str = "download") -> str:
    filename = get_valid_filename(str(value or "").strip())
    return filename or fallback


def _expires_at():
    return timezone.now() + timedelta(hours=DEFAULT_SNAPSHOT_DOWNLOAD_EXPIRES_HOURS)


def _max_download_bytes() -> int:
    raw = getattr(settings, "PROTECTION_SNAPSHOT_DOWNLOAD_MAX_BYTES", DEFAULT_SNAPSHOT_DOWNLOAD_MAX_BYTES)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_SNAPSHOT_DOWNLOAD_MAX_BYTES


def _download_timeout_seconds() -> int:
    raw = getattr(
        settings,
        "PROTECTION_SNAPSHOT_DOWNLOAD_TIMEOUT_SECONDS",
        DEFAULT_SNAPSHOT_DOWNLOAD_TIMEOUT_SECONDS,
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_SNAPSHOT_DOWNLOAD_TIMEOUT_SECONDS


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _artifact_storage_path(*, task: Task, filename: str) -> Path:
    return _artifact_root() / str(task.organization_id) / str(task.task_uuid) / filename


def _create_pending_artifact(
    *,
    task: Task,
    directory_id: int,
    relative_path: str,
    filename: str,
) -> SnapshotDownloadArtifact:
    safe_filename = _safe_filename(filename, "snapshot-download")
    return SnapshotDownloadArtifact.objects.create(
        task=task,
        organization_id=task.organization_id,
        source_snapshot_directory_id=directory_id,
        relative_path=relative_path,
        filename=safe_filename,
        content_type="application/octet-stream",
        size_bytes=0,
        storage_path=str(_artifact_storage_path(task=task, filename=safe_filename)),
        status=SnapshotDownloadArtifact.Status.UPLOADING,
        expires_at=_expires_at(),
    )


def prepare_snapshot_artifact_upload(
    *, artifact: SnapshotDownloadArtifact, node_id: int
) -> dict[str, Any]:
    if artifact.status != SnapshotDownloadArtifact.Status.UPLOADING:
        raise SnapshotArtifactUploadError("snapshot download artifact is not accepting uploads")
    token = signing.dumps(
        {
            "artifact_id": int(artifact.id),
            "task_id": int(artifact.task_id),
            "organization_id": int(artifact.organization_id),
            "node_id": int(node_id),
            "nonce": str(uuid4()),
        },
        salt=_SNAPSHOT_DOWNLOAD_UPLOAD_SIGNING_SALT,
        compress=True,
    )
    artifact.upload_token_digest = _token_digest(token)
    artifact.save(update_fields=["upload_token_digest", "updated_at"])
    return {
        "artifact_id": int(artifact.id),
        "path": SNAPSHOT_DOWNLOAD_UPLOAD_PATH.format(artifact_id=artifact.id),
        "token": token,
        "max_bytes": _max_download_bytes(),
    }


def _validated_upload_token(token: str) -> dict[str, Any]:
    try:
        payload = signing.loads(
            token,
            salt=_SNAPSHOT_DOWNLOAD_UPLOAD_SIGNING_SALT,
            max_age=SNAPSHOT_DOWNLOAD_UPLOAD_MAX_AGE_SECONDS,
        )
    except signing.SignatureExpired as exc:
        raise SnapshotArtifactUploadError("snapshot download upload token expired") from exc
    except signing.BadSignature as exc:
        raise SnapshotArtifactUploadError("invalid snapshot download upload token") from exc
    if not isinstance(payload, dict):
        raise SnapshotArtifactUploadError("invalid snapshot download upload token payload")
    return payload


def create_snapshot_artifact_file_token(
    *, artifact: SnapshotDownloadArtifact, user_id: int
) -> str:
    return signing.dumps(
        {
            "artifact_id": int(artifact.id),
            "organization_id": int(artifact.organization_id),
            "user_id": int(user_id),
        },
        salt=_SNAPSHOT_DOWNLOAD_FILE_SIGNING_SALT,
        compress=True,
    )


def validate_snapshot_artifact_file_token(
    *, token: str, artifact_id: int, user_id: int
) -> int:
    try:
        payload = signing.loads(
            token,
            salt=_SNAPSHOT_DOWNLOAD_FILE_SIGNING_SALT,
            max_age=SNAPSHOT_DOWNLOAD_FILE_TOKEN_MAX_AGE_SECONDS,
        )
    except (signing.BadSignature, signing.SignatureExpired) as exc:
        raise SnapshotArtifactUploadError("snapshot download link is invalid or expired") from exc
    if not isinstance(payload, dict) or (
        int(payload.get("artifact_id") or 0) != int(artifact_id)
        or int(payload.get("user_id") or 0) != int(user_id)
    ):
        raise SnapshotArtifactUploadError("snapshot download link does not match this request")
    return int(payload.get("organization_id") or 0)


def accept_snapshot_artifact_upload(
    *,
    artifact_id: int,
    token: str,
    stream,
    content_length: int,
    content_type: str,
    expected_sha256: str,
    filename: str,
) -> SnapshotDownloadArtifact:
    clean_token = str(token or "").strip()
    signed = _validated_upload_token(clean_token)
    if int(signed.get("artifact_id") or 0) != int(artifact_id):
        raise SnapshotArtifactUploadError("upload token does not match the artifact")
    try:
        content_length = int(content_length)
    except (TypeError, ValueError) as exc:
        raise SnapshotArtifactUploadError("Content-Length is required") from exc
    if content_length < 0 or content_length > _max_download_bytes():
        raise SnapshotArtifactUploadError("snapshot download exceeds the configured size limit")
    expected_sha256 = str(expected_sha256 or "").strip().lower()
    if len(expected_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha256):
        raise SnapshotArtifactUploadError("X-Content-SHA256 is required")

    artifact = SnapshotDownloadArtifact.objects.select_related("task").filter(id=artifact_id).first()
    if artifact is None:
        raise SnapshotArtifactUploadError("snapshot download artifact was not found")
    if (
        int(signed.get("task_id") or 0) != int(artifact.task_id)
        or int(signed.get("organization_id") or 0) != int(artifact.organization_id)
    ):
        raise SnapshotArtifactUploadError("upload token does not match the download task")
    if artifact.status == SnapshotDownloadArtifact.Status.READY:
        if artifact.sha256 == expected_sha256 and artifact.size_bytes == content_length:
            return artifact
        raise SnapshotArtifactUploadError("snapshot download artifact was already finalized")
    if not constant_time_compare(artifact.upload_token_digest, _token_digest(clean_token)):
        raise SnapshotArtifactUploadError("upload token does not match the download task")
    if artifact.status != SnapshotDownloadArtifact.Status.UPLOADING:
        raise SnapshotArtifactUploadError("snapshot download artifact is not accepting uploads")
    if artifact.expires_at <= timezone.now():
        raise SnapshotArtifactUploadError("snapshot download artifact expired")
    if artifact.task.status != Task.Status.RUNNING:
        raise SnapshotArtifactUploadError("snapshot download task is no longer accepting uploads")

    initial_path = Path(artifact.storage_path)
    initial_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    safe_filename = _safe_filename(filename, artifact.filename)
    final_path = _artifact_storage_path(task=artifact.task, filename=safe_filename)
    temporary_path = initial_path.with_name(f".{initial_path.name}.{uuid4().hex}.part")
    digest = hashlib.sha256()
    written = 0
    try:
        with open(temporary_path, "xb") as output:
            os.chmod(temporary_path, 0o600)
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > content_length or written > _max_download_bytes():
                    raise SnapshotArtifactUploadError("snapshot download exceeds the declared size")
                output.write(chunk)
                digest.update(chunk)
        if written != content_length:
            raise SnapshotArtifactUploadError("snapshot download upload was incomplete")
        if not constant_time_compare(digest.hexdigest(), expected_sha256):
            raise SnapshotArtifactUploadError("snapshot download checksum mismatch")

        with transaction.atomic():
            locked_task = Task.objects.select_for_update().get(id=artifact.task_id)
            if locked_task.status != Task.Status.RUNNING:
                raise SnapshotArtifactUploadError("snapshot download task is no longer accepting uploads")
            locked = SnapshotDownloadArtifact.objects.select_for_update().get(id=artifact.id)
            if locked.status != SnapshotDownloadArtifact.Status.UPLOADING:
                raise SnapshotArtifactUploadError("snapshot download artifact is not accepting uploads")
            if not constant_time_compare(locked.upload_token_digest, _token_digest(clean_token)):
                raise SnapshotArtifactUploadError("snapshot download upload token was already replaced")
            os.replace(temporary_path, final_path)
            locked.status = SnapshotDownloadArtifact.Status.READY
            locked.filename = safe_filename
            locked.content_type = str(content_type or "application/octet-stream")[:120]
            locked.size_bytes = written
            locked.sha256 = expected_sha256
            locked.storage_path = str(final_path)
            locked.upload_token_digest = ""
            locked.save(
                update_fields=[
                    "status",
                    "filename",
                    "content_type",
                    "size_bytes",
                    "sha256",
                    "storage_path",
                    "upload_token_digest",
                    "updated_at",
                ]
            )
            return locked
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def create_snapshot_download_task(
    *,
    organization_id: int,
    directory_id: int,
    path: str,
    trigger_type: str = Task.TriggerType.MANUAL,
) -> Task:
    clean_path = _clean_relative_path(path)
    directory = _get_directory(organization_id=organization_id, directory_id=directory_id)
    if not clean_path and directory.path_type != BackupSourceSnapshotDirectory.PathType.FILE:
        raise ValidationError({"path": "Download path is required."})
    source_snapshot = directory.source_snapshot
    display_path = clean_path or directory.source_path
    task = create_task(
        organization_id=organization_id,
        task_type=Task.Type.SNAPSHOT_DOWNLOAD,
        display_name=f"Download snapshot path {display_path}",
        trigger_type=trigger_type,
        request_payload={
            "source_snapshot_directory_id": directory.id,
            "source_snapshot_id": directory.source_snapshot_id,
            "source_snapshot_uid": source_snapshot.snapshot_uid,
            "repository_id": directory.repository_id,
            "kopia_snapshot_id": directory.kopia_snapshot_id,
            "source_path": directory.source_path,
            "path": clean_path,
        },
        resources=[
            {
                "resource_type": TaskResource.Type.BACKUP_SOURCE,
                "resource_subtype": source_snapshot.source_type,
                "resource_id": source_snapshot.source_ref_id,
                "is_primary": True,
            },
        ],
        steps=["snapshot_download_restore", "snapshot_download_transfer", "snapshot_download_finalize"],
    )
    filename = _safe_filename(
        str(display_path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1],
        "snapshot-download",
    )
    _create_pending_artifact(
        task=task,
        directory_id=directory.id,
        relative_path=clean_path,
        filename=filename,
    )
    _queue_snapshot_download_execution(task_id=task.id)
    return task


def _dedupe_clean_paths(paths: list[str]) -> list[str]:
    clean_paths: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        clean_path = _clean_relative_path(str(raw or ""))
        if not clean_path:
            raise ValidationError({"paths": "Download path is required."})
        if clean_path in seen:
            continue
        clean_paths.append(clean_path)
        seen.add(clean_path)
    return clean_paths


def _has_path_conflict(paths: list[str]) -> bool:
    ordered = sorted(paths)
    for index, current in enumerate(ordered):
        for candidate in ordered[index + 1 :]:
            if not candidate.startswith(f"{current}/"):
                break
            return True
    return False


def create_snapshot_batch_download_task(
    *,
    organization_id: int,
    directory_id: int,
    paths: list[str],
    trigger_type: str = Task.TriggerType.MANUAL,
) -> Task:
    if not paths:
        raise ValidationError({"paths": "At least one download path is required."})
    clean_paths = _dedupe_clean_paths(paths)
    if len(clean_paths) > DEFAULT_SNAPSHOT_BATCH_DOWNLOAD_MAX_PATHS:
        raise ValidationError({"paths": f"At most {DEFAULT_SNAPSHOT_BATCH_DOWNLOAD_MAX_PATHS} paths can be downloaded."})
    if _has_path_conflict(clean_paths):
        raise ValidationError({"paths": "Parent and child paths cannot be downloaded together."})
    directory = _get_directory(organization_id=organization_id, directory_id=directory_id)
    source_snapshot = directory.source_snapshot
    task = create_task(
        organization_id=organization_id,
        task_type=Task.Type.SNAPSHOT_DOWNLOAD,
        display_name=f"Download {len(clean_paths)} snapshot paths",
        trigger_type=trigger_type,
        request_payload={
            "source_snapshot_directory_id": directory.id,
            "source_snapshot_id": directory.source_snapshot_id,
            "source_snapshot_uid": source_snapshot.snapshot_uid,
            "repository_id": directory.repository_id,
            "kopia_snapshot_id": directory.kopia_snapshot_id,
            "source_path": directory.source_path,
            "paths": clean_paths,
        },
        resources=[
            {
                "resource_type": TaskResource.Type.BACKUP_SOURCE,
                "resource_subtype": source_snapshot.source_type,
                "resource_id": source_snapshot.source_ref_id,
                "is_primary": True,
            },
        ],
        steps=["snapshot_download_restore", "snapshot_download_transfer", "snapshot_download_finalize"],
    )
    _create_pending_artifact(
        task=task,
        directory_id=directory.id,
        relative_path=",".join(clean_paths),
        filename=f"snapshot-download-{task.id}.zip",
    )
    _queue_snapshot_download_execution(task_id=task.id)
    return task


def _set_download_step_status(
    *,
    task: Task,
    step_name: str,
    status: str,
    progress: int | float,
    task_progress: int | float | None = None,
    current_step: str | None = None,
) -> None:
    legacy_names = {
        "snapshot_download_restore": "restore",
        "snapshot_download_transfer": "transfer",
        "snapshot_download_finalize": "finalize",
    }
    names = [step_name]
    legacy = legacy_names.get(step_name)
    if legacy:
        names.append(legacy)
    step = TaskStep.objects.filter(task=task, step_name__in=names).order_by("step_index").first()
    if step is not None:
        step.status = status
        step.progress = progress
        step.save(update_fields=["status", "progress"])
    updates: list[str] = []
    if current_step is not None:
        task.current_step = current_step
        updates.append("current_step")
    if task_progress is not None:
        task.progress = task_progress
        updates.append("progress")
    if updates:
        updates.append("updated_at")
        task.save(update_fields=updates)


def _failed_download_progress(step_name: str) -> int:
    if step_name in {"snapshot_download_transfer", "transfer"}:
        return 70
    if step_name in {"snapshot_download_finalize", "finalize"}:
        return 90
    return 10


def _queue_snapshot_download_execution(*, task_id: int) -> None:
    try:
        from apps.protection.tasks.snapshot_download import execute_snapshot_download_task

        execute_snapshot_download_task.delay(task_id=task_id)
        return
    except Exception:
        logger.exception("failed to queue snapshot download celery task; falling back to local thread")
    thread = threading.Thread(
        target=_run_snapshot_download_in_thread,
        kwargs={"task_id": task_id},
        daemon=True,
    )
    thread.start()


def _run_snapshot_download_in_thread(*, task_id: int) -> None:
    close_old_connections()
    try:
        task = Task.objects.get(id=task_id)
        run_snapshot_download_task(task=task)
    except Exception:  # pragma: no cover - defensive logging for background thread
        logger.exception("snapshot download task failed before finalization", extra={"task_id": task_id})
    finally:
        close_old_connections()


def run_snapshot_download_task(*, task: Task) -> dict[str, Any]:
    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    directory_id = int(payload.get("source_snapshot_directory_id") or 0)
    path = str(payload.get("path") or "")
    paths = payload.get("paths")
    artifact = SnapshotDownloadArtifact.objects.filter(task=task).first()
    try:
        task = start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)
        directory = _get_directory(organization_id=task.organization_id, directory_id=directory_id)
        kopia_snapshot_id = str(payload.get("kopia_snapshot_id") or directory.kopia_snapshot_id or "").strip()
        selected_names = (
            [str(item or "").strip() for item in paths if str(item or "").strip()]
            if isinstance(paths, list) and paths
            else [path or str(payload.get("source_path") or directory.source_path or "").strip()]
        )
        selected_names = [item for item in selected_names if item]
        _set_download_step_status(
            task=task,
            step_name="snapshot_download_restore",
            status=TaskStep.Status.RUNNING,
            progress=10,
            task_progress=10,
            current_step="snapshot_download_restore",
        )
        append_task_step_event(
            task=task,
            step_name="snapshot_download_restore",
            message="Starting snapshot download",
            metadata={
                "directory_id": directory_id,
                "path": path,
                "paths": paths if isinstance(paths, list) else None,
                "kopia_snapshot_id": kopia_snapshot_id,
                "object_id": kopia_snapshot_id,
                "object_names": selected_names,
            },
        )
        if artifact is None:
            raise SnapshotBrowserError("Snapshot download artifact was not prepared.")
        if isinstance(paths, list) and paths:
            try:
                download = download_snapshot_file(
                    organization_id=task.organization_id,
                    directory_id=directory_id,
                    path="",
                    paths=[str(item or "") for item in paths],
                    upload_artifact=artifact,
                    wait_timeout_seconds=_download_timeout_seconds(),
                )
            except SnapshotArtifactUploadUnsupported:
                download = _download_batch_as_zip(
                    organization_id=task.organization_id,
                    directory_id=directory_id,
                    paths=[str(item or "") for item in paths],
                    task_id=task.id,
                )
        else:
            download = download_snapshot_file(
                organization_id=task.organization_id,
                directory_id=directory_id,
                path=path,
                upload_artifact=artifact,
                wait_timeout_seconds=_download_timeout_seconds(),
            )
        if len(download.content) > _max_download_bytes():
            raise SnapshotBrowserError("Snapshot download exceeds the configured size limit.")
        _set_download_step_status(
            task=task,
            step_name="snapshot_download_restore",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=55,
            current_step="snapshot_download_transfer",
        )
        _set_download_step_status(
            task=task,
            step_name="snapshot_download_transfer",
            status=TaskStep.Status.RUNNING,
            progress=40,
            task_progress=70,
        )
        if download.artifact_id != artifact.id:
            artifact = _persist_artifact(artifact=artifact, download=download)
        else:
            artifact.refresh_from_db()
        _set_download_step_status(
            task=task,
            step_name="snapshot_download_transfer",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=90,
            current_step="snapshot_download_finalize",
        )
        result = {
            "artifact_id": artifact.id,
            "filename": artifact.filename,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "expires_at": artifact.expires_at.isoformat(),
            "object_name": artifact.filename,
        }
        append_task_step_event(
            task=task,
            step_name="snapshot_download_transfer",
            message="Snapshot download artifact is ready",
            metadata=result,
        )
        _set_download_step_status(
            task=task,
            step_name="snapshot_download_finalize",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=100,
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.SUCCESS,
            result_payload=result,
        )
        return result
    except (SnapshotBrowserError, SnapshotBrowserForbidden, ValidationError) as exc:
        return _complete_failed_snapshot_download(
            task=task,
            artifact=artifact,
            message=str(exc),
        )
    except Exception:
        logger.exception(
            "snapshot download failed unexpectedly",
            extra={"task_id": task.id, "artifact_id": getattr(artifact, "id", None)},
        )
        return _complete_failed_snapshot_download(
            task=task,
            artifact=artifact,
            message="Snapshot download failed due to an internal error.",
        )


def _complete_failed_snapshot_download(
    *, task: Task, artifact: SnapshotDownloadArtifact | None, message: str
) -> dict[str, str]:
    _fail_pending_artifact(artifact=artifact)
    failed_step = str(task.current_step or "snapshot_download_restore")
    failed_progress = _failed_download_progress(failed_step)
    _set_download_step_status(
        task=task,
        step_name=failed_step,
        status=TaskStep.Status.FAILED,
        progress=100,
        task_progress=failed_progress,
        current_step=failed_step,
    )
    complete_task(
        task_uuid=task.task_uuid,
        organization_id=task.organization_id,
        status=Task.Status.FAILED,
        progress=failed_progress,
        error_code="SNAPSHOT_DOWNLOAD_FAILED",
        error_message=message,
    )
    return {"error": message}


def _download_batch_as_zip(*, organization_id: int, directory_id: int, paths: list[str], task_id: int) -> SnapshotFileDownload:
    clean_paths = _dedupe_clean_paths(paths)
    if _has_path_conflict(clean_paths):
        raise ValidationError({"paths": "Parent and child paths cannot be downloaded together."})
    buffer = BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for clean_path in clean_paths:
            download = download_snapshot_file(
                organization_id=organization_id,
                directory_id=directory_id,
                path=clean_path,
                wait_timeout_seconds=_download_timeout_seconds(),
            )
            if download.content_type == "application/zip":
                _append_nested_zip(
                    archive=archive,
                    content=download.content,
                    prefix=clean_path,
                    used_names=used_names,
                )
            else:
                entry_name = _unique_zip_name(clean_path, used_names)
                archive.writestr(entry_name, download.content)
    filename = _safe_filename(f"snapshot-download-{task_id}.zip", "snapshot-download.zip")
    return SnapshotFileDownload(
        filename=filename,
        content=buffer.getvalue(),
        content_type="application/zip",
    )


def _append_nested_zip(*, archive: zipfile.ZipFile, content: bytes, prefix: str, used_names: set[str]) -> None:
    try:
        nested = zipfile.ZipFile(BytesIO(content), "r")
    except zipfile.BadZipFile:
        entry_name = _unique_zip_name(f"{prefix}.zip", used_names)
        archive.writestr(entry_name, content)
        return
    with nested:
        for info in nested.infolist():
            nested_name = _clean_zip_entry_name(info.filename)
            if not nested_name:
                continue
            entry_name = _unique_zip_name(f"{prefix}/{nested_name}".rstrip("/"), used_names)
            if info.is_dir():
                archive.writestr(entry_name.rstrip("/") + "/", b"")
            else:
                archive.writestr(entry_name, nested.read(info))


def _clean_zip_entry_name(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _unique_zip_name(name: str, used_names: set[str]) -> str:
    clean_name = _clean_zip_entry_name(name) or "download"
    candidate = clean_name
    stem = Path(clean_name).stem
    suffix = Path(clean_name).suffix
    parent = str(Path(clean_name).parent).replace("\\", "/")
    if parent == ".":
        parent = ""
    counter = 2
    while candidate in used_names:
        base = f"{stem}-{counter}{suffix}"
        candidate = f"{parent}/{base}" if parent else base
        counter += 1
    used_names.add(candidate)
    return candidate


def _persist_artifact(*, artifact: SnapshotDownloadArtifact, download) -> SnapshotDownloadArtifact:
    if len(download.content) > _max_download_bytes():
        raise SnapshotBrowserError("Snapshot download exceeds the configured size limit.")
    artifact_path = Path(artifact.storage_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = artifact_path.with_name(f".{artifact_path.name}.legacy.part")
    temporary_path.write_bytes(download.content)
    os.chmod(temporary_path, 0o600)
    checksum = hashlib.sha256(download.content).hexdigest()
    with transaction.atomic():
        locked = SnapshotDownloadArtifact.objects.select_for_update().get(id=artifact.id)
        if locked.status != SnapshotDownloadArtifact.Status.UPLOADING:
            temporary_path.unlink(missing_ok=True)
            raise SnapshotBrowserError("Snapshot download artifact is not accepting content.")
        os.replace(temporary_path, artifact_path)
        locked.filename = _safe_filename(download.filename, locked.filename)
        locked.content_type = download.content_type or "application/octet-stream"
        locked.size_bytes = len(download.content)
        locked.sha256 = checksum
        locked.status = SnapshotDownloadArtifact.Status.READY
        locked.upload_token_digest = ""
        locked.save(
            update_fields=[
                "filename",
                "content_type",
                "size_bytes",
                "sha256",
                "status",
                "upload_token_digest",
                "updated_at",
            ]
        )
        return locked


def _fail_pending_artifact(*, artifact: SnapshotDownloadArtifact | None) -> None:
    if artifact is None:
        return
    SnapshotDownloadArtifact.objects.filter(
        id=artifact.id,
        status=SnapshotDownloadArtifact.Status.UPLOADING,
    ).update(
        status=SnapshotDownloadArtifact.Status.FAILED,
        upload_token_digest="",
        updated_at=timezone.now(),
    )
    path = Path(artifact.storage_path)
    for candidate in path.parent.glob(f".{path.name}.*.part") if path.parent.exists() else []:
        candidate.unlink(missing_ok=True)


def get_snapshot_download_artifact(*, organization_id: int, artifact_id: int) -> SnapshotDownloadArtifact | None:
    return SnapshotDownloadArtifact.objects.filter(
        organization_id=organization_id,
        id=artifact_id,
    ).select_related("task").first()


def mark_artifact_downloaded(*, artifact: SnapshotDownloadArtifact) -> None:
    artifact.downloaded_at = timezone.now()
    artifact.save(update_fields=["downloaded_at", "updated_at"])


def cleanup_expired_snapshot_download_artifacts(*, now=None, limit: int = 1000) -> int:
    cutoff = now or timezone.now()
    artifacts = list(
        SnapshotDownloadArtifact.objects.filter(
            status__in=[
                SnapshotDownloadArtifact.Status.UPLOADING,
                SnapshotDownloadArtifact.Status.READY,
                SnapshotDownloadArtifact.Status.FAILED,
            ],
            expires_at__lte=cutoff,
        ).order_by("expires_at", "id")[: max(1, limit)]
    )
    cleaned = 0
    for artifact in artifacts:
        try:
            if artifact.storage_path:
                os.remove(artifact.storage_path)
        except FileNotFoundError:
            pass
        path = Path(artifact.storage_path)
        if path.parent.exists():
            for partial in path.parent.glob(f".{path.name}.*.part"):
                partial.unlink(missing_ok=True)
        artifact.status = SnapshotDownloadArtifact.Status.EXPIRED
        artifact.upload_token_digest = ""
        artifact.save(update_fields=["status", "upload_token_digest", "updated_at"])
        cleaned += 1
    return cleaned
