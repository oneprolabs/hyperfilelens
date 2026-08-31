import uuid
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.lens_bridge.services import sl_client
from apps.lens_bridge.services.provisioning import _lensnode_matches_workspace


class SlClientErrorFormatTests(SimpleTestCase):
    def tearDown(self):
        sl_client._ADMIN_ACCESS_TOKEN = None
        sl_client._ADMIN_REFRESH_TOKEN = None
        sl_client._ADMIN_ACCESS_EXPIRES_AT = 0.0
        super().tearDown()

    def test_format_non_field_errors(self):
        body = {"non_field_errors": ["selected_dirs path is not available on LensNode: /x"]}
        self.assertIn("selected_dirs", sl_client._format_sl_error(body))

    def test_format_field_errors(self):
        body = {"name": ["This field is required."]}
        self.assertIn("name", sl_client._format_sl_error(body))

    def test_admin_login_uses_email_credential(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"access": "access", "refresh": "refresh"}
        with (
            patch.object(sl_client.deploy, "lens_bridge_configured", return_value=True),
            patch.object(sl_client.deploy, "lens_base_url", return_value="http://lens"),
            patch.object(
                sl_client.deploy,
                "lens_bridge_email",
                return_value="admin@example.com",
            ),
            patch.object(
                sl_client.deploy,
                "lens_bridge_password",
                return_value="secret",
            ),
            patch.object(
                sl_client.deploy,
                "lens_bridge_legacy_username",
                return_value="",
            ),
            patch.object(sl_client.requests, "post", return_value=response) as post,
        ):
            sl_client._login()

        post.assert_called_once_with(
            "http://lens/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "secret"},
            timeout=30,
        )

    def test_admin_login_falls_back_for_pre_email_sourcelens(self):
        rejected = MagicMock(status_code=400, text="email unsupported")
        accepted = MagicMock(status_code=200)
        accepted.json.return_value = {"access": "access", "refresh": "refresh"}
        with (
            patch.object(sl_client.deploy, "lens_bridge_configured", return_value=True),
            patch.object(sl_client.deploy, "lens_base_url", return_value="http://lens"),
            patch.object(
                sl_client.deploy,
                "lens_bridge_email",
                return_value="admin@example.com",
            ),
            patch.object(
                sl_client.deploy,
                "lens_bridge_legacy_username",
                return_value="admin",
            ),
            patch.object(
                sl_client.deploy,
                "lens_bridge_password",
                return_value="secret",
            ),
            patch.object(
                sl_client.requests,
                "post",
                side_effect=[rejected, accepted],
            ) as post,
        ):
            sl_client._login()

        self.assertEqual(
            post.call_args_list,
            [
                call(
                    "http://lens/api/v1/auth/login",
                    json={"email": "admin@example.com", "password": "secret"},
                    timeout=30,
                ),
                call(
                    "http://lens/api/v1/auth/login",
                    json={"username": "admin", "password": "secret"},
                    timeout=30,
                ),
            ],
        )

    def test_chat_user_login_falls_back_during_mixed_version_upgrade(self):
        rejected = MagicMock(status_code=400, text="email unsupported")
        accepted = MagicMock(status_code=200)
        accepted.json.return_value = {"access": "chat-access"}
        with (
            patch.object(sl_client.deploy, "lens_base_url", return_value="http://lens"),
            patch.object(
                sl_client.requests,
                "post",
                side_effect=[rejected, accepted],
            ) as post,
        ):
            token = sl_client.login_user(
                email="hfl-u-7@users.hyperfilelens.invalid",
                password="secret",
                legacy_username="hfl-u-7",
            )

        self.assertEqual(token, "chat-access")
        self.assertEqual(
            post.call_args_list,
            [
                call(
                    "http://lens/api/v1/auth/login",
                    json={
                        "email": "hfl-u-7@users.hyperfilelens.invalid",
                        "password": "secret",
                    },
                    timeout=30,
                ),
                call(
                    "http://lens/api/v1/auth/login",
                    json={"username": "hfl-u-7", "password": "secret"},
                    timeout=30,
                ),
            ],
        )


