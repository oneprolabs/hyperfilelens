"""Admin can bind platform Skills/MCP onto tenant Assistants."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.services.assistants import _validate_assistant_tool_bindings


class PlatformAssistantToolBindingTests(SimpleTestCase):
    def setUp(self) -> None:
        self.org = MagicMock(spec=Organization)
        self.org.id = 42
        self.user = MagicMock(spec=User)
        self.skill_uuid = uuid.uuid4()
        self.mcp_uuid = uuid.uuid4()

    def test_tenant_save_still_requires_org_links(self) -> None:
        with patch(
            "apps.lens_bridge.services.org_skills.validate_org_skill_uuids",
            side_effect=ValidationError({"skill_bindings": "missing"}),
        ) as validate_skills:
            with self.assertRaises(ValidationError):
                _validate_assistant_tool_bindings(
                    self.org,
                    {
                        "skill_bindings": [{"skill_uuid": str(self.skill_uuid)}],
                        "mcp_bindings": [],
                    },
                    platform_passthrough=False,
                )
        validate_skills.assert_called_once()

    @patch("apps.lens_bridge.services.skills.get_skill")
    @patch("apps.lens_bridge.services.org_skills.register_org_skill")
    @patch("apps.lens_bridge.services.mcp_servers.get_mcp_server")
    @patch("apps.lens_bridge.services.org_mcp_servers.register_org_mcp")
    def test_admin_save_registers_platform_catalog_bindings(
        self,
        register_mcp,
        get_mcp,
        register_skill,
        get_skill,
    ) -> None:
        get_skill.return_value = {"uuid": str(self.skill_uuid)}
        get_mcp.return_value = {"uuid": str(self.mcp_uuid)}

        _validate_assistant_tool_bindings(
            self.org,
            {
                "skill_bindings": [{"skill_uuid": str(self.skill_uuid)}],
                "mcp_bindings": [{"mcp_uuid": str(self.mcp_uuid)}],
            },
            platform_passthrough=True,
            created_by=self.user,
        )

        get_skill.assert_called_once_with(self.skill_uuid)
        register_skill.assert_called_once_with(
            org=self.org,
            sl_skill_uuid=self.skill_uuid,
            created_by=self.user,
        )
        get_mcp.assert_called_once_with(self.mcp_uuid)
        register_mcp.assert_called_once_with(
            org=self.org,
            sl_mcp_uuid=self.mcp_uuid,
            created_by=self.user,
        )
