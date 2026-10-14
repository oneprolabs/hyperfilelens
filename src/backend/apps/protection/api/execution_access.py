"""Linked-resource authorization for user-triggered protection workflows."""

from __future__ import annotations

from typing import Any, Iterable

from rest_framework.exceptions import PermissionDenied

from apps.iam.org_context import require_org
from apps.iam.resource_access import assert_resource_access
from apps.protection.models import BackupConfig, BackupPolicy
from apps.source.services.internal.selectable_ids import parse_selectable_id
from common.extension_spi import get_authz_provider


def assert_backup_config_execution(
    request: Any,
    config_id: int,
    *,
    action: str = "protection.backup_configurations.run",
) -> None:
    """An owned configuration cannot promote read-only linked resource access."""
    if get_authz_provider() is None:
        return
    config = BackupConfig.objects.filter(
        organization_id=require_org(request).id, pk=config_id
    ).first()
    if config is None or config.source_type not in ("agent", "nas"):
        raise PermissionDenied("This configuration has no authorized source.")
    for kind, pk in (
        ("backup_config", config.pk),
        ("node" if config.source_type == "agent" else "source_resource", config.source_ref_id),
        ("repository", config.repository_id),
    ):
        assert_resource_access(request, kind, pk, action=action)


def assert_source_cleanup(
    request: Any,
    source_ids: Iterable[str],
    *,
    config_action: str = "protection.backup_configurations.delete",
    source_action: str = "resource.manage",
) -> None:
    """Authorize all affected configurations before any source-wide cleanup."""
    if get_authz_provider() is None:
        return
    org = require_org(request)
    for source_id in source_ids:
        parsed = parse_selectable_id(str(source_id))
        if parsed is None or parsed[0] not in ("agent", "nas"):
            raise PermissionDenied("Invalid cleanup source reference.")
        kind, pk = parsed
        assert_resource_access(
            request, "node" if kind == "agent" else "source_resource", pk,
            action=source_action,
        )
        for config in BackupConfig.objects.filter(
            organization_id=org.pk, source_type=kind, source_ref_id=pk
        ):
            assert_resource_access(
                request, "backup_config", config.pk, action=config_action
            )
            assert_resource_access(
                request, "backup_config", config.pk,
                action="protection.backup_data.delete",
            )


def assert_scheduled_config_write(request: Any, data: dict, config=None) -> None:
    """Enabling/changing an automatic backup is execution, not merely CRUD."""
    provider = get_authz_provider()
    if provider is None:
        return
    if config is not None and not {
        "source_type", "source_ref_id", "repository_id", "backup_policy_id", "directories"
    } & data.keys():
        return
    policy_id = data.get("backup_policy_id", getattr(config, "backup_policy_id", None))
    policy = BackupPolicy.objects.filter(
        organization_id=require_org(request).id, pk=policy_id, is_active=True
    ).first()
    if policy is None or not (policy.schedule or {}).get("enabled", False):
        return
    action_name = "protection.backup_configurations.run"
    if config is None:
        if not provider.has_org_action(request.user, require_org(request).key, action_name):
            raise PermissionDenied("Run Backups permission is required for scheduled backups.")
    else:
        assert_resource_access(request, "backup_config", config.pk, action=action_name)
    source_type = data.get("source_type", getattr(config, "source_type", ""))
    source_id = data.get("source_ref_id", getattr(config, "source_ref_id", 0))
    repository_id = data.get("repository_id", getattr(config, "repository_id", 0))
    assert_resource_access(
        request, "node" if source_type == "agent" else "source_resource", source_id,
        action=action_name,
    )
    assert_resource_access(request, "repository", repository_id, action=action_name)


def assert_scheduled_policy_write(request: Any, policy: BackupPolicy, data: dict) -> None:
    """A shared policy cannot start backups outside the actor's execution scope."""
    if get_authz_provider() is None or not {"is_active", "schedule"} & data.keys():
        return
    schedule = data.get("schedule", policy.schedule) or {}
    if not data.get("is_active", policy.is_active) or not schedule.get("enabled", False):
        return
    for config_id in BackupConfig.objects.filter(
        organization_id=require_org(request).id, backup_policy_id=policy.pk
    ).values_list("pk", flat=True):
        assert_backup_config_execution(request, config_id)