class BuildLensEnrollConfigTests(SimpleTestCase):
    @patch(
        "apps.lens_bridge.deploy.local_platform_lens_gateway_base_url",
        return_value="https://127.0.0.1:11443/sourcelens",
    )
    @patch(
        "apps.lens_bridge.deploy.lens_gateway_base_url",
        return_value="https://console.example/sourcelens",
    )
    def test_installer_managed_gateway_gets_local_lens_url(
        self, _public_url, _local_url
    ):
        from apps.lens_bridge.services.provisioning import build_lens_enroll_config
        from apps.node.services.internal.local_platform_gateway import (
            LOCAL_PLATFORM_GATEWAY_METADATA,
        )

        gateway = MagicMock(
            id=7,
            name="platform-gateway",
            metadata=dict(LOCAL_PLATFORM_GATEWAY_METADATA),
        )
        link = MagicMock(
            gateway=gateway,
            config_json={},
            sl_lensnode_uuid=None,
        )
        link.resolved_workspace_root.return_value = "/workspace/platform"

        result = build_lens_enroll_config(link)

        self.assertEqual(
            result["lens_base_url"],
            "https://127.0.0.1:11443/sourcelens",
        )
        self.assertEqual(result["lens_base_path"], "/sourcelens")

    @patch(
        "apps.lens_bridge.deploy.local_platform_lens_gateway_base_url",
        return_value="https://127.0.0.1:11443/sourcelens",
    )
    @patch(
        "apps.lens_bridge.deploy.lens_gateway_base_url",
        return_value="https://console.example/sourcelens",
    )
    def test_unmanaged_gateway_keeps_public_lens_url(self, _public_url, _local_url):
        from apps.lens_bridge.services.provisioning import build_lens_enroll_config

        gateway = MagicMock(id=8, name="user-gateway", metadata={})
        link = MagicMock(
            gateway=gateway,
            config_json={},
            sl_lensnode_uuid=None,
        )
        link.resolved_workspace_root.return_value = "/workspace/user"

        result = build_lens_enroll_config(link)

        self.assertEqual(
            result["lens_base_url"],
            "https://console.example/sourcelens",
        )
        self.assertEqual(result["lens_base_path"], "/sourcelens")


class LensnodeWorkspaceReadinessTests(SimpleTestCase):
    lensnode_uuid = "de240f46-eccd-4e4b-868f-b1f504fbe67b"

    def test_accepts_online_lensnode_at_workspace_root_without_deep_dirs(self):
        data = {
            "uuid": self.lensnode_uuid,
            "status": "online",
            "workspace_path": "/workspace/org-1/",
            "available_dirs": [],
        }
        self.assertTrue(
            _lensnode_matches_workspace(
                data,
                lensnode_uuid=self.lensnode_uuid,
                workspace_root="/workspace/org-1",
            )
        )

    def test_rejects_offline_or_wrong_workspace_lensnode(self):
        base = {
            "uuid": self.lensnode_uuid,
            "status": "offline",
            "workspace_path": "/workspace/org-1",
        }
        self.assertFalse(
            _lensnode_matches_workspace(
                base,
                lensnode_uuid=self.lensnode_uuid,
                workspace_root="/workspace/org-1",
            )
        )
        base["status"] = "online"
        base["workspace_path"] = "/workspace/another-root"
        self.assertFalse(
            _lensnode_matches_workspace(
                base,
                lensnode_uuid=self.lensnode_uuid,
                workspace_root="/workspace/org-1",
            )
        )

    def test_requires_selected_directory_to_be_advertised(self):
        data = {
            "uuid": self.lensnode_uuid,
            "status": "online",
            "workspace_path": "/workspace/org-1",
            "available_dirs": [
                {"path": "/workspace/org-1/hfl-ks-ready"},
            ],
        }
        self.assertTrue(
            _lensnode_matches_workspace(
                data,
                lensnode_uuid=self.lensnode_uuid,
                workspace_root="/workspace/org-1",
                selected_dir="/workspace/org-1/hfl-ks-ready",
            )
        )
        self.assertFalse(
            _lensnode_matches_workspace(
                data,
                lensnode_uuid=self.lensnode_uuid,
                workspace_root="/workspace/org-1",
                selected_dir="/workspace/org-1/nested/hfl-ks-missing",
            )
        )


