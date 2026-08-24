from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction

from apps.protection.models import BackupConfig, BackupConfigCreateRequest


class BackupConfigCreateIdempotencyConflict(Exception):
    pass


@dataclass(frozen=True)
class BackupConfigCreateResult:
    payload: dict
    status_code: int
    replayed: bool
    backup_config: BackupConfig | None


def backup_config_create_request_digest(data: dict) -> str:
    canonical = json.dumps(
        data,
        cls=DjangoJSONEncoder,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _create_or_lock_request(
    *,
    organization_id: int,
    idempotency_key: str,
    request_digest: str,
) -> tuple[BackupConfigCreateRequest, bool]:
    try:
        with transaction.atomic():
            return (
                BackupConfigCreateRequest.objects.create(
                    organization_id=organization_id,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                ),
                True,
            )
    except IntegrityError:
        return (
            BackupConfigCreateRequest.objects.select_for_update().get(
                organization_id=organization_id,
                idempotency_key=idempotency_key,
            ),
            False,
        )


def execute_idempotent_backup_config_create(
    *,
    organization_id: int,
    idempotency_key: str,
    data: dict,
    create: Callable[[], tuple[BackupConfig, dict, int]],
) -> BackupConfigCreateResult:
    request_digest = backup_config_create_request_digest(data)
    with transaction.atomic():
        request_record, created = _create_or_lock_request(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if request_record.request_digest != request_digest:
            raise BackupConfigCreateIdempotencyConflict(
                "This idempotency key is already bound to a different backup configuration request."
            )
        if not created:
            if request_record.response_status is None or not request_record.response_payload:
                raise RuntimeError("idempotent backup configuration request has no durable result")
            return BackupConfigCreateResult(
                payload=dict(request_record.response_payload),
                status_code=int(request_record.response_status),
                replayed=True,
                backup_config=request_record.backup_config,
            )

        config, payload, status_code = create()
        request_record.backup_config = config
        request_record.response_payload = payload
        request_record.response_status = status_code
        request_record.save(
            update_fields=[
                "backup_config",
                "response_payload",
                "response_status",
                "updated_at",
            ]
        )
        return BackupConfigCreateResult(
            payload=payload,
            status_code=status_code,
            replayed=False,
            backup_config=config,
        )
