"""Platform Gateway selection tests."""

from __future__ import annotations

from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import platform_lens
from apps.lens_bridge.services.chat_lifecycle import _configured_gateway_link_for_chat
from apps.node.models import Node
from apps.node.models.base import NodeRole


class PlatformGatewaySelectionTests(TestCase):
    def test_chat_auto_selection_uses_readiness_aware_platform_resolver(self):
        expected = mock.Mock()
        user = mock.Mock()

        with mock.patch(
            "apps.lens_bridge.services.chat_lifecycle.platform_lens"
        ) as chat_platform_lens:
            chat_platform_lens.resolve_auto_gateway_link_for_copilot.return_value = (
                expected
            )

            resolved = _configured_gateway_link_for_chat(
                mock.Mock(),
                user=user,
                gateway_mode="auto",
                gateway_link_id=None,
            )

        self.assertIs(resolved, expected)
        chat_platform_lens.resolve_auto_gateway_link_for_copilot.assert_called_once_with(
            user=user
        )

    def test_missing_auto_gateway_uses_public_private_product_terms(self):
        tenant = Organization.objects.create(
            key="gateway-copy-tenant",
            name="Gateway Copy Tenant",
        )

        with self.assertRaises(ValidationError) as raised:
            platform_lens.resolve_gateway_link_for_copilot(
                tenant,
                user=mock.Mock(),
            )

        self.assertEqual(
            str(raised.exception.detail["gateway_link_id"]),
            (
                "No public Data Gateway is available. Select a private "
                "Data Gateway or contact your administrator."
            ),
        )

    def test_explicit_platform_default_is_selected_before_older_gateway(self):
        org = platform_lens.get_or_create_platform_org()
        older_gateway = Node.objects.create(
            organization=org,
            name="older-gateway",
            role=NodeRole.GATEWAY,
        )
        selected_gateway = Node.objects.create(
            organization=org,
            name="selected-gateway",
            role=NodeRole.GATEWAY,
        )
        LensGatewayLink.objects.create(
            organization=org,
            gateway=older_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            sl_lensnode_uuid="9f16dace-78ae-4979-9e88-a63d6c641f8e",
            sidecar_status=LensGatewayLink.SidecarStatus.ONLINE,
        )
        selected = LensGatewayLink.objects.create(
            organization=org,
            gateway=selected_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            sl_lensnode_uuid="e440d5a4-2dc0-4ff9-b268-5afee3211d30",
            sidecar_status=LensGatewayLink.SidecarStatus.ONLINE,
            is_platform_default=True,
        )

        with mock.patch(
            "apps.lens_bridge.services.platform_lens.gateway_runtime_state",
            return_value={"copilot_eligible": True},
        ):
            resolved = platform_lens.resolve_platform_default_gateway_link()

        self.assertEqual(resolved, selected)

    def test_auto_selection_falls_back_when_default_is_not_copilot_ready(self):
        org = platform_lens.get_or_create_platform_org()
        fallback_gateway = Node.objects.create(
            organization=org,
            name="fallback-gateway",
            role=NodeRole.GATEWAY,
        )
        stale_gateway = Node.objects.create(
            organization=org,
            name="stale-default-gateway",
            role=NodeRole.GATEWAY,
        )
        fallback = LensGatewayLink.objects.create(
            organization=org,
            gateway=fallback_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            sl_lensnode_uuid="c440d5a4-2dc0-4ff9-b268-5afee3211d30",
            sidecar_status=LensGatewayLink.SidecarStatus.ONLINE,
        )
        stale_default = LensGatewayLink.objects.create(
            organization=org,
            gateway=stale_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            sl_lensnode_uuid="d440d5a4-2dc0-4ff9-b268-5afee3211d30",
            sidecar_status=LensGatewayLink.SidecarStatus.OFFLINE,
            is_platform_default=True,
        )

        def readiness(link):
            return {"copilot_eligible": link.pk == fallback.pk}

        with mock.patch(
            "apps.lens_bridge.services.platform_lens.gateway_runtime_state",
            side_effect=readiness,
        ):
            resolved = platform_lens.resolve_auto_gateway_link_for_copilot(
                user=mock.Mock()
            )

        self.assertEqual(resolved, fallback)
        self.assertNotEqual(resolved, stale_default)

    def test_database_rejects_multiple_live_platform_defaults(self):
        org = platform_lens.get_or_create_platform_org()
        first_gateway = Node.objects.create(
            organization=org,
            name="first-default",
            role=NodeRole.GATEWAY,
        )
        second_gateway = Node.objects.create(
            organization=org,
            name="second-default",
            role=NodeRole.GATEWAY,
        )
        LensGatewayLink.objects.create(
            organization=org,
            gateway=first_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            owner_user=None,
            is_platform_default=True,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            LensGatewayLink.objects.create(
                organization=org,
                gateway=second_gateway,
                scope=LensGatewayLink.GatewayScope.PLATFORM,
                owner_user=None,
                is_platform_default=True,
            )

    @mock.patch(
        "apps.lens_bridge.services.platform_lens.require_hfl_usable_gateway"
    )
    def test_setting_a_new_default_clears_the_previous_default(self, _ready):
        org = platform_lens.get_or_create_platform_org()
        first_gateway = Node.objects.create(
            organization=org,
            name="previous-default",
            role=NodeRole.GATEWAY,
        )
        second_gateway = Node.objects.create(
            organization=org,
            name="replacement-default",
            role=NodeRole.GATEWAY,
        )
        previous = LensGatewayLink.objects.create(
            organization=org,
            gateway=first_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            owner_user=None,
            is_platform_default=True,
        )
        replacement = LensGatewayLink.objects.create(
            organization=org,
            gateway=second_gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            owner_user=None,
        )

        platform_lens.set_platform_default_gateway(gateway_link_id=replacement.id)

        previous.refresh_from_db()
        replacement.refresh_from_db()
        self.assertFalse(previous.is_platform_default)
        self.assertTrue(replacement.is_platform_default)