class SlLensnodeSnapshotTests(SimpleTestCase):
    def test_extracts_display_fields(self):
        from apps.lens_bridge.services.provisioning import _extract_sl_lensnode_snapshot

        snap = _extract_sl_lensnode_snapshot(
            {
                "uuid": "de240f46-eccd-4e4b-868f-b1f504fbe67b",
                "name": "hfl-gw-134-zjb-134",
                "status": "online",
                "workspace_path": "/workspace/org-1",
                "agent_version": "0.1.0",
                "last_heartbeat_at": "2026-07-07T02:54:22.289738Z",
                "registered_at": "2026-07-06T09:16:24.641202Z",
                "tasks": [{"name": "knowledge_qa", "title": "Knowledge Q&A"}],
            }
        )
        self.assertEqual(snap["sl_name"], "hfl-gw-134-zjb-134")
        self.assertEqual(snap["sl_status"], "online")
        self.assertEqual(len(snap["sl_tasks"]), 1)
        self.assertEqual(snap["sl_tasks"][0]["title"], "Knowledge Q&A")


class EnsureKsWorkspaceTests(SimpleTestCase):
    @patch("apps.lens_bridge.services.gateway_execution.context_for_gateway_link")
    @patch("apps.lens_bridge.services.provisioning.wait_for_lensnode_ready")
    @patch("apps.node.services.internal.agent_task.run_agent_task_sync")
    def test_dispatches_prepare_task(self, mock_sync, mock_wait, mock_context):
        from apps.lens_bridge.services import provisioning

        task = MagicMock()
        task.last_error = ""
        mock_sync.return_value = MagicMock(ok=True, task=task)

        org = MagicMock(id=1)
        gateway = MagicMock(id=134)
        link = MagicMock(id=7)
        link.sl_lensnode_uuid = "de240f46-eccd-4e4b-868f-b1f504fbe67b"
        link.resolved_workspace_root.return_value = "/workspace/org-1"
        mock_context.return_value = MagicMock(
            gateway=gateway,
            execution_organization=MagicMock(id=99),
        )
        workspace_binding = MagicMock(
            gateway_link_id=7,
            execution_node_id=134,
            execution_organization_id=99,
            workspace_root="/workspace/org-1",
            workspace_uid="workspace-9",
            knowledge_source_id=9,
            workspace_kind="managed_restore",
        )
        workspace_binding.resolved_path.return_value = "/workspace/org-1/ks-9"

        provisioning.ensure_ks_workspace_on_gateway(
            org=org,
            gateway=gateway,
            gateway_link=link,
            workspace_binding=workspace_binding,
        )

        mock_sync.assert_called_once()
        kwargs = mock_sync.call_args.kwargs
        self.assertEqual(kwargs["kind"], "lens.ks.prepare")
        self.assertEqual(kwargs["payload"]["path"], "/workspace/org-1/ks-9")
        self.assertEqual(kwargs["payload"]["workspace_uid"], "workspace-9")
        self.assertEqual(kwargs["requesting_organization_id"], 1)
        mock_wait.assert_called_once_with(
            lensnode_uuid=link.sl_lensnode_uuid,
            workspace_root="/workspace/org-1",
            selected_dir="/workspace/org-1/ks-9",
        )


