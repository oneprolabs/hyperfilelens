"""Advisory UI capabilities; every business operation still checks authority."""

from __future__ import annotations

import uuid
from typing import Any

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgAuthorizedMember
from apps.iam.permission_catalog import (
    resource_permission,
    NODE_PERMISSION_PREFIXES,
    REPOSITORY_PERMISSION_PREFIXES,
)
from apps.iam.resource_access import resolve_resource_reference
from common.extension_spi import get_authz_provider


RESOURCE_TYPES = (
    "node",
    "repository",
    "source_resource",
    "backup_config",
    "restore_plan",
    "backup_policy",
    "file_filter_rule",
    "alert_policy",
    "notification_channel",
)


def management_action(kind: str) -> str:
    return (
        "operations.manage"
        if kind in ("alert_policy", "notification_channel")
        else "protection.manage"
    )


class CapabilityReferenceSerializer(serializers.Serializer):
    resource_type = serializers.ChoiceField(choices=RESOURCE_TYPES)
    resource_id = serializers.CharField(max_length=64)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            if set(data) != {"resource_type", "resource_id"} or isinstance(
                data.get("resource_id"), bool
            ):
                raise serializers.ValidationError(
                    "A typed resource reference is required."
                )
        return super().to_internal_value(data)

    def validate(self, attrs):
        raw = attrs["resource_id"]
        if attrs["resource_type"] == "alert_policy":
            try:
                attrs["resource_id"] = str(uuid.UUID(raw))
            except ValueError:
                raise serializers.ValidationError(
                    {"resource_id": "A rule UUID is required."}
                ) from None
        elif not raw.isascii() or not raw.isdigit() or not 0 < int(raw) <= 2**63 - 1:
            raise serializers.ValidationError(
                {"resource_id": "A positive 64-bit resource ID is required."}
            )
        else:
            attrs["resource_id"] = str(int(raw))
        return attrs


class CapabilityRequestSerializer(serializers.Serializer):
    resources = CapabilityReferenceSerializer(many=True, max_length=200)

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) != {"resources"}:
            raise serializers.ValidationError(
                "Only resource references may be provided."
            )
        return super().to_internal_value(data)


def current_objects(org_id: int, references: list[dict]) -> dict[tuple[str, str], Any]:
    """Use Host objects, not supplied IDs, to enforce tenant/lifecycle boundaries."""
    from apps.alert.models import AlertPolicy
    from apps.lens_bridge.models import LensGatewayLink
    from apps.node.models import Node
    from apps.notification.models import NotificationChannel
    from apps.protection.models import BackupConfig, BackupPolicy, FileFilterRule
    from apps.restore.models import RestorePlan
    from apps.source.constants import ResourceStatus
    from apps.source.models import SourceResource
    from apps.storage.repositories.models import Repository

    models = {
        "node": Node,
        "repository": Repository,
        "source_resource": SourceResource,
        "backup_config": BackupConfig,
        "restore_plan": RestorePlan,
        "backup_policy": BackupPolicy,
        "file_filter_rule": FileFilterRule,
        "alert_policy": AlertPolicy,
        "notification_channel": NotificationChannel,
    }
    objects = {}
    for kind, model in models.items():
        ids = [ref["resource_id"] for ref in references if ref["resource_type"] == kind]
        if not ids:
            continue
        rows = model.objects.filter(organization_id=org_id, pk__in=ids)
        if kind == "node":
            rows = rows.filter(is_deleted=False).exclude(
                pk__in=LensGatewayLink.objects.filter(scope="platform").values(
                    "gateway_id"
                ),
            )
        elif kind == "source_resource":
            rows = rows.filter(is_deleted=False).exclude(status=ResourceStatus.REMOVED)
        elif kind == "repository":
            rows = rows.exclude(status=Repository.Status.REMOVED)
        elif kind == "restore_plan":
            rows = rows.filter(
                backup_config_id__in=BackupConfig.objects.filter(
                    organization_id=org_id,
                ).values("pk")
            )
        objects.update({(kind, str(obj.pk)): obj for obj in rows})
    return objects


