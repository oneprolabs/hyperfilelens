"""Resource-scoped API adapter; organization administration keeps its own gates."""

from __future__ import annotations

from django.db import transaction
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from apps.iam.org_context import require_org
from apps.iam.permissions_org import get_membership, resolve_org_key
from apps.iam.resource_access import (
    assert_resource_access,
    filter_resource_queryset,
    record_resource_owner,
    resolve_resource_reference,
)
from common.extension_spi import get_authz_provider


class GovernedResourcePermission(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        if get_membership(request) is None:
            return False
        if getattr(request, "hfl_support_readonly", False):
            return request.method in permissions.SAFE_METHODS
        provider = get_authz_provider()
        gate = getattr(provider, "has_resource_action", None)
        if gate is None:
            return False
        action = view._resource_action()
        from apps.iam.query_authorization import request_authorization

        context = request_authorization(
            request, fresh=request.method not in permissions.SAFE_METHODS,
        )
        if getattr(view, "action", "") in ("create", "duplicate"):
            # Creation has no existing Group binding. Subtypes are validated
            # separately once the request body is available.
            if view.governance_resource_type not in ("node", "repository"):
                from apps.iam.permission_catalog import resource_permission
                from types import SimpleNamespace

                obj = None
                if view.governance_resource_type == "source_resource":
                    data = request.data if isinstance(request.data, dict) else {}
                    obj = SimpleNamespace(resource_type=data.get("resource_type", "nas"))
                key = resource_permission(view.governance_resource_type, "manage", obj)
                if context is not None:
                    return bool(key and context.has_org_action(key))
                return bool(
                    key
                    and provider.has_org_action(
                        request.user, resolve_org_key(request), key
                    )
                )
        if context is not None:
            return context.has_action(view.governance_resource_type, action)
        return gate(
            request.user,
            resolve_org_key(request),
            view.governance_resource_type,
            action,
        )


class GovernedResourceMixin:
    """Ensure list/detail/bulk operations use the same per-resource authority."""

    governance_resource_type = ""
    governance_manage_action = "protection.manage"

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        # Manual domain create handlers and their owner rows commit together.
        return super().create(request, *args, **kwargs)

    def get_permissions(self):
        original = super().get_permissions()
        if (
            get_authz_provider() is None
            or not callable(getattr(get_authz_provider(), "has_resource_action", None))
            or getattr(self, "action", "")
            in getattr(self, "org_scoped_skip_actions", ())
        ):
            return original
        return [permissions.IsAuthenticated(), GovernedResourcePermission()]

    def _resource_action(self):
        action = getattr(self, "action", "")
        if action in ("run", "run_batch", "run_source"):
            return "resource.run"
        if action == "cancel":
            return "resource.cancel"
        if action == "operations" and isinstance(self.request.data, dict):
            if self.request.data.get("kind") == "upgrade":
                return "resource.upgrade"
            if self.request.data.get("kind") == "remove":
                return "resource.delete"
        if self.request.method in permissions.SAFE_METHODS:
            return "resource.view"
        if self.request.method == "DELETE" or action in (
            "destroy",
            "bulk_delete",
            "remove",
        ):
            return "resource.delete"
        if "upgrade" in action:
            return "resource.upgrade"
        if action in ("test", "test_config"):
            return "resource.test"
        return "resource.manage"

    def get_queryset(self):
        return filter_resource_queryset(
            self.request,
            super().get_queryset(),
            self.governance_resource_type,
            action=self._resource_action(),
        )

    def get_object(self):
        obj = super().get_object()
        if get_authz_provider() is not None:
            org = require_org(self.request)
            ref = resolve_resource_reference(
                organization_id=org.id,
                resource_type=self.governance_resource_type,
                source_id=obj.pk,
            )
            assert_resource_access(
                self.request,
                self.governance_resource_type,
                ref,
                action=self._resource_action(),
            )
        return obj

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if get_authz_provider() is None or getattr(self, "action", "") in getattr(
            self, "org_scoped_skip_actions", ()
        ):
            return
        if getattr(self, "action", "") == "test_config":
            provider = get_authz_provider()
            if not provider.has_org_action(
                request.user,
                require_org(request).key,
                "operations.notification_channels.manage",
            ):
                raise PermissionDenied(
                    "Manage permission is required for testing unsaved configuration."
                )
        if getattr(self, "action", "") in (
            "create",
            "duplicate",
        ) and self.governance_resource_type in ("node", "repository"):
            from types import SimpleNamespace
            from apps.iam.permission_catalog import resource_permission

            provider = get_authz_provider()
            data = request.data if isinstance(request.data, dict) else {}
            role = data.get("role") or request.query_params.get("role") or "agent"
            repo_type = data.get("repo_type") or data.get("type") or data.get("kind")
            obj = SimpleNamespace(role=role, repo_type=repo_type)
            key = resource_permission(self.governance_resource_type, "manage", obj)
            if not key or not provider.has_org_action(
                request.user, require_org(request).key, key
            ):
                raise PermissionDenied("Resource creation permission is not granted.")
        if kwargs.get("pk") is not None and getattr(
            self, "governance_check_pk_in_initial", False
        ):
            org = require_org(request)
            ref = resolve_resource_reference(
                organization_id=org.id,
                resource_type=self.governance_resource_type,
                source_id=kwargs["pk"],
            )
            assert_resource_access(
                request,
                self.governance_resource_type,
                ref,
                action=self._resource_action(),
            )
        if (
            self.governance_resource_type == "source_resource"
            and request.method not in permissions.SAFE_METHODS
        ):
            if isinstance(request.data, dict):
                node_id = (
                    request.data.get("node_id")
                    or request.data.get("bound_node_id")
                    or request.data.get("bound_node")
                )
                if node_id:
                    ref = resolve_resource_reference(
                        organization_id=require_org(request).id,
                        resource_type="node",
                        source_id=node_id,
                    )
                    assert_resource_access(request, "node", ref, action="resource.view")
        if (
            self.governance_resource_type == "backup_config"
            and request.method not in permissions.SAFE_METHODS
        ):
            if isinstance(request.data, dict):
                from apps.protection.models import BackupConfig

                existing = (
                    BackupConfig.objects.filter(
                        organization_id=require_org(request).id,
                        pk=kwargs.get("pk"),
                    ).first()
                    if kwargs.get("pk")
                    else None
                )
                data = request.data
                refs = []
                if "source_ref_id" in data:
                    source_type = data.get("source_type") or getattr(
                        existing, "source_type", ""
                    )
                    refs.append(
                        (
                            "node" if source_type == "agent" else "source_resource",
                            data["source_ref_id"],
                            "resource.view",
                        )
                    )
                for field, kind, action in (
                    ("repository_id", "repository", "resource.view"),
                    ("backup_policy_id", "backup_policy", "resources.view"),
                    ("file_filter_rule_id", "file_filter_rule", "resources.view"),
                ):
                    if data.get(field):
                        refs.append((kind, data[field], action))
                for kind, source_id, action in refs:
                    ref = resolve_resource_reference(
                        organization_id=require_org(request).id,
                        resource_type=kind,
                        source_id=source_id,
                    )
                    assert_resource_access(request, kind, ref, action=action)
        if request.method in permissions.SAFE_METHODS:
            return
        if getattr(self, "action", "") in ("bulk_state", "bulk_delete"):
            org = require_org(request)
            if not isinstance(request.data, dict):
                return
            ids = request.data.get("ids", [])
            if not isinstance(ids, list):
                return
            if len(ids) > 500:
                from rest_framework.exceptions import ValidationError

                raise ValidationError("At most 500 resources per operation.")
            for source_id in ids:
                if isinstance(source_id, bool):
                    raise PermissionDenied("Invalid resource ID.")
                ref = resolve_resource_reference(
                    organization_id=org.id,
                    resource_type=self.governance_resource_type,
                    source_id=source_id,
                )
                assert_resource_access(
                    request,
                    self.governance_resource_type,
                    ref,
                    action=self._resource_action(),
                )

    def perform_create(self, serializer):
        with transaction.atomic():
            super().perform_create(serializer)
            obj = serializer.instance
            if obj is not None:
                record_resource_owner(
                    organization_id=require_org(self.request).id,
                    resource_type=self.governance_resource_type,
                    resource_id=obj.pk,
                    owner_id=self.request.user.id,
                )