class CreateAssistantModelBindingTests(SimpleTestCase):
    def test_analysis_types_are_derived_from_sourcelens_tasks(self):
        from apps.lens_bridge.services import provisioning

        self.assertEqual(
            provisioning.analysis_types_for_tasks(
                [
                    {"name": "knowledge_qa", "title": "Knowledge Q&A"},
                    {"task": "code_analysis", "title": "Code Analysis"},
                    {"name": "general_chat", "title": "General Chat"},
                ]
            ),
            ["knowledge_qa", "code_analysis"],
        )

    def test_gateway_capabilities_reject_an_unavailable_analysis_type(self):
        from apps.lens_bridge.services import provisioning

        gateway_link = MagicMock(
            config_json={
                "sl_lensnode_snapshot": {
                    "sl_tasks": [
                        {"name": "knowledge_qa", "title": "Knowledge Q&A"}
                    ]
                }
            },
            sl_lensnode_uuid=uuid.uuid4(),
        )

        with self.assertRaises(ValidationError) as raised:
            provisioning.validate_analysis_type_for_gateway(
                gateway_link,
                "code_analysis",
            )

        self.assertIn("analysis_type", raised.exception.detail)

    def test_legacy_gateway_without_tasks_keeps_knowledge_qa_default(self):
        from apps.lens_bridge.services import provisioning

        gateway_link = MagicMock(
            config_json={},
            sl_lensnode_uuid=uuid.uuid4(),
        )

        self.assertEqual(
            provisioning.validate_analysis_type_for_gateway(gateway_link, None),
            "knowledge_qa",
        )
        self.assertEqual(
            provisioning.analysis_types_for_gateway(gateway_link),
            ["knowledge_qa"],
        )

    def test_gateway_with_only_general_chat_does_not_use_legacy_fallback(self):
        from apps.lens_bridge.services import provisioning

        gateway_link = MagicMock(
            config_json={
                "sl_lensnode_snapshot": {
                    "sl_tasks": [
                        {"name": "general_chat", "title": "General Chat"}
                    ]
                }
            },
            sl_lensnode_uuid=uuid.uuid4(),
        )

        with self.assertRaises(ValidationError):
            provisioning.validate_analysis_type_for_gateway(
                gateway_link,
                "knowledge_qa",
            )

    @patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_requested_analysis_type_is_resolved_instead_of_first_task(self, request_json):
        from apps.lens_bridge.services import provisioning

        request_json.return_value = {
            "tasks": [
                {"name": "knowledge_qa", "title": "Knowledge Q&A"},
                {"name": "code_analysis", "title": "Code Analysis"},
            ]
        }

        self.assertEqual(
            provisioning.pick_lensnode_task(
                uuid.uuid4(), analysis_type="code_analysis"
            ),
            "code_analysis",
        )

    @patch(
        "apps.lens_bridge.services.provisioning."
        "default_multimodal_model_ref_for_org"
    )
    @patch("apps.lens_bridge.services.provisioning.pick_lensnode_task")
    @patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_freezes_agent_and_multimodal_models_on_assistant(
        self,
        request_json,
        pick_task,
        default_multimodal,
    ):
        from apps.lens_bridge.services import provisioning

        agent_ref = "876742d4-c3b7-4f6c-84a8-a3c0cc8ac38e"
        multimodal_ref = "f658a5ed-8878-4c81-8428-87a3926203ab"
        assistant_uuid = "50b5d33c-7028-4c0b-a3bb-b907db06dfc4"
        request_json.return_value = {"uuid": assistant_uuid}
        pick_task.return_value = "knowledge_qa"
        default_multimodal.return_value = multimodal_ref
        knowledge_source = MagicMock(
            id=42,
            name="Chat documents",
            backup_source_snapshot_id=11,
            backup_snapshot_directory_id=12,
            workspace_path_on_lensnode="/workspace/org-1/ks-42",
            ingest_policy_json={
                "document": True,
                "image": True,
                "embedded_image": True,
                "pdf_render_scanned_pages": True,
                "vision_model_ref": multimodal_ref,
            },
        )
        gateway_link = MagicMock(
            sl_lensnode_uuid="de240f46-eccd-4e4b-868f-b1f504fbe67b"
        )

        result = provisioning.create_sl_assistant_for_ks(
            org=MagicMock(key="tenant-one"),
            ks=knowledge_source,
            gateway_link=gateway_link,
            model_ref=agent_ref,
            multimodal_model_ref=multimodal_ref,
            slug="chat-documents-ks-42",
        )

        self.assertEqual(str(result), assistant_uuid)
        payload = request_json.call_args.kwargs["json_body"]
        self.assertEqual(payload["agent_model_ref"], agent_ref)
        self.assertEqual(payload["agent_rounds"], "balanced")
        self.assertEqual(
            payload["multimodal_model_ref"],
            multimodal_ref,
        )
        self.assertEqual(
            payload["settings"]["ingestion"]["conversion"][
                "vision_model_ref"
            ],
            multimodal_ref,
        )
        self.assertEqual(
            payload["settings"]["retrieval_policy"],
            {
                "include_hidden": True,
                "exclude_dirs": [],
                "exclude_extensions": [],
            },
        )

    def test_analysis_mode_maps_to_source_lens_agent_rounds(self):
        from apps.lens_bridge.services import provisioning

        self.assertEqual(provisioning.agent_rounds_for_analysis_mode("fast"), "fast")
        self.assertEqual(
            provisioning.agent_rounds_for_analysis_mode("standard"),
            "balanced",
        )
        self.assertEqual(provisioning.agent_rounds_for_analysis_mode("deep"), "deep")
        self.assertEqual(
            provisioning.agent_rounds_for_analysis_mode("unsupported"),
            "balanced",
        )

    @patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_analysis_type_updates_source_lens_selected_task(self, request_json):
        from apps.lens_bridge.services import provisioning

        knowledge_source = MagicMock(sl_assistant_uuid="assistant-uuid")
        assistant_uuid = uuid.uuid4()

        provisioning.sync_assistant_execution_config(
            ks=knowledge_source,
            analysis_type="code_analysis",
            assistant_uuid=assistant_uuid,
        )

        request_json.assert_called_once_with(
            "PATCH",
            f"/api/lens/assistants/{assistant_uuid}/",
            json_body={"selected_task": "code_analysis"},
        )


