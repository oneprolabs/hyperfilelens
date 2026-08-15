"""Ensure Copilot chat bindings (backup source + snapshot + gateway preference).

KS/Assistant are created per Chat (see chat_lifecycle) — binding does not create them.
DG is only selected/referenced, never created or deleted here.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import LensChatBinding, LensGatewayLink
from apps.lens_bridge.services import (
    chat_user_provisioning,
    gateway_readiness,
    platform_lens,
    provisioning,
    sl_client,
)
from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.storage.services.internal.repository_workload import (
    RepositoryWorkload,
    lock_repositories_for_workload,
)


def _validate_snapshot(
    org: Organization,
    *,
    backup_config_id: int,
    backup_source_snapshot_id: int,
    backup_snapshot_directory_id: int | None,
) -> tuple[BackupConfig, BackupSourceSnapshot, str]:
    config = BackupConfig.objects.filter(
        organization_id=org.id,
        id=backup_config_id,
    ).first()
    if config is None:
        raise ValidationError({"backup_config_id": "Backup source not found."})
    lock_repositories_for_workload(
        organization_id=org.id,
        repository_ids=[config.repository_id],
        workload=RepositoryWorkload.RESTORE_READ,
    )

    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=org.id,
        id=backup_source_snapshot_id,
        backup_config_id=backup_config_id,
    ).first()
    if snapshot is None:
        raise ValidationError({"backup_source_snapshot_id": "Snapshot not found."})
    if snapshot.status not in (
        BackupSourceSnapshot.Status.AVAILABLE,
        BackupSourceSnapshot.Status.PARTIAL,
    ):
        raise ValidationError(
            {"backup_source_snapshot_id": "Snapshot is not available."}
        )

    source_path = ""
    if backup_snapshot_directory_id:
        directory = BackupSourceSnapshotDirectory.objects.filter(
            organization_id=org.id,
            id=backup_snapshot_directory_id,
            source_snapshot_id=snapshot.id,
        ).first()
        if directory is None:
            raise ValidationError(
                {"backup_snapshot_directory_id": "Snapshot directory not found."}
            )
        source_path = str(directory.path or "").strip()
    if not source_path:
        raise ValidationError({"source_path": "Snapshot directory path is required."})

    return config, snapshot, source_path


def _grant_assistant_to_chat_user(*, assistant_uuid, sl_user_id: int) -> None:
    sl_client.request_json(
        "PATCH",
        f"/api/lens/assistants/{assistant_uuid}/",
        json_body={"access_grants": [{"type": "user", "id": sl_user_id}]},
    )


@transaction.atomic
def ensure_chat_binding(
    org: Organization,
    *,
    user: AbstractBaseUser,
    backup_config_id: int,
    backup_source_snapshot_id: int,
    backup_snapshot_directory_id: int | None = None,
    source_path: str = "",
    gateway_link_id: int | None = None,
) -> LensChatBinding:
    """Save Copilot source+DG preference only. Does not create KS/Ass/DG."""
    chat_user_provisioning.ensure_sl_chat_user(user)

    _config, _snapshot, resolved_path = _validate_snapshot(
        org,
        backup_config_id=backup_config_id,
        backup_source_snapshot_id=backup_source_snapshot_id,
        backup_snapshot_directory_id=backup_snapshot_directory_id,
    )
    path = (source_path or resolved_path).strip()
    if not path:
        raise ValidationError({"source_path": "Source path is required."})

    gateway_link = platform_lens.resolve_gateway_link_for_copilot(
        org,
        user=user,
        gateway_link_id=gateway_link_id,
    )
    gateway_readiness.require_copilot_gateway(gateway_link)

    existing = (
        LensChatBinding.objects.filter(
            organization=org,
            hfl_user=user,
            backup_config_id=backup_config_id,
            backup_source_snapshot_id=backup_source_snapshot_id,
            backup_snapshot_directory_id=backup_snapshot_directory_id,
            gateway_link=gateway_link,
            source_path=path,
            is_active=True,
        )
        .select_related("gateway_link", "gateway_link__gateway")
        .first()
    )
    if existing is not None:
        return existing

    LensChatBinding.objects.filter(
        organization=org,
        hfl_user=user,
        is_active=True,
    ).update(is_active=False)

    return LensChatBinding.objects.create(
        organization=org,
        hfl_user=user,
        backup_config_id=backup_config_id,
        backup_source_snapshot_id=backup_source_snapshot_id,
        backup_snapshot_directory_id=backup_snapshot_directory_id,
        source_path=path,
        gateway_link=gateway_link,
        knowledge_source=None,
        sl_assistant_uuid=None,
        is_active=True,
    )


def get_active_chat_binding(
    org: Organization, *, user: AbstractBaseUser
) -> LensChatBinding | None:
    return (
        LensChatBinding.objects.filter(
            organization=org,
            hfl_user=user,
            is_active=True,
        )
        .select_related("knowledge_source", "gateway_link", "gateway_link__gateway")
        .first()
    )


def list_gateway_options(org: Organization, *, user) -> list[dict[str, Any]]:
    """List platform and current-user DGs available for explicit Copilot selection."""
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    for link in platform_lens.platform_gateway_links():
        if not link.sl_lensnode_uuid:
            continue
        provisioning.sync_gateway_lensnode_status(link)
        rows.append(
            _gateway_option_row(
                link,
                scope="platform",
                is_platform_default=bool(link.is_platform_default),
            )
        )
        seen.add(link.id)

    for link in platform_lens.user_gateway_links(
        user=user,
        organization=org,
    ).select_related("gateway"):
        if not link.sl_lensnode_uuid or link.id in seen:
            continue
        provisioning.sync_gateway_lensnode_status(link)
        rows.append(_gateway_option_row(link, scope="user"))
        seen.add(link.id)

    return rows


def _gateway_option_row(
    link: LensGatewayLink,
    *,
    scope: str,
    is_platform_default: bool = False,
) -> dict[str, Any]:
    gw = link.gateway
    runtime_state = gateway_readiness.gateway_runtime_state(link)
    return {
        "gateway_link_id": link.id,
        "gateway_id": gw.id,
        "name": gw.name,
        "scope": scope,
        "is_platform_default": is_platform_default,
        "sidecar_status": link.sidecar_status,
        "online": runtime_state["hfl_usable"],
        "hfl_usable": runtime_state["hfl_usable"],
        "copilot_eligible": runtime_state["copilot_eligible"],
    }


def serialize_chat_binding(binding: LensChatBinding) -> dict[str, Any]:
    ks = binding.knowledge_source
    gw = binding.gateway_link.gateway if binding.gateway_link_id else None
    return {
        "id": binding.id,
        "backup_config_id": binding.backup_config_id,
        "backup_source_snapshot_id": binding.backup_source_snapshot_id,
        "backup_snapshot_directory_id": binding.backup_snapshot_directory_id,
        "source_path": binding.source_path,
        "gateway_link_id": binding.gateway_link_id,
        "gateway_name": gw.name if gw else "",
        "gateway_scope": binding.gateway_link.scope if binding.gateway_link_id else "",
        "knowledge_source_id": ks.id if ks else None,
        "knowledge_source_status": ks.status if ks else None,
        "sl_assistant_uuid": str(binding.sl_assistant_uuid)
        if binding.sl_assistant_uuid
        else None,
        "is_active": binding.is_active,
        "ready_for_chat": bool(binding.gateway_link_id and binding.source_path),
    }
