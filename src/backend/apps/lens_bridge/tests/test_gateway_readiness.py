from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import gateway_readiness


def _link(*, origin=LensGatewayLink.Origin.USER, sidecar_status=LensGatewayLink.SidecarStatus.ONLINE):
    return SimpleNamespace(
        gateway_id=21,
        gateway=SimpleNamespace(
            role="gateway",
            metadata={
                "inventory": {"capabilities": ["insight_safe_restore_v1"]},
                "inventory_session_id": "session-1",
                "inventory_capabilities_session_id": "session-1",
            },
        ),
        sl_lensnode_uuid="c5381ee5-4569-4ffb-8915-6f72705fa6c7",
        origin=origin,
        sidecar_status=sidecar_status,
    )


class GatewayReadinessTests(SimpleTestCase):
    @patch(
        "apps.lens_bridge.services.gateway_readiness.get_agent_session",
        return_value="session-1",
    )
    @patch("apps.lens_bridge.services.gateway_readiness.agent_ws_routable", return_value=True)
    def test_hfl_gateway_bundle_is_copilot_eligible_when_fully_ready(
        self, mock_routable, _mock_session
    ):
        state = gateway_readiness.gateway_runtime_state(_link(), sl_runtime_status="online")

        self.assertTrue(state["hfl_managed"])
        self.assertTrue(state["hfl_agent_online"])
        self.assertTrue(state["hfl_sidecar_online"])
        self.assertTrue(state["hfl_usable"])
        self.assertTrue(state["copilot_eligible"])
        self.assertEqual(state["readiness_reason"], "ready")
        mock_routable.assert_called_once_with(agent_id=21)

    def test_unmapped_sl_lensnode_is_discoverable_but_not_hfl_usable(self):
        state = gateway_readiness.gateway_runtime_state(None, sl_runtime_status="online")

        self.assertFalse(state["hfl_managed"])
        self.assertFalse(state["hfl_agent_online"])
        self.assertFalse(state["hfl_sidecar_online"])
        self.assertFalse(state["hfl_usable"])
        self.assertFalse(state["copilot_eligible"])
        self.assertEqual(state["readiness_reason"], "not_managed")

    @patch("apps.lens_bridge.services.gateway_readiness.agent_ws_routable", return_value=False)
    def test_hfl_gateway_without_routable_agent_is_not_usable(self, _mock_routable):
        state = gateway_readiness.gateway_runtime_state(_link())

        self.assertFalse(state["hfl_usable"])
        self.assertFalse(state["copilot_eligible"])
        self.assertEqual(state["readiness_reason"], "agent_offline")

    @patch(
        "apps.lens_bridge.services.gateway_readiness.get_agent_session",
        return_value="session-1",
    )
    @patch("apps.lens_bridge.services.gateway_readiness.agent_ws_routable", return_value=True)
    def test_gateway_without_safe_restore_capability_is_not_copilot_eligible(
        self, _mock_routable, _mock_session
    ):
        link = _link()
        link.gateway.metadata = {"inventory": {"capabilities": []}}

        state = gateway_readiness.gateway_runtime_state(link)

        self.assertTrue(state["hfl_agent_online"])
        self.assertFalse(state["hfl_agent_capabilities_ready"])
        self.assertTrue(state["hfl_usable"])
        self.assertFalse(state["copilot_eligible"])
        self.assertEqual(state["readiness_reason"], "capabilities_syncing")

    @patch(
        "apps.lens_bridge.services.gateway_readiness.get_agent_session",
        return_value="session-2",
    )
    @patch("apps.lens_bridge.services.gateway_readiness.agent_ws_routable", return_value=True)
    def test_old_session_capability_does_not_make_new_session_copilot_eligible(
        self, _mock_routable, _mock_session
    ):
        state = gateway_readiness.gateway_runtime_state(_link())

        self.assertTrue(state["hfl_agent_online"])
        self.assertFalse(state["hfl_agent_capabilities_ready"])
        self.assertFalse(state["copilot_eligible"])

    @patch(
        "apps.lens_bridge.services.gateway_readiness.get_agent_session",
        return_value=None,
    )
    @patch("apps.lens_bridge.services.gateway_readiness.agent_ws_routable", return_value=True)
    def test_unknown_agent_session_fails_closed_for_copilot(
        self, _mock_routable, _mock_session
    ):
        state = gateway_readiness.gateway_runtime_state(_link())

        self.assertTrue(state["hfl_agent_online"])
        self.assertFalse(state["hfl_agent_capabilities_ready"])
        self.assertFalse(state["copilot_eligible"])

    @patch(
        "apps.lens_bridge.services.gateway_readiness.get_agent_session",
        side_effect=["session-1", "session-2"],
    )
    @patch("apps.lens_bridge.services.gateway_readiness.agent_ws_routable", return_value=True)
    def test_route_change_during_readiness_check_fails_closed(
        self, _mock_routable, _mock_session
    ):
        state = gateway_readiness.gateway_runtime_state(_link())

        self.assertFalse(state["hfl_agent_capabilities_ready"])
        self.assertFalse(state["copilot_eligible"])