class UpdateAssistantRetrievalPolicyTests(SimpleTestCase):
    @patch("apps.lens_bridge.services.provisioning._assistant_is_chat_managed")
    @patch("apps.lens_bridge.services.provisioning.indexed_dirs_for_ks")
    @patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_chat_sync_forces_include_hidden_without_excludes(
        self,
        request_json,
        indexed_dirs,
        is_chat_managed,
    ):
        from apps.lens_bridge.services import provisioning

        is_chat_managed.return_value = True
        indexed_dirs.return_value = [{"path": "/workspace/org-1/ks-42"}]
        request_json.side_effect = [
            {
                "settings": {
                    "retrieval_policy": {
                        "include_hidden": False,
                        "exclude_dirs": [".git"],
                        "exclude_extensions": [".lock"],
                    }
                }
            },
            {"uuid": "50b5d33c-7028-4c0b-a3bb-b907db06dfc4"},
        ]
        knowledge_source = MagicMock(
            sl_assistant_uuid="50b5d33c-7028-4c0b-a3bb-b907db06dfc4",
            ingest_policy_json={"document": True},
        )
        gateway_link = MagicMock(
            sl_lensnode_uuid="de240f46-eccd-4e4b-868f-b1f504fbe67b"
        )

        provisioning.update_sl_assistant_for_ks(
            org=MagicMock(key="tenant-one"),
            ks=knowledge_source,
            gateway_link=gateway_link,
        )

        patch_body = request_json.call_args_list[1].kwargs["json_body"]
        self.assertEqual(
            patch_body["settings"]["retrieval_policy"],
            {
                "include_hidden": True,
                "exclude_dirs": [],
                "exclude_extensions": [],
            },
        )

    @patch("apps.lens_bridge.services.provisioning._assistant_is_chat_managed")
    @patch("apps.lens_bridge.services.provisioning.indexed_dirs_for_ks")
    @patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_manual_sync_preserves_operator_retrieval_policy(
        self,
        request_json,
        indexed_dirs,
        is_chat_managed,
    ):
        from apps.lens_bridge.services import provisioning

        is_chat_managed.return_value = False
        indexed_dirs.return_value = [{"path": "/workspace/org-1/ks-42"}]
        existing_policy = {
            "include_hidden": False,
            "exclude_dirs": [".git", "node_modules"],
            "exclude_extensions": [".lock"],
        }
        request_json.side_effect = [
            {"settings": {"retrieval_policy": existing_policy}},
            {"uuid": "50b5d33c-7028-4c0b-a3bb-b907db06dfc4"},
        ]
        knowledge_source = MagicMock(
            sl_assistant_uuid="50b5d33c-7028-4c0b-a3bb-b907db06dfc4",
            ingest_policy_json={"document": True},
        )
        gateway_link = MagicMock(
            sl_lensnode_uuid="de240f46-eccd-4e4b-868f-b1f504fbe67b"
        )

        provisioning.update_sl_assistant_for_ks(
            org=MagicMock(key="tenant-one"),
            ks=knowledge_source,
            gateway_link=gateway_link,
        )

        patch_body = request_json.call_args_list[1].kwargs["json_body"]
        self.assertEqual(
            patch_body["settings"]["retrieval_policy"],
            existing_policy,
        )


