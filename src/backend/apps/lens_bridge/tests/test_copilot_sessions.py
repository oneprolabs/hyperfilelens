import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.api.serializers import LensSessionCreateSerializer
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

    @patch(
        "apps.lens_bridge.services.chat_lifecycle._queue_teardown_or_record_error"
    )
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
