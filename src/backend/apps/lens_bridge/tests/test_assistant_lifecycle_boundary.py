import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensAssistantLink,
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
)
from apps.lens_bridge.services import assistants
from apps.node.models import Node
from apps.node.models.base import NodeRole


class AssistantLifecycleBoundaryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="assistant-lifecycle",
            name="Assistant lifecycle",
        )
        self.user = get_user_model().objects.create_user(
            username="assistant-owner@example.test",
            email="assistant-owner@example.test",
        )
        self.gateway = Node.objects.create(
            organization=self.org,
            name="Assistant gateway",
            role=NodeRole.GATEWAY,
        )
        self.gateway_link = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=self.gateway,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        self.knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.org,
            name="Chat source",
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            source_path="/workspace/chat-source",
            created_by=self.user,
        )
        self.assistant_uuid = uuid.uuid4()
        self.assistant_link = LensAssistantLink.objects.create(
            organization=self.org,
            sl_assistant_uuid=self.assistant_uuid,
            knowledge_source=self.knowledge_source,
            owner_user=self.user,
            created_by=self.user,
            visibility_scope=LensAssistantLink.VisibilityScope.USER,
            lifecycle_owner=LensAssistantLink.LifecycleOwner.CHAT,
        )
        LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            gateway_link=self.gateway_link,
            knowledge_source=self.knowledge_source,
            sl_assistant_uuid=self.assistant_uuid,
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )

    def _create_private_source_for_other_user(self):
        other_user = get_user_model().objects.create_user(
            username="other-assistant-owner@example.test",
            email="other-assistant-owner@example.test",
        )
        other_gateway = Node.objects.create(
            organization=self.org,
            name="Other Assistant gateway",
            role=NodeRole.GATEWAY,
        )
        other_link = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=other_gateway,
            owner_user=other_user,
            scope=LensGatewayLink.GatewayScope.USER,
            sl_lensnode_uuid=uuid.uuid4(),
        )
        other_source = LensKnowledgeSource.objects.create(
            organization=self.org,
            name="Other private source",
            gateway=other_gateway,
            gateway_link=other_link,
            source_path="/workspace/other-private-source",
            created_by=other_user,
        )
        return other_user, other_link, other_source

    @mock.patch("apps.lens_bridge.services.assistants.sl_client.request_json")
    def test_tenant_assistant_rejects_raw_execution_identity(self, request_json):
        with self.assertRaises(ValidationError):
            assistants.create_org_assistant(
                self.org,
                {
                    "name": "Raw execution bypass",
                    "lensnode_uuid": str(uuid.uuid4()),
                    "selected_task": "document_review",
                    "selected_dirs": [{"path": "/workspace/other-user"}],
                },
                user=self.user,
            )

        request_json.assert_not_called()

    @mock.patch("apps.lens_bridge.services.assistants.sl_client.request_json")
    def test_tenant_assistant_rejects_another_users_knowledge_source(
        self,
        request_json,
    ):
        _other_user, _other_link, other_source = (
            self._create_private_source_for_other_user()
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Knowledge source is not owned by the current user.",
        ):
            assistants.create_org_assistant(
                self.org,
                {
                    "name": "Owner bypass",
                    "knowledge_source_id": other_source.id,
                    "selected_task": "document_review",
                },
                user=self.user,
            )

        request_json.assert_not_called()

    @mock.patch(
        "apps.lens_bridge.services.provisioning.sl_lensnode_snapshot_from_link",
        return_value={},
    )
    @mock.patch(
        "apps.lens_bridge.services.org_mcp_servers.list_org_mcp_servers",
        return_value=[],
    )
    @mock.patch(
        "apps.lens_bridge.services.org_skills.list_org_skills",
        return_value=[],
    )
    @mock.patch("apps.lens_bridge.services.assistants.sl_client.request_json")
    def test_tenant_form_options_share_gateways_but_not_knowledge_sources(
        self,
        request_json,
        _list_skills,
        _list_mcps,
        _lensnode_snapshot,
    ):
        self.gateway_link.sl_lensnode_uuid = uuid.uuid4()
        self.gateway_link.save(update_fields=["sl_lensnode_uuid", "updated_at"])
        _other_user, other_link, other_source = (
            self._create_private_source_for_other_user()
        )
        request_json.return_value = [
            {"uuid": str(self.gateway_link.sl_lensnode_uuid), "name": "Mine"},
            {"uuid": str(other_link.sl_lensnode_uuid), "name": "Other"},
        ]

        options = assistants.assistant_form_options(self.org, user=self.user)

        self.assertEqual(
            [row["gateway_id"] for row in options["gateways"]],
            [self.gateway.id, other_link.gateway_id],
        )
        self.assertEqual(
            [row["uuid"] for row in options["lensnodes"]],
            [
                str(self.gateway_link.sl_lensnode_uuid),
                str(other_link.sl_lensnode_uuid),
            ],
        )
        self.assertEqual(
            [row["id"] for row in options["knowledge_sources"]],
            [self.knowledge_source.id],
        )
        self.assertNotIn(
            other_source.id,
            [row["id"] for row in options["knowledge_sources"]],
        )

    @mock.patch(
        "apps.lens_bridge.services.org_skills.sync_assistant_skill_links"
    )
    @mock.patch(
        "apps.lens_bridge.services.assistants._knowledge_source_execution",
        return_value={
            "lensnode_uuid": "37941d34-a8bf-49d7-bfab-f8e61a350645",
            "selected_dirs": [{"path": "/workspace/manual-source"}],
        },
    )
    @mock.patch("apps.lens_bridge.services.assistants.sl_client.request_json")
    def test_tenant_assistant_rebind_updates_hfl_knowledge_source_link(
        self,
        request_json,
        _knowledge_source_execution,
        _sync_skills,
    ):
        self.assistant_link.lifecycle_owner = LensAssistantLink.LifecycleOwner.MANUAL
        self.assistant_link.save(update_fields=["lifecycle_owner", "updated_at"])
        LensSessionLink.objects.all().delete()
        replacement_source = LensKnowledgeSource.objects.create(
            organization=self.org,
            name="Manual source",
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            source_path="/workspace/manual-source",
            created_by=self.user,
        )
        prefix = assistants._org_prefix(self.org)
        request_json.return_value = {
            "uuid": str(self.assistant_uuid),
            "slug": f"{prefix}-manual",
            "name": "Manual",
        }

        assistants.update_org_assistant(
            self.org,
            self.assistant_uuid,
            {"knowledge_source_id": replacement_source.id},
            user=self.user,
        )

        self.assistant_link.refresh_from_db()
        self.assertEqual(
            self.assistant_link.knowledge_source_id,
            replacement_source.id,
        )

    @mock.patch("apps.lens_bridge.services.assistants._delete_sl_assistant")
    @mock.patch("apps.lens_bridge.services.assistants.get_org_assistant")
    def test_chat_assistant_cannot_be_deleted_directly(
        self,
        get_assistant,
        delete_remote,
    ):
        get_assistant.return_value = {"uuid": str(self.assistant_uuid)}

        with self.assertRaises(ValidationError):
            assistants.delete_org_assistant(
                self.org,
                self.assistant_uuid,
                user=self.user,
                can_manage_all=True,
            )

        delete_remote.assert_not_called()

    @mock.patch("apps.lens_bridge.services.assistants.sl_client.request_json")
    @mock.patch("apps.lens_bridge.services.assistants.get_org_assistant")
    def test_chat_assistant_cannot_be_updated_directly(
        self,
        get_assistant,
        request_json,
    ):
        get_assistant.return_value = {"uuid": str(self.assistant_uuid)}

        with self.assertRaises(ValidationError):
            assistants.update_org_assistant(
                self.org,
                self.assistant_uuid,
                {"name": "Bypass update"},
                user=self.user,
                can_manage_all=True,
            )

        request_json.assert_not_called()

    @mock.patch("apps.lens_bridge.services.assistants.sl_client.request_json")
    def test_chat_knowledge_source_cannot_receive_manual_assistant(
        self,
        request_json,
    ):
        with self.assertRaises(ValidationError):
            assistants.create_org_assistant(
                self.org,
                {
                    "name": "Bypass assistant",
                    "knowledge_source_id": self.knowledge_source.id,
                },
                user=self.user,
            )

        request_json.assert_not_called()

    @mock.patch("apps.lens_bridge.services.assistants.sl_client.request_json")
    def test_org_manager_can_update_another_users_manual_assistant(
        self,
        request_json,
    ):
        self.assistant_link.lifecycle_owner = (
            LensAssistantLink.LifecycleOwner.MANUAL
        )
        self.assistant_link.save(update_fields=["lifecycle_owner", "updated_at"])
        LensSessionLink.objects.all().delete()
        prefix = assistants._org_prefix(self.org)

        def response_for(method, _path, **_kwargs):
            if method == "GET":
                return {
                    "uuid": str(self.assistant_uuid),
                    "slug": f"{prefix}-manual",
                    "name": "Manual",
                }
            return {
                "uuid": str(self.assistant_uuid),
                "slug": f"{prefix}-manual",
                "name": "Updated",
            }

        request_json.side_effect = response_for
        manager = get_user_model().objects.create_user(
            username="assistant-manager@example.test",
            email="assistant-manager@example.test",
        )
        with mock.patch(
            "apps.lens_bridge.services.org_skills.sync_assistant_skill_links"
        ):
            result = assistants.update_org_assistant(
                self.org,
                self.assistant_uuid,
                {"name": "Updated"},
                user=manager,
                can_manage_all=True,
            )

        self.assertEqual(result["name"], "Updated")
