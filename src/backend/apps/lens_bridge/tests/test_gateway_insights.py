from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.lens_bridge.services import gateway_insights


class GatewayInsightDirectoryTests(SimpleTestCase):
    @patch("apps.lens_bridge.services.gateway_insights._serialize_row")
    @patch("apps.lens_bridge.services.gateway_insights._link_index")
    @patch("apps.lens_bridge.services.gateway_insights._lensnode_rows")
    def test_admin_directory_includes_unmanaged_sl_nodes(
        self,
        mock_lensnodes,
        mock_link_index,
        mock_serialize,
    ):
        mock_lensnodes.return_value = [{"uuid": "sl-user"}, {"uuid": "sl-external"}]
        link = MagicMock()
        mock_link_index.return_value = {"sl-user": link}
        mock_serialize.side_effect = [{"name": "user"}, {"name": "external"}]

        rows = gateway_insights.list_admin_gateway_insight_rows(user=MagicMock())

        self.assertEqual(rows, [{"name": "user"}, {"name": "external"}])
        self.assertEqual(mock_serialize.call_count, 2)
        self.assertIs(mock_serialize.call_args_list[0].kwargs["link"], link)
        self.assertIsNone(mock_serialize.call_args_list[1].kwargs["link"])

    @patch("apps.lens_bridge.services.gateway_insights._serialize_row")
    @patch("apps.lens_bridge.services.gateway_insights._link_index")
    @patch("apps.lens_bridge.services.gateway_insights._lensnode_rows")
    def test_organization_directory_only_returns_organization_mappings(
        self,
        mock_lensnodes,
        mock_link_index,
        mock_serialize,
    ):
        user = MagicMock()
        organization = MagicMock()
        link = MagicMock()
        mock_lensnodes.return_value = [{"uuid": "owned"}, {"uuid": "other"}]
        mock_link_index.return_value = {"owned": link}
        mock_serialize.return_value = {"name": "owned"}

        link.gateway_id = 11
        with patch("apps.lens_bridge.services.gateway_insights.gateway_ownership.organization_gateway_links") as links, patch("apps.lens_bridge.services.gateway_insights._filter_user_gateways") as filtered:
            filtered.return_value.values_list.return_value = [11]
            rows = gateway_insights.list_organization_gateway_insight_rows(
                organization=organization, user=user,
            )
            links.assert_called_once_with(organization=organization)

        self.assertEqual(rows, [{"name": "owned"}])
        mock_link_index.assert_called_once_with(organization=organization)
        mock_serialize.assert_called_once()
