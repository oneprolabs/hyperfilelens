import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.exceptions import NotFound
from rest_framework.test import APIClient

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.api.serializers import (
    LensRunCreateSerializer,
    LensRunFeedbackSerializer,
    LensSessionCreateSerializer,
    LensSessionLinkSerializer,
    LensSessionUpdateSerializer,
)
from apps.lens_bridge.api.views import (
    _attachment_proxy_url,
    _output_file_proxy_url,
    _require_attachment_proxy_token,
    _rewrite_attachment_urls,
    _source_lens_session_meta,
)
from apps.lens_bridge.models import (
    LensRunSubmission,
    LensSessionLink,
    LensSlUserLink,
    LensUsageLedger,
)
from apps.lens_bridge.services import sl_client


class LensSessionCreateSerializerTests(SimpleTestCase):
    def _payload(self, **overrides):
        payload = {
            "idempotency_key": "session-create-test",
            "backup_config_id": 1,
            "backup_source_snapshot_id": 1,
            "source_scopes": [
                {
                    "source_path": "/documents",
                    "backup_snapshot_directory_id": 1,
                }
            ],
        }
        payload.update(overrides)
        return payload

    def test_private_gateway_option_requires_a_gateway(self):
        serializer = LensSessionCreateSerializer(
            data=self._payload(gateway_mode=LensSessionLink.GatewaySelectionMode.MANUAL)
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            str(serializer.errors["gateway_link_id"][0]),
            "Select a Private Data Gateway.",
        )

    def test_create_requires_an_idempotency_key(self):
        serializer = LensSessionCreateSerializer(
            data=self._payload(idempotency_key=None)
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("idempotency_key", serializer.errors)

    def test_public_gateway_option_rejects_a_specific_gateway(self):
        serializer = LensSessionCreateSerializer(
            data=self._payload(
                gateway_mode=LensSessionLink.GatewaySelectionMode.AUTO,
                gateway_link_id=7,
            )
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            str(serializer.errors["gateway_link_id"][0]),
            (
                "Do not select a specific Data Gateway when using the Public "
                "Data Gateway option."
            ),
        )

    def test_create_accepts_chat_execution_options(self):
        model_ref = uuid.uuid4()
        serializer = LensSessionCreateSerializer(
            data=self._payload(
                analysis_mode=LensSessionLink.AnalysisMode.DEEP,
                agent_model_ref=str(model_ref),
            )
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["analysis_mode"],
            LensSessionLink.AnalysisMode.DEEP,
        )
        self.assertEqual(serializer.validated_data["agent_model_ref"], model_ref)

    def test_create_accepts_analysis_type(self):
        serializer = LensSessionCreateSerializer(
            data=self._payload(analysis_type=LensSessionLink.AnalysisType.CODE_ANALYSIS)
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["analysis_type"],
            LensSessionLink.AnalysisType.CODE_ANALYSIS,
        )

    def test_create_rejects_unknown_analysis_type(self):
        serializer = LensSessionCreateSerializer(
            data=self._payload(analysis_type="general_chat")
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("analysis_type", serializer.errors)

    def test_execution_update_accepts_analysis_type(self):
        serializer = LensSessionUpdateSerializer(
            data={"analysis_type": LensSessionLink.AnalysisType.CODE_ANALYSIS}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["analysis_type"],
            LensSessionLink.AnalysisType.CODE_ANALYSIS,
        )

    def test_gateway_options_expose_supported_analysis_types(self):
        from apps.lens_bridge.api.serializers import LensCopilotGatewayOptionSerializer

        serializer = LensCopilotGatewayOptionSerializer(
            data={
                "gateway_link_id": 1,
                "gateway_id": 2,
                "name": "gateway",
                "scope": "platform",
                "is_platform_default": True,
                "sidecar_status": "online",
                "online": True,
                "hfl_usable": True,
                "copilot_eligible": True,
                "analysis_types": ["knowledge_qa", "code_analysis"],
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["analysis_types"],
            ["knowledge_qa", "code_analysis"],
        )

    def test_create_rejects_unknown_analysis_mode(self):
        serializer = LensSessionCreateSerializer(
            data=self._payload(analysis_mode="max")
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("analysis_mode", serializer.errors)

    def test_run_accepts_an_attachment_without_question_text(self):
        attachment_uuid = uuid.uuid4()
        serializer = LensRunCreateSerializer(
            data={"question": "", "attachment_uuids": [str(attachment_uuid)]}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["attachment_uuids"],
            [attachment_uuid],
        )

    def test_run_requires_text_or_an_attachment(self):
        serializer = LensRunCreateSerializer(data={"question": ""})

        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

    def test_run_rejects_more_than_four_attachments(self):
        serializer = LensRunCreateSerializer(
            data={
                "attachment_uuids": [str(uuid.uuid4()) for _ in range(5)],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("attachment_uuids", serializer.errors)

    def test_run_rejects_duplicate_attachment_uuids(self):
        attachment_uuid = str(uuid.uuid4())
        serializer = LensRunCreateSerializer(
            data={
                "attachment_uuids": [attachment_uuid, attachment_uuid],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("attachment_uuids", serializer.errors)

    def test_run_accepts_a_retry_reference(self):
        retry_run_uuid = uuid.uuid4()
        serializer = LensRunCreateSerializer(
            data={
                "question": "Try again",
                "retry_of_run_uuid": str(retry_run_uuid),
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["retry_of_run_uuid"],
            retry_run_uuid,
        )

    def test_run_feedback_accepts_supported_values_and_clear(self):
        for feedback in ("positive", "negative", ""):
            serializer = LensRunFeedbackSerializer(data={"feedback": feedback})

            self.assertTrue(serializer.is_valid(), serializer.errors)
            self.assertEqual(serializer.validated_data["feedback"], feedback)

    def test_run_feedback_rejects_unknown_value(self):
        serializer = LensRunFeedbackSerializer(data={"feedback": "helpful"})

        self.assertFalse(serializer.is_valid())
        self.assertIn("feedback", serializer.errors)

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_session_metadata_follows_sourcelens_pagination(
        self,
        request_json,
    ):
        first_uuid = str(uuid.uuid4())
        second_uuid = str(uuid.uuid4())
        request_json.side_effect = [
            {
                "results": [{"uuid": first_uuid, "pinned_at": None}],
                "next": "http://sourcelens/api/lens/sessions/?page=2",
            },
            {
                "results": [
                    {"uuid": second_uuid, "pinned_at": "2026-08-14T08:00:00Z"}
                ],
                "next": None,
            },
        ]
        user = Mock()

        result = _source_lens_session_meta(
            hfl_user=user,
            session_uuids={first_uuid, second_uuid},
        )

        self.assertEqual(set(result), {first_uuid, second_uuid})
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual(
            request_json.call_args_list[1].kwargs["params"],
            {"page": 2, "page_size": 500},
        )

    def test_attachment_token_cannot_be_reused_for_another_uuid(self):
        attachment_uuid = uuid.uuid4()
        signed_url = _attachment_proxy_url(17, str(attachment_uuid))
        token = parse_qs(urlsplit(signed_url).query)["token"][0]
        request = SimpleNamespace(query_params={"token": token})

        with self.assertRaises(NotFound):
            _require_attachment_proxy_token(
                request,
                session_id=17,
                attachment_uuid=uuid.uuid4(),
            )

        with self.assertRaises(NotFound):
            _require_attachment_proxy_token(
                request,
                session_id=18,
                attachment_uuid=attachment_uuid,
            )

    def test_session_serializer_does_not_invent_an_unpinned_state(self):
        self.assertNotIn("pinned_at", LensSessionLinkSerializer().fields)

    def test_submission_attachment_field_accepts_mixed_version_writes(self):
        field = LensRunSubmission._meta.get_field("attachment_uuids")

        self.assertTrue(field.null)


class CopilotSessionApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="copilot-session-owner",
            email="copilot-session-owner@example.com",
            password="test-password",
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        self.session = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            title="Preparing Chat",
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_phase=LensSessionLink.ProvisionPhase.QUEUED,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _mark_session_ready(self):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.READY
        self.session.provision_phase = LensSessionLink.ProvisionPhase.READY
        self.session.sl_session_uuid = uuid.uuid4()
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_phase",
                "sl_session_uuid",
                "updated_at",
            ]
        )

    def test_sync_resolves_the_current_users_session(self):
        response = self.client.get(
            reverse(
                "lens-copilot-session-sync",
                kwargs={"pk": self.session.pk},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        payload = payload.get("data", payload)
        self.assertEqual(payload["session_id"], self.session.pk)
        self.assertEqual(
            payload["lifecycle_status"],
            LensSessionLink.LifecycleStatus.PROVISIONING,
        )
        self.assertEqual(payload["run_outcomes"], [])
        self.assertEqual(payload["response_state"]["status"], "idle")

    def test_sync_returns_structured_gateway_capacity_failure(self):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.lifecycle_error = "internal capacity diagnostic"
        self.session.lifecycle_error_state_json = {
            "code": "SUBSCRIPTION.QUOTA_EXCEEDED",
            "message": "This Public Data Gateway is at capacity.",
            "retryable": True,
            "meta": {
                "quota_type": "gateway.public_capacity_bytes",
                "scope": "gateway",
                "limit": 10 * 1024**2,
                "used": 11_890_256,
            },
        }
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "lifecycle_error",
                "lifecycle_error_state_json",
                "updated_at",
            ]
        )

        response = self.client.get(
            reverse(
                "lens-copilot-session-sync",
                kwargs={"pk": self.session.pk},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json().get("data", response.json())
        self.assertEqual(
            payload["lifecycle_error_code"],
            "SUBSCRIPTION.QUOTA_EXCEEDED",
        )
        self.assertEqual(payload["lifecycle_error_meta"]["scope"], "gateway")
        self.assertNotIn("limit", payload["lifecycle_error_meta"])
        self.assertNotIn("used", payload["lifecycle_error_meta"])
        self.assertNotIn(
            "internal capacity diagnostic",
            payload["lifecycle_error_message"],
        )
        self.assertEqual(
            payload["lifecycle_error"],
            payload["lifecycle_error_message"],
        )
        serializer_payload = LensSessionLinkSerializer(self.session).data
        self.assertEqual(
            serializer_payload["lifecycle_error"],
            serializer_payload["lifecycle_error_message"],
        )
        self.assertNotIn(
            "internal capacity diagnostic",
            serializer_payload["lifecycle_error"],
        )

    def test_not_ready_actions_never_return_raw_lifecycle_diagnostics(self):
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        self.session.lifecycle_error = "private repository path /srv/internal/config"
        self.session.lifecycle_error_state_json = {}
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "lifecycle_error",
                "lifecycle_error_state_json",
                "updated_at",
            ]
        )

        response = self.client.post(
            reverse(
                "lens-copilot-session-pin",
                kwargs={"pk": self.session.pk},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("/srv/internal/config", str(response.json()))
        self.assertIn("Chat preparation failed", str(response.json()))

    @patch(
        "apps.lens_bridge.api.views.copilot_service.list_copilot_assistants",
        return_value=[],
    )
    @patch(
        "apps.lens_bridge.api.views._source_lens_session_meta",
        side_effect=sl_client.LensBridgeUnavailable(),
    )
    def test_list_omits_pinned_state_when_sourcelens_is_unavailable(
        self,
        _session_meta,
        _assistants,
    ):
        response = self.client.get(
            reverse("lens-copilot-session-list"),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json().get("data", response.json())
        self.assertEqual(len(payload), 1)
        self.assertNotIn("pinned_at", payload[0])

    @patch(
        "apps.lens_bridge.api.views.copilot_service.list_copilot_assistants",
        return_value=[],
    )
    @patch(
        "apps.lens_bridge.api.views._source_lens_session_meta",
        return_value={},
    )
    def test_list_stays_ordered_by_creation_when_an_older_chat_gets_an_answer(
        self,
        _session_meta,
        _assistants,
    ):
        newer = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            title="Newer Chat",
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
            sl_session_uuid=uuid.uuid4(),
        )
        older_created_at = datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc)
        newer_created_at = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
        LensSessionLink.objects.filter(pk=self.session.pk).update(
            created_at=older_created_at,
            last_message_at=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        )
        LensSessionLink.objects.filter(pk=newer.pk).update(
            created_at=newer_created_at,
            last_message_at=datetime(2026, 8, 20, 8, 1, tzinfo=timezone.utc),
        )

        response = self.client.get(
            reverse("lens-copilot-session-list"),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json().get("data", response.json())
        self.assertEqual(
            [row["id"] for row in payload],
            [newer.id, self.session.id],
        )

    @patch(
        "apps.lens_bridge.api.views.copilot_service.list_copilot_assistants",
        return_value=[],
    )
    @patch("apps.lens_bridge.api.views._source_lens_session_meta")
    def test_list_places_explicitly_pinned_chats_before_creation_order(
        self,
        session_meta,
        _assistants,
    ):
        self._mark_session_ready()
        newer = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            title="Newer Chat",
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
            sl_session_uuid=uuid.uuid4(),
        )
        session_meta.return_value = {
            str(self.session.sl_session_uuid): {
                "uuid": str(self.session.sl_session_uuid),
                "pinned_at": "2026-08-20T10:00:00Z",
            },
            str(newer.sl_session_uuid): {
                "uuid": str(newer.sl_session_uuid),
                "pinned_at": None,
            },
        }

        response = self.client.get(
            reverse("lens-copilot-session-list"),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json().get("data", response.json())
        self.assertEqual(payload[0]["id"], self.session.id)
        self.assertEqual(payload[0]["pinned_at"], "2026-08-20T10:00:00Z")

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_pin_uses_sourcelens_as_the_authoritative_state(self, request_json):
        self._mark_session_ready()
        pinned_at = "2026-08-14T08:00:00Z"
        request_json.return_value = {
            "uuid": str(self.session.sl_session_uuid),
            "pinned_at": pinned_at,
        }

        response = self.client.post(
            reverse(
                "lens-copilot-session-pin",
                kwargs={"pk": self.session.pk},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json().get("data", response.json())
        self.assertEqual(payload["pinned_at"], pinned_at)
        request_json.assert_called_once_with(
            "POST",
            f"/api/lens/sessions/{self.session.sl_session_uuid}/pin/",
            hfl_user=self.user,
        )

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_pin_rejects_an_invalid_sourcelens_response(self, request_json):
        self._mark_session_ready()
        request_json.return_value = {"pinned_at": None}

        response = self.client.post(
            reverse(
                "lens-copilot-session-pin",
                kwargs={"pk": self.session.pk},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("invalid session pin response", str(response.json()))

    @patch("apps.lens_bridge.api.views.sl_client.request_multipart")
    def test_attachment_upload_is_forwarded_to_the_mapped_session(
        self,
        request_multipart,
    ):
        self._mark_session_ready()
        attachment_uuid = uuid.uuid4()
        request_multipart.return_value = {
            "uuid": str(attachment_uuid),
            "kind": "document",
            "original_name": "report.pdf",
        }
        uploaded = SimpleUploadedFile(
            "report.pdf",
            b"pdf-bytes",
            content_type="application/pdf",
        )

        response = self.client.post(
            reverse(
                "lens-copilot-session-upload-attachment",
                kwargs={"pk": self.session.pk},
            ),
            {"file": uploaded},
            format="multipart",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json().get("data", response.json())
        self.assertEqual(payload["uuid"], str(attachment_uuid))
        self.assertIn(
            f"/sessions/{self.session.pk}/attachments/{attachment_uuid}/",
            payload["url"],
        )
        request_multipart.assert_called_once()
        self.assertEqual(
            request_multipart.call_args.args[0],
            f"/api/lens/sessions/{self.session.sl_session_uuid}/attachments/",
        )
        self.assertIs(request_multipart.call_args.kwargs["hfl_user"], self.user)

    @patch("apps.lens_bridge.api.views.sl_client.request_multipart")
    def test_attachment_upload_rejects_an_invalid_sourcelens_response(
        self,
        request_multipart,
    ):
        self._mark_session_ready()
        request_multipart.return_value = {
            "uuid": "not-a-uuid",
            "kind": "document",
        }
        uploaded = SimpleUploadedFile(
            "report.pdf",
            b"pdf-bytes",
            content_type="application/pdf",
        )

        response = self.client.post(
            reverse(
                "lens-copilot-session-upload-attachment",
                kwargs={"pk": self.session.pk},
            ),
            {"file": uploaded},
            format="multipart",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("invalid attachment response", str(response.json()))

    @patch("apps.lens_bridge.api.views.sl_client.request_multipart")
    def test_attachment_upload_requires_a_sourcelens_kind(
        self,
        request_multipart,
    ):
        self._mark_session_ready()
        request_multipart.return_value = {"uuid": str(uuid.uuid4())}
        uploaded = SimpleUploadedFile(
            "report.pdf",
            b"pdf-bytes",
            content_type="application/pdf",
        )

        response = self.client.post(
            reverse(
                "lens-copilot-session-upload-attachment",
                kwargs={"pk": self.session.pk},
            ),
            {"file": uploaded},
            format="multipart",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("invalid attachment response", str(response.json()))

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    def test_attachment_content_is_streamed_through_hfl(self, stream_binary):
        self._mark_session_ready()
        attachment_uuid = uuid.uuid4()
        stream_binary.return_value = sl_client.BinaryStreamResponse(
            body=iter([b"abc", b"def"]),
            content_type="image/png",
            content_length="6",
            content_disposition="",
            cache_control="private, max-age=3600",
        )

        response = self.client.get(
            _attachment_proxy_url(self.session.pk, str(attachment_uuid)),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"abcdef")
        self.assertEqual(response["Content-Type"], "image/png")
        stream_binary.assert_called_once_with(
            f"/api/lens/attachments/{attachment_uuid}/",
            hfl_user=self.user,
        )

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    def test_attachment_content_rejects_an_unsigned_uuid(self, stream_binary):
        self._mark_session_ready()
        attachment_uuid = uuid.uuid4()

        response = self.client.get(
            reverse(
                "lens-copilot-session-attachment-content",
                kwargs={
                    "pk": self.session.pk,
                    "attachment_uuid": attachment_uuid,
                },
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 404)
        stream_binary.assert_not_called()

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    def test_output_file_content_is_streamed_through_hfl(self, stream_binary):
        self._mark_session_ready()
        output_file_uuid = uuid.uuid4()
        stream_binary.return_value = sl_client.BinaryStreamResponse(
            body=iter([b"abc", b"def"]),
            content_type="text/markdown",
            content_length="6",
            content_disposition='attachment; filename="report.md"',
            cache_control="private, max-age=3600",
        )

        response = self.client.get(
            _output_file_proxy_url(self.session.pk, str(output_file_uuid)),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"abcdef")
        self.assertEqual(response["Content-Type"], "text/markdown")
        stream_binary.assert_called_once_with(
            f"/api/lens/output-files/{output_file_uuid}/",
            hfl_user=self.user,
        )

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    def test_output_file_content_rejects_an_unsigned_uuid(self, stream_binary):
        self._mark_session_ready()
        output_file_uuid = uuid.uuid4()

        response = self.client.get(
            reverse(
                "lens-copilot-session-output-file-content",
                kwargs={
                    "pk": self.session.pk,
                    "file_uuid": output_file_uuid,
                },
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 404)
        stream_binary.assert_not_called()

    def test_rewrite_attachment_urls_rewrites_output_file_urls(self):
        messages = [
            {
                "output_files": [
                    {
                        "uuid": str(uuid.uuid4()),
                        "filename": "report.md",
                        "url": "https://sourcelens/api/lens/output-files/x/",
                    }
                ],
            },
        ]

        rewritten = _rewrite_attachment_urls(messages, session_id=17)

        output_file = rewritten[0]["output_files"][0]
        self.assertIn("output-files/", output_file["url"])
        self.assertNotIn("sourcelens", output_file["url"])
        self.assertIn("token=", output_file["url"])

    def test_rewrite_attachment_urls_rewrites_output_files_without_attachments(self):
        messages = [
            {
                "attachments": None,
                "output_files": [
                    {
                        "uuid": str(uuid.uuid4()),
                        "filename": "report.md",
                        "url": "https://sourcelens/api/lens/output-files/x/",
                    }
                ],
            },
        ]

        rewritten = _rewrite_attachment_urls(messages, session_id=17)

        output_file = rewritten[0]["output_files"][0]
        self.assertIn("output-files/", output_file["url"])
        self.assertNotIn("sourcelens", output_file["url"])

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_feedback_persists_through_the_sourcelens_run(self, request_json):
        self._mark_session_ready()
        run_uuid = uuid.uuid4()
        request_json.side_effect = [
            [
                {
                    "uuid": str(uuid.uuid4()),
                    "role": "assistant",
                    "run": str(run_uuid),
                    "content": "Answer",
                }
            ],
            {
                "feedback": "positive",
                "feedback_updated_at": "2026-08-20T02:00:00Z",
            },
        ]

        response = self.client.patch(
            reverse(
                "lens-copilot-session-feedback",
                kwargs={"pk": self.session.pk, "run_uuid": run_uuid},
            ),
            {"feedback": "positive"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json().get("data", response.json())
        self.assertEqual(payload["feedback"], "positive")
        self.assertEqual(
            payload["feedback_updated_at"],
            "2026-08-20T02:00:00Z",
        )
        self.assertEqual(
            request_json.call_args_list[0].args,
            (
                "GET",
                f"/api/lens/sessions/{self.session.sl_session_uuid}/messages/",
            ),
        )
        self.assertEqual(
            request_json.call_args_list[1].args,
            ("PATCH", f"/api/lens/runs/{run_uuid}/feedback/"),
        )
        self.assertEqual(
            request_json.call_args_list[1].kwargs["json_body"],
            {"feedback": "positive"},
        )
        self.assertEqual(
            request_json.call_args_list[1].kwargs["hfl_user"].pk,
            self.user.pk,
        )

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_feedback_rejects_a_run_outside_the_session(self, request_json):
        self._mark_session_ready()
        request_json.return_value = []

        response = self.client.patch(
            reverse(
                "lens-copilot-session-feedback",
                kwargs={"pk": self.session.pk, "run_uuid": uuid.uuid4()},
            ),
            {"feedback": "negative"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(request_json.call_count, 1)

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_feedback_rejects_an_unknown_value_before_calling_sourcelens(
        self,
        request_json,
    ):
        self._mark_session_ready()

        response = self.client.patch(
            reverse(
                "lens-copilot-session-feedback",
                kwargs={"pk": self.session.pk, "run_uuid": uuid.uuid4()},
            ),
            {"feedback": "helpful"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 400)
        request_json.assert_not_called()

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_answer_pdf_is_streamed_for_a_run_in_the_session(
        self,
        request_json,
        stream_binary,
    ):
        self._mark_session_ready()
        run_uuid = uuid.uuid4()
        request_json.return_value = [
            {
                "uuid": str(uuid.uuid4()),
                "role": "assistant",
                "run": str(run_uuid),
                "content": "Answer",
            }
        ]
        stream_binary.return_value = sl_client.BinaryStreamResponse(
            body=iter([b"pdf", b"-bytes"]),
            content_type="application/pdf",
            content_length="9",
            content_disposition='attachment; filename="answer.pdf"',
            cache_control="private, max-age=0, no-store",
        )

        response = self.client.get(
            reverse(
                "lens-copilot-session-run-pdf",
                kwargs={"pk": self.session.pk, "run_uuid": run_uuid},
            ),
            HTTP_ACCEPT="application/pdf",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"pdf-bytes")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, max-age=0, no-store")
        stream_binary.assert_called_once_with(
            f"/api/lens/runs/{run_uuid}/pdf/",
            hfl_user=self.user,
        )

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_answer_pdf_rejects_a_run_outside_the_session(
        self,
        request_json,
        stream_binary,
    ):
        self._mark_session_ready()
        request_json.return_value = []

        response = self.client.get(
            reverse(
                "lens-copilot-session-run-pdf",
                kwargs={"pk": self.session.pk, "run_uuid": uuid.uuid4()},
            ),
            HTTP_ACCEPT="application/pdf",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        stream_binary.assert_not_called()

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_answer_pdf_upstream_error_is_json(
        self,
        request_json,
        stream_binary,
    ):
        self._mark_session_ready()
        run_uuid = uuid.uuid4()
        request_json.return_value = [
            {
                "uuid": str(uuid.uuid4()),
                "role": "assistant",
                "run": str(run_uuid),
                "content": "Answer",
            }
        ]
        stream_binary.side_effect = sl_client.LensBridgeUnavailable()

        response = self.client.get(
            reverse(
                "lens-copilot-session-run-pdf",
                kwargs={"pk": self.session.pk, "run_uuid": run_uuid},
            ),
            HTTP_ACCEPT="application/pdf",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Content-Type"], "application/json")
        stream_binary.assert_called_once_with(
            f"/api/lens/runs/{run_uuid}/pdf/",
            hfl_user=self.user,
        )

    def test_pdf_content_negotiation_is_limited_to_the_download_action(self):
        response = self.client.get(
            reverse(
                "lens-copilot-session-sync",
                kwargs={"pk": self.session.pk},
            ),
            HTTP_ACCEPT="application/pdf",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 406)

    @patch("apps.lens_bridge.api.views.sl_client.stream_sse")
    def test_run_stream_requires_the_run_bound_to_the_session(self, stream_sse):
        run_uuid = uuid.uuid4()
        self.session.active_run_uuid = run_uuid
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )
        stream_sse.return_value = iter([b"data: {}\n\n"])

        response = self.client.get(
            reverse(
                "lens-copilot-session-run-stream",
                kwargs={"session_id": self.session.pk, "run_uuid": run_uuid},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        stream_sse.assert_called_once_with(
            f"/api/lens/runs/{run_uuid}/stream/",
            hfl_user=self.user,
        )
        self.assertEqual(b"".join(response.streaming_content), b"data: {}\n\n")

    @patch("apps.lens_bridge.api.views.sl_client.stream_sse")
    def test_run_stream_rejects_a_run_not_bound_to_the_session(self, stream_sse):
        self.session.active_run_uuid = uuid.uuid4()
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )

        response = self.client.get(
            reverse(
                "lens-copilot-session-run-stream",
                kwargs={
                    "session_id": self.session.pk,
                    "run_uuid": uuid.uuid4(),
                },
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 404)
        stream_sse.assert_not_called()

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_create_run_rejects_a_second_active_run(self, request_json):
        self._mark_session_ready()
        active_run_uuid = uuid.uuid4()
        self.session.active_run_uuid = active_run_uuid
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )
        request_json.return_value = {
            "uuid": str(active_run_uuid),
            "status": "running",
            "idempotency_key": "first-request",
        }

        response = self.client.post(
            reverse(
                "lens-copilot-session-create-run",
                kwargs={"pk": self.session.pk},
            ),
            {"question": "Second question", "idempotency_key": "second-request"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 409)
        request_json.assert_called_once_with(
            "GET",
            f"/api/lens/runs/{active_run_uuid}/",
            hfl_user=self.user,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_run_uuid, active_run_uuid)

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_create_run_persists_and_forwards_a_valid_retry(self, request_json):
        self._mark_session_ready()
        retry_run_uuid = uuid.uuid4()
        new_run_uuid = uuid.uuid4()
        request_json.side_effect = [
            [
                {
                    "uuid": str(uuid.uuid4()),
                    "role": "assistant",
                    "run": str(retry_run_uuid),
                    "content": "Original answer",
                }
            ],
            {
                "uuid": str(new_run_uuid),
                "status": "queued",
                "idempotency_key": "retry-request",
            },
        ]

        response = self.client.post(
            reverse(
                "lens-copilot-session-create-run",
                kwargs={"pk": self.session.pk},
            ),
            {
                "question": "Original question",
                "idempotency_key": "retry-request",
                "retry_of_run_uuid": str(retry_run_uuid),
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 201)
        submission = LensRunSubmission.objects.get(
            session_link=self.session,
            idempotency_key="retry-request",
        )
        self.assertEqual(submission.retry_of_run_uuid, retry_run_uuid)
        self.assertEqual(
            request_json.call_args_list[1].kwargs["json_body"]["retry_of_run_uuid"],
            str(retry_run_uuid),
        )

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_create_run_rejects_a_retry_outside_the_session(self, request_json):
        self._mark_session_ready()
        request_json.return_value = []

        response = self.client.post(
            reverse(
                "lens-copilot-session-create-run",
                kwargs={"pk": self.session.pk},
            ),
            {
                "question": "Try another Run",
                "idempotency_key": "invalid-retry",
                "retry_of_run_uuid": str(uuid.uuid4()),
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            LensRunSubmission.objects.filter(
                session_link=self.session,
                idempotency_key="invalid-retry",
            ).exists()
        )

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_create_run_replays_the_same_idempotency_key(self, request_json):
        self._mark_session_ready()
        active_run_uuid = uuid.uuid4()
        self.session.active_run_uuid = active_run_uuid
        self.session.active_run_status = "queued"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )
        request_json.return_value = {
            "uuid": str(active_run_uuid),
            "status": "running",
            "idempotency_key": "same-request",
        }

        response = self.client.post(
            reverse(
                "lens-copilot-session-create-run",
                kwargs={"pk": self.session.pk},
            ),
            {"question": "Question", "idempotency_key": "same-request"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json().get("data", response.json())
        self.assertEqual(payload["uuid"], str(active_run_uuid))
        request_json.assert_called_once_with(
            "GET",
            f"/api/lens/runs/{active_run_uuid}/",
            hfl_user=self.user,
        )

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_create_run_preserves_active_run_when_sourcelens_is_unavailable(
        self,
        request_json,
    ):
        self._mark_session_ready()
        active_run_uuid = uuid.uuid4()
        self.session.active_run_uuid = active_run_uuid
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )
        request_json.side_effect = sl_client.LensBridgeUnavailable()

        response = self.client.post(
            reverse(
                "lens-copilot-session-create-run",
                kwargs={"pk": self.session.pk},
            ),
            {"question": "Question", "idempotency_key": "new-request"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 503)
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_run_uuid, active_run_uuid)

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_create_run_reconciles_a_terminal_run_before_starting_another(
        self,
        request_json,
    ):
        self._mark_session_ready()
        completed_run_uuid = uuid.uuid4()
        next_run_uuid = uuid.uuid4()
        self.session.active_run_uuid = completed_run_uuid
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )

        def response_for(method, path, **_kwargs):
            if method == "GET" and path.endswith(f"/runs/{completed_run_uuid}/"):
                return {
                    "uuid": str(completed_run_uuid),
                    "status": "done",
                    "idempotency_key": "completed-request",
                }
            if method == "POST" and path.endswith("/runs/"):
                return {
                    "uuid": str(next_run_uuid),
                    "status": "queued",
                    "idempotency_key": "next-request",
                }
            raise AssertionError((method, path))

        request_json.side_effect = response_for
        response = self.client.post(
            reverse(
                "lens-copilot-session-create-run",
                kwargs={"pk": self.session.pk},
            ),
            {"question": "Next question", "idempotency_key": "next-request"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 201)
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_run_uuid, next_run_uuid)
        self.assertEqual(self.session.active_run_status, "queued")

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_create_run_recovers_after_local_binding_failure(self, request_json):
        self._mark_session_ready()
        run_uuid = uuid.uuid4()
        request_json.return_value = {
            "uuid": str(run_uuid),
            "status": "queued",
            "idempotency_key": "durable-request",
            "created_at": "2026-08-11T02:00:00Z",
        }
        self.client.raise_request_exception = False

        with patch(
            "apps.lens_bridge.services.run_submissions.usage.register_usage_run",
            side_effect=RuntimeError("local binding interrupted"),
        ):
            failed_response = self.client.post(
                reverse(
                    "lens-copilot-session-create-run",
                    kwargs={"pk": self.session.pk},
                ),
                {
                    "question": "Recover this question",
                    "idempotency_key": "durable-request",
                },
                format="json",
                HTTP_X_ORG_KEY=self.org.key,
            )

        self.assertEqual(failed_response.status_code, 500)
        submission = LensRunSubmission.objects.get(
            session_link=self.session,
            idempotency_key="durable-request",
        )
        self.assertEqual(submission.status, LensRunSubmission.Status.PENDING)
        self.assertIsNone(submission.sl_run_uuid)
        self.session.refresh_from_db()
        self.assertIsNone(self.session.active_run_uuid)

        recovered_response = self.client.post(
            reverse(
                "lens-copilot-session-create-run",
                kwargs={"pk": self.session.pk},
            ),
            {
                "question": "Recover this question",
                "idempotency_key": "durable-request",
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(recovered_response.status_code, 201)
        submission.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(submission.status, LensRunSubmission.Status.BOUND)
        self.assertEqual(submission.sl_run_uuid, run_uuid)
        self.assertEqual(self.session.active_run_uuid, run_uuid)
        self.assertEqual(request_json.call_count, 2)
        for call in request_json.call_args_list:
            self.assertEqual(
                call.args[:2],
                ("POST", f"/api/lens/sessions/{self.session.sl_session_uuid}/runs/"),
            )
            self.assertEqual(
                call.kwargs["json_body"]["idempotency_key"],
                "durable-request",
            )

    @patch("apps.lens_bridge.services.sl_client.request_json")
    def test_sync_returns_hfl_submission_as_elapsed_anchor(self, request_json):
        self._mark_session_ready()
        run_uuid = uuid.uuid4()
        submission = LensRunSubmission.objects.create(
            organization=self.org,
            hfl_user=self.user,
            session_link=self.session,
            idempotency_key="elapsed-anchor",
            question="When did this response start?",
            status=LensRunSubmission.Status.BOUND,
            sl_run_uuid=run_uuid,
            run_status="queued",
        )
        submitted_at = datetime(2026, 8, 11, 1, 59, tzinfo=timezone.utc)
        LensRunSubmission.objects.filter(pk=submission.pk).update(
            created_at=submitted_at
        )
        self.session.active_run_uuid = run_uuid
        self.session.active_run_status = "queued"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )

        def response_for(_method, path, **_kwargs):
            if path.endswith("/messages/"):
                return []
            if path.endswith(f"/runs/{run_uuid}/"):
                return {
                    "uuid": str(run_uuid),
                    "status": "running",
                    "created_at": "2026-08-11T02:00:00Z",
                    "started_at": "2026-08-11T02:00:03Z",
                }
            raise AssertionError(path)

        request_json.side_effect = response_for
        response = self.client.get(
            reverse("lens-copilot-session-sync", kwargs={"pk": self.session.pk}),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json().get("data", response.json())
        self.assertEqual(
            payload["active_run"]["elapsed_anchor_at"],
            "2026-08-11T01:59:00Z",
        )
        self.assertEqual(payload["response_state"]["status"], "running")
        self.assertEqual(
            payload["response_state"]["started_at"],
            "2026-08-11T01:59:00Z",
        )

    @patch("apps.lens_bridge.services.sl_client.request_json", return_value=[])
    def test_sync_exposes_a_pending_submission(self, _request_json):
        self._mark_session_ready()
        submission = LensRunSubmission.objects.create(
            organization=self.org,
            hfl_user=self.user,
            session_link=self.session,
            idempotency_key="awaiting-recovery",
            question="Keep this question visible",
        )
        submitted_at = datetime(2026, 8, 11, 1, 58, tzinfo=timezone.utc)
        LensRunSubmission.objects.filter(pk=submission.pk).update(
            created_at=submitted_at
        )

        response = self.client.get(
            reverse("lens-copilot-session-sync", kwargs={"pk": self.session.pk}),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json().get("data", response.json())
        self.assertIsNone(payload["active_run"])
        self.assertEqual(payload["response_state"]["status"], "submitting")
        self.assertEqual(
            payload["response_state"]["started_at"],
            "2026-08-11T01:58:00Z",
        )
        self.assertEqual(
            payload["response_state"]["question"],
            "Keep this question visible",
        )

    @patch("apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error")
    def test_delete_returns_the_persisted_deleting_state(self, _queue_teardown):
        response = self.client.delete(
            reverse("lens-copilot-session-detail", kwargs={"pk": self.session.pk}),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        payload = payload.get("data", payload)
        self.assertEqual(
            payload["lifecycle_status"],
            LensSessionLink.LifecycleStatus.DELETING,
        )
        self.assertEqual(payload["status"], LensSessionLink.Status.ARCHIVED)

    @patch("apps.lens_bridge.services.sl_client.request_json")
    def test_sync_returns_a_durable_sanitized_failed_run_outcome(self, request_json):
        run_uuid = uuid.uuid4()
        session_uuid = uuid.uuid4()
        self.session.lifecycle_status = LensSessionLink.LifecycleStatus.READY
        self.session.provision_phase = LensSessionLink.ProvisionPhase.READY
        self.session.sl_session_uuid = session_uuid
        self.session.active_run_uuid = run_uuid
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=[
                "lifecycle_status",
                "provision_phase",
                "sl_session_uuid",
                "active_run_uuid",
                "active_run_status",
                "updated_at",
            ]
        )
        LensSlUserLink.objects.create(
            hfl_user=self.user,
            sl_user_id=41,
            sl_username="hfl-u-41",
            provision_status=LensSlUserLink.ProvisionStatus.READY,
        )
        LensUsageLedger.objects.create(
            organization=self.org,
            hfl_user=self.user,
            session_link=self.session,
            sl_user_id=41,
            sl_run_uuid=run_uuid,
            run_status="running",
            question="List files",
            occurred_at=self.session.created_at,
        )

        def response_for(_method, path, **_kwargs):
            if path.endswith("/messages/"):
                return [
                    {
                        "uuid": str(uuid.uuid4()),
                        "role": "user",
                        "content": "List files",
                        "run": str(run_uuid),
                    }
                ]
            if path.endswith(f"/runs/{run_uuid}/"):
                return {
                    "uuid": str(run_uuid),
                    "status": "failed",
                    "error": "MODEL_STREAM_ERROR api_key=must-not-leak",
                }
            raise AssertionError(path)

        request_json.side_effect = response_for
        response = self.client.get(
            reverse("lens-copilot-session-sync", kwargs={"pk": self.session.pk}),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        payload = payload.get("data", payload)
        self.assertIsNone(payload["active_run"])
        self.assertEqual(len(payload["run_outcomes"]), 1)
        outcome = payload["run_outcomes"][0]
        self.assertEqual(outcome["run_uuid"], str(run_uuid))
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error_code"], "MODEL_PROVIDER_ERROR")
        self.assertIn("quota", outcome["message"])
        self.assertNotIn("must-not-leak", str(payload))
        self.session.refresh_from_db()
        self.assertIsNone(self.session.active_run_uuid)
