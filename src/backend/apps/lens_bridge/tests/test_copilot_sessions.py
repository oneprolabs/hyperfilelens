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
    LensSessionCreateSerializer,
    LensSessionLinkSerializer,
)
from apps.lens_bridge.api.views import (
    _attachment_proxy_url,
    _require_attachment_proxy_token,
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
