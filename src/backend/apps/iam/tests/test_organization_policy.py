"""Community defaults and the optional organization policy SPI."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied

from apps.iam.organization_policy import (
    OrganizationPolicy, require_organization_capability, resolve_organization_policy,
)


class OrganizationPolicyTests(SimpleTestCase):
    def test_community_keeps_direct_storage_without_managed_storage(self):
        with patch("apps.iam.organization_policy.get_authz_provider", return_value=None):
            policy = resolve_organization_policy(7)
        self.assertEqual(policy.mode, "standard")
        self.assertEqual(policy.capabilities(), {
            "managed_storage": False, "object_storage": True,
            "target_nas": True, "local_disks": True,
            "source_nas": True, "proxy_hosts": True, "source_tags": True,
        })

    def test_request_cache_is_bound_to_provider_and_organization(self):
        request = SimpleNamespace()
        provider = Mock()
        provider.organization_policy.return_value = OrganizationPolicy()
        with patch("apps.iam.organization_policy.get_authz_provider", return_value=provider):
            resolve_organization_policy(7, request=request)
            resolve_organization_policy(7, request=request)
            self.assertEqual(provider.organization_policy.call_count, 1)
            resolve_organization_policy(8, request=request)
            self.assertEqual(provider.organization_policy.call_count, 2)

    def test_invalid_provider_result_does_not_restore_standard_capabilities(self):
        provider = Mock()
        provider.organization_policy.return_value = None
        with patch("apps.iam.organization_policy.get_authz_provider", return_value=provider):
            with self.assertRaises(PermissionDenied):
                resolve_organization_policy(7)

    def test_provider_without_policy_resolver_fails_closed(self):
        provider = object()
        with patch("apps.iam.organization_policy.get_authz_provider", return_value=provider):
            with self.assertRaises(PermissionDenied):
                resolve_organization_policy(7)

    def test_capability_check_does_not_treat_unknown_capabilities_as_available(self):
        with patch("apps.iam.organization_policy.get_authz_provider", return_value=None):
            require_organization_capability(7, "object_storage")
            for capability in ("managed_storage", "unknown"):
                with self.subTest(capability=capability), self.assertRaises(PermissionDenied):
                    require_organization_capability(7, capability)

    def test_source_policy_does_not_disable_agent_or_other_permissions(self):
        policy = OrganizationPolicy(source_nas=False, proxy_hosts=False, source_tags=False)
        for prefix in ("source_nas", "proxy_hosts", "tags"):
            for action in ("view", "manage", "delete"):
                self.assertFalse(policy.allows_source_permission(f"protection.{prefix}.{action}"))
        for permission in (
            "protection.source_hosts.manage", "protection.backup_configurations.view",
            "configuration.members.manage", "insights.chats.use",
        ):
            self.assertTrue(policy.allows_source_permission(permission))