class BrowseGatewayDirectoryTests(SimpleTestCase):
    @patch("apps.node.services.interface.run_agent_task_sync")
    @patch("apps.lens_bridge.services.provisioning.get_gateway_link")
    @patch("apps.lens_bridge.services.provisioning.require_gateway_node")
    def test_dispatches_restricted_gateway_browse(
        self,
        require_gateway,
        get_gateway_link,
        run_agent_task,
    ):
        from apps.lens_bridge.services.provisioning import browse_gateway_directory

        gateway = MagicMock(id=7, status="active", availability="online")
        require_gateway.return_value = gateway
        link = MagicMock()
        link.resolved_workspace_root.return_value = "/workspace/org-1/data"
        get_gateway_link.return_value = link
        run_agent_task.return_value = MagicMock(
            ok=True,
            timed_out=False,
            result={
                "path": "/workspace/org-1/data/documents",
                "entries": [
                    {
                        "name": "reports",
                        "path": "/workspace/org-1/data/documents/reports",
                        "is_dir": True,
                    },
                    {
                        "name": "escape",
                        "path": "/etc",
                        "is_dir": True,
                    },
                ],
            },
        )

        result = browse_gateway_directory(
            org=MagicMock(id=1),
            gateway_id=7,
            path="/workspace/org-1/data/documents",
        )

        kwargs = run_agent_task.call_args.kwargs
        self.assertEqual(kwargs["kind"], "lens.gateway.browse")
        self.assertEqual(
            kwargs["payload"]["allowed_root"],
            "/workspace/org-1/data",
        )
        self.assertEqual(
            [entry["path"] for entry in result["entries"]],
            ["/workspace/org-1/data/documents/reports"],
        )

    @patch("apps.lens_bridge.services.provisioning.get_gateway_link")
    @patch("apps.lens_bridge.services.provisioning.require_gateway_node")
    def test_rejects_traversal_without_dispatching(
        self,
        require_gateway,
        get_gateway_link,
    ):
        from apps.lens_bridge.services.provisioning import browse_gateway_directory

        require_gateway.return_value = MagicMock(id=7, status="online")
        link = MagicMock()
        link.resolved_workspace_root.return_value = "/workspace/org-1/data"
        get_gateway_link.return_value = link

        with self.assertRaises(ValidationError):
            browse_gateway_directory(
                org=MagicMock(id=1),
                gateway_id=7,
                path="/workspace/org-1/data/../../etc",
            )