class ResourceCapabilitiesView(APIView):
    """Read-only POST for bounded batches; capability grants never consume quota."""

    permission_classes = [IsAuthenticated, IsOrgAuthorizedMember]

    def post(self, request):
        org = require_org(request)
        serializer = CapabilityRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refs = serializer.validated_data["resources"]
        objects = current_objects(org.pk, refs)
        provider = get_authz_provider()
        support = getattr(request, "hfl_support_readonly", False)
        canonical = {
            key: (
                key[0],
                resolve_resource_reference(
                    organization_id=org.pk,
                    resource_type=key[0],
                    source_id=key[1],
                ),
            )
            for key in objects
        }
        from apps.iam.query_authorization import request_authorization

        context = request_authorization(request)
        extra_actions = (
            "protection.backup_data.browse", "protection.backup_data.download",
            "protection.backup_data.delete", "insights.chats.use",
            "protection.restore_plans.run", "protection.restore_plans.cancel",
            "protection.backup_configurations.run", "protection.backup_configurations.cancel",
            "insights.private_data_gateways.use",
        )
        intents = ("view", "manage", "delete", "upgrade", "run", "cancel", "test")
        matrix = context.visible_refs_by_action(
            list(canonical.values()), [*(f"resource.{intent}" for intent in intents), *extra_actions],
        ) if context is not None and not support else None
        def resolve_visible(action):
            if context is not None:
                return matrix[action]
            return provider.visible_resource_refs(
                request.user, org.key, list(canonical.values()), action=action,
            )
        permissions_by_action = {}
        for intent in intents:
            if support:
                allowed = set(canonical.values()) if intent == "view" else set()
            elif provider is None:
                allowed = set(canonical.values())
            else:
                allowed = resolve_visible(f"resource.{intent}")
            permissions_by_action[intent] = allowed
        extra = {}
        for action in extra_actions:
            extra[action] = (
                resolve_visible(action)
                if provider and not support
                else set(canonical.values())
                if provider is None and not support
                else set()
            )
        rows = []
        for ref in refs:
            key = (ref["resource_type"], ref["resource_id"])
            reference = canonical.get(key)
            rows.append(
                {
                    **ref,
                    **{
                        intent: reference in permissions_by_action[intent]
                        for intent in intents
                    },
                    "browse_data": reference in extra["protection.backup_data.browse"],
                    "download_data": reference
                    in extra["protection.backup_data.download"],
                    "delete_data": reference in extra["protection.backup_data.delete"],
                    "use_insights": reference in extra["insights.chats.use"],
                    "run_restore": reference in extra["protection.restore_plans.run"],
                    "cancel_restore": reference in extra["protection.restore_plans.cancel"],
                    "run_backup": reference in extra["protection.backup_configurations.run"],
                    "cancel_backup": reference in extra["protection.backup_configurations.cancel"],
                    "use_gateway": reference
                    in extra["insights.private_data_gateways.use"],
                    "manage_insights": key[0] == "node"
                    and getattr(objects.get(key), "role", "") == "gateway"
                    and reference in permissions_by_action["manage"],
                }
            )
        from types import SimpleNamespace

        create = {}
        for kind in RESOURCE_TYPES:
            objects = [None]
            if kind == "node":
                objects = [
                    SimpleNamespace(role=role) for role in NODE_PERMISSION_PREFIXES
                ]
            elif kind == "repository":
                objects = [
                    SimpleNamespace(repo_type=repo_type)
                    for repo_type in REPOSITORY_PERMISSION_PREFIXES
                ]
            create[kind] = False
            for obj in objects:
                permission = resource_permission(kind, "manage", obj)
                allowed = not support and (
                    provider is None
                    or bool(
                        permission
                        and provider.has_org_action(request.user, org.key, permission)
                    )
                )
                create[kind] |= allowed
                if obj is not None:
                    subtype = getattr(obj, "role", None) or obj.repo_type
                    create[f"{kind}:{subtype}"] = allowed
        return Response(
            {"organization_key": org.key, "resources": rows, "create": create}
        )
