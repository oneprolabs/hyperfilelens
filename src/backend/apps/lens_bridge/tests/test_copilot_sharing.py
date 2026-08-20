import uuid
from types import SimpleNamespace
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.models import LensSessionLink
from apps.lens_bridge.services import copilot_sharing, sl_client


class CopilotSharingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="share-owner@example.test",
            email="share-owner@example.test",
        )
        self.org, _membership = provision_registered_user_tenant(self.user)
        self.session = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            title="Shareable Chat",
            sl_session_uuid=uuid.uuid4(),
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )
        self.run_uuid = uuid.uuid4()
        self.share_uuid = uuid.uuid4()

    def _messages(self):
        return [
            {"role": "user", "content": "First question"},
            {
                "role": "assistant",
                "content": "First answer",
                "run": str(uuid.uuid4()),
                "completed_at": "2026-08-20T07:00:00Z",
            },
            {"role": "user", "content": "Latest question"},
            {
                "role": "assistant",
                "content": "Latest answer",
                "run": str(self.run_uuid),
                "completed_at": "2026-08-20T08:00:00Z",
            },
        ]

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_candidate_uses_latest_completed_answer_and_existing_share(
        self,
        request_json,
    ):
        request_json.side_effect = [
            self._messages(),
            {
                "results": [
                    {
                        "uuid": str(self.share_uuid),
                        "token": "share-token",
                        "run_uuid": str(self.run_uuid),
                        "title": "Existing share",
                    }
                ],
                "next": None,
            },
        ]

        result = copilot_sharing.get_share_candidate(self.session)

        self.assertTrue(result["shareable"])
        self.assertEqual(result["question"], "Latest question")
        self.assertEqual(result["answer"], "Latest answer")
        self.assertEqual(result["share"]["uuid"], str(self.share_uuid))
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.share_state_json["shares"][0]["token"],
            "share-token",
        )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_candidate_ignores_a_newer_answer_that_is_still_running(
        self,
        request_json,
    ):
        completed_run_uuid = uuid.uuid4()
        request_json.side_effect = [
            [
                {"role": "user", "content": "Completed question"},
                {
                    "role": "assistant",
                    "content": "Completed answer",
                    "run": str(completed_run_uuid),
                    "completed_at": "2026-08-20T08:00:00Z",
                },
                {"role": "user", "content": "Running question"},
                {
                    "role": "assistant",
                    "content": "Partial answer",
                    "run": str(self.run_uuid),
                    "completed_at": None,
                },
            ],
            {"results": [], "next": None},
        ]

        result = copilot_sharing.get_share_candidate(self.session)

        self.assertEqual(result["run_uuid"], str(completed_run_uuid))
        self.assertEqual(result["question"], "Completed question")
        self.assertEqual(result["answer"], "Completed answer")

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_existing_share_recovers_and_revokes_other_chat_share_identities(
        self,
        request_json,
    ):
        old_run_uuid = uuid.uuid4()
        old_share_uuid = uuid.uuid4()
        messages = [
            {"role": "user", "content": "Old question"},
            {
                "role": "assistant",
                "content": "Old answer",
                "run": str(old_run_uuid),
                "completed_at": "2026-08-20T07:00:00Z",
            },
            {"role": "user", "content": "Latest question"},
            {
                "role": "assistant",
                "content": "Latest answer",
                "run": str(self.run_uuid),
                "completed_at": "2026-08-20T08:00:00Z",
            },
        ]
        request_json.side_effect = [
            messages,
            {
                "results": [
                    {
                        "uuid": str(self.share_uuid),
                        "token": "current-share-token",
                        "run_uuid": str(self.run_uuid),
                    },
                    {
                        "uuid": str(old_share_uuid),
                        "token": "old-share-token",
                        "run_uuid": str(old_run_uuid),
                    },
                ],
                "next": None,
            },
            None,
        ]

        share = copilot_sharing.create_share(self.session)

        self.assertEqual(share["uuid"], str(self.share_uuid))
        self.assertIn(
            call(
                "DELETE",
                f"/api/lens/shares/{old_share_uuid}/",
                hfl_user=self.user,
            ),
            request_json.call_args_list,
        )
        self.session.refresh_from_db()
        self.assertEqual(
            [row["uuid"] for row in self.session.share_state_json["shares"]],
            [str(self.share_uuid)],
        )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_candidate_lookup_alone_converges_to_one_current_share(
        self,
        request_json,
    ):
        old_run_uuid = uuid.uuid4()
        old_share_uuid = uuid.uuid4()
        messages = [
            {"role": "user", "content": "Old question"},
            {
                "role": "assistant",
                "content": "Old answer",
                "run": str(old_run_uuid),
                "completed_at": "2026-08-20T07:00:00Z",
            },
            {"role": "user", "content": "Latest question"},
            {
                "role": "assistant",
                "content": "Latest answer",
                "run": str(self.run_uuid),
                "completed_at": "2026-08-20T08:00:00Z",
            },
        ]
        request_json.side_effect = [
            messages,
            {
                "results": [
                    {
                        "uuid": str(self.share_uuid),
                        "token": "current-share-token",
                        "run_uuid": str(self.run_uuid),
                    },
                    {
                        "uuid": str(old_share_uuid),
                        "token": "old-share-token",
                        "run_uuid": str(old_run_uuid),
                    },
                ],
                "next": None,
            },
            None,
        ]

        candidate = copilot_sharing.get_share_candidate(self.session)

        self.assertEqual(candidate["share"]["uuid"], str(self.share_uuid))
        self.assertIn(
            call(
                "DELETE",
                f"/api/lens/shares/{old_share_uuid}/",
                hfl_user=self.user,
            ),
            request_json.call_args_list,
        )
        self.session.refresh_from_db()
        self.assertEqual(
            [row["uuid"] for row in self.session.share_state_json["shares"]],
            [str(self.share_uuid)],
        )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_create_share_delegates_content_to_sourcelens_and_records_identity(
        self,
        request_json,
    ):
        request_json.side_effect = [
            self._messages(),
            {"results": [], "next": None},
            {
                "uuid": str(self.share_uuid),
                "token": "new-share-token",
                "run_uuid": str(self.run_uuid),
                "title": "Latest question",
            },
        ]

        share = copilot_sharing.create_share(
            self.session,
            title="Latest question",
        )

        self.assertEqual(share["token"], "new-share-token")
        self.assertEqual(
            request_json.call_args_list[-1].args,
            ("POST", f"/api/lens/runs/{self.run_uuid}/share/"),
        )
        self.assertEqual(
            request_json.call_args_list[-1].kwargs["json_body"],
            {"title": "Latest question"},
        )
        self.session.refresh_from_db()
        access = copilot_sharing.make_share_access_token(self.session, share)
        signed_payload = copilot_sharing.resolve_share_access_token(access)
        self.assertNotIn("share_token", signed_payload)
        resolved, payload = copilot_sharing.require_active_share_access(
            organization_id=self.org.id,
            raw_token=access,
        )
        self.assertEqual(resolved.id, self.session.id)
        self.assertEqual(payload["share_token"], "new-share-token")

        with self.assertRaises(copilot_sharing.CopilotShareNotFoundError):
            copilot_sharing.require_active_share_access(
                organization_id=self.org.id + 1,
                raw_token=access,
            )

    def test_share_access_fails_closed_while_replacement_is_incomplete(self):
        old_share_uuid = uuid.uuid4()
        current_share = {
            "uuid": str(self.share_uuid),
            "run_uuid": str(self.run_uuid),
            "token": "current-share-token",
        }
        self.session.share_state_json = {
            "version": 1,
            "shares": [
                current_share,
                {
                    "uuid": str(old_share_uuid),
                    "run_uuid": str(uuid.uuid4()),
                    "token": "old-share-token",
                },
            ],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        access = copilot_sharing.make_share_access_token(
            self.session,
            current_share,
        )

        with self.assertRaises(copilot_sharing.CopilotShareNotFoundError):
            copilot_sharing.require_active_share_access(
                organization_id=self.org.id,
                raw_token=access,
            )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_create_share_replaces_the_previous_chat_share(
        self,
        request_json,
    ):
        old_share_uuid = uuid.uuid4()
        old_run_uuid = uuid.uuid4()
        self.session.share_state_json = {
            "version": 1,
            "shares": [
                {
                    "uuid": str(old_share_uuid),
                    "run_uuid": str(old_run_uuid),
                    "token": "old-share-token",
                }
            ],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        request_json.side_effect = [
            self._messages(),
            {"results": [], "next": None},
            {
                "uuid": str(self.share_uuid),
                "token": "new-share-token",
                "run_uuid": str(self.run_uuid),
                "title": "Latest question",
            },
            None,
        ]

        copilot_sharing.create_share(self.session, title="Latest question")

        self.assertEqual(
            request_json.call_args_list[-1],
            call(
                "DELETE",
                f"/api/lens/shares/{old_share_uuid}/",
                hfl_user=self.user,
            ),
        )
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.share_state_json["shares"],
            [
                {
                    "uuid": str(self.share_uuid),
                    "run_uuid": str(self.run_uuid),
                    "token": "new-share-token",
                }
            ],
        )

    @patch(
        "apps.lens_bridge.services.copilot_sharing._record_share",
        side_effect=RuntimeError("database write failed"),
    )
    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_create_share_compensates_when_hfl_cannot_record_ownership(
        self,
        request_json,
        _record_share,
    ):
        request_json.side_effect = [
            self._messages(),
            {"results": [], "next": None},
            {
                "uuid": str(self.share_uuid),
                "token": "new-share-token",
                "run_uuid": str(self.run_uuid),
                "title": "Latest question",
            },
            None,
        ]

        with self.assertRaisesRegex(RuntimeError, "database write failed"):
            copilot_sharing.create_share(
                self.session,
                title="Latest question",
            )

        self.assertEqual(
            request_json.call_args_list[-1].args,
            ("DELETE", f"/api/lens/shares/{self.share_uuid}/"),
        )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_create_share_compensates_when_chat_cleanup_wins_the_race(
        self,
        request_json,
    ):
        def request(method, path, **_kwargs):
            if method == "GET" and path.endswith("/messages/"):
                return self._messages()
            if method == "GET" and path == "/api/lens/shares/":
                return {"results": [], "next": None}
            if method == "POST":
                LensSessionLink.objects.filter(pk=self.session.pk).update(
                    status=LensSessionLink.Status.ARCHIVED,
                    lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
                    cleanup_intent=LensSessionLink.CleanupIntent.DELETE_SESSION,
                )
                return {
                    "uuid": str(self.share_uuid),
                    "token": "new-share-token",
                    "run_uuid": str(self.run_uuid),
                    "title": "Latest question",
                }
            if method == "DELETE":
                return None
            self.fail(f"Unexpected SourceLens request: {method} {path}")

        request_json.side_effect = request

        with self.assertRaises(copilot_sharing.CopilotShareNotFoundError):
            copilot_sharing.create_share(
                self.session,
                title="Latest question",
            )

        self.assertIn(
            call(
                "DELETE",
                f"/api/lens/shares/{self.share_uuid}/",
                hfl_user=self.user,
            ),
            request_json.call_args_list,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.share_state_json["shares"], [])

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_teardown_revokes_known_shares_without_deleting_hfl_content(
        self,
        request_json,
    ):
        self.session.share_state_json = {
            "version": 1,
            "shares": [
                {
                    "uuid": str(self.share_uuid),
                    "run_uuid": str(self.run_uuid),
                    "token": "share-token",
                }
            ],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        revoked = copilot_sharing.revoke_session_shares(self.session)

        self.assertEqual(revoked, 1)
        request_json.assert_called_once_with(
            "DELETE",
            f"/api/lens/shares/{self.share_uuid}/",
            hfl_user=self.user,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.share_state_json["shares"], [])

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_teardown_preserves_share_state_when_revocation_fails(
        self,
        request_json,
    ):
        self.session.share_state_json = {
            "version": 1,
            "shares": [
                {
                    "uuid": str(self.share_uuid),
                    "run_uuid": str(self.run_uuid),
                    "token": "share-token",
                }
            ],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        request_json.side_effect = sl_client.LensBridgeUnavailable()

        with self.assertRaises(sl_client.LensBridgeUnavailable):
            copilot_sharing.revoke_session_shares(self.session)

        self.session.refresh_from_db()
        self.assertEqual(len(self.session.share_state_json["shares"]), 1)

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_revoke_share_cleans_every_known_link_for_the_chat(
        self,
        request_json,
    ):
        other_share_uuid = uuid.uuid4()
        self.session.share_state_json = {
            "version": 1,
            "shares": [
                {
                    "uuid": str(self.share_uuid),
                    "run_uuid": str(self.run_uuid),
                    "token": "share-token",
                },
                {
                    "uuid": str(other_share_uuid),
                    "run_uuid": str(uuid.uuid4()),
                    "token": "other-share-token",
                },
            ],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])

        copilot_sharing.revoke_share(self.session, str(self.share_uuid))

        request_json.assert_has_calls(
            [
                call(
                    "DELETE",
                    f"/api/lens/shares/{self.share_uuid}/",
                    hfl_user=self.user,
                ),
                call(
                    "DELETE",
                    f"/api/lens/shares/{other_share_uuid}/",
                    hfl_user=self.user,
                ),
            ],
            any_order=True,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.share_state_json["shares"], [])

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_update_share_title_replaces_other_known_chat_shares(
        self,
        request_json,
    ):
        stale_share_uuid = uuid.uuid4()
        stale_run_uuid = uuid.uuid4()
        self.session.share_state_json = {
            "version": 1,
            "shares": [
                {
                    "uuid": str(stale_share_uuid),
                    "run_uuid": str(stale_run_uuid),
                    "token": "stale-share-token",
                }
            ],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        request_json.side_effect = [
            self._messages(),
            {
                "results": [
                    {
                        "uuid": str(self.share_uuid),
                        "run_uuid": str(self.run_uuid),
                        "token": "current-share-token",
                        "title": "Original title",
                    }
                ],
                "next": None,
            },
            {
                "uuid": str(self.share_uuid),
                "run_uuid": str(self.run_uuid),
                "token": "current-share-token",
                "title": "Renamed title",
            },
            None,
        ]

        updated = copilot_sharing.update_share_title(
            self.session,
            str(self.share_uuid),
            title="Renamed title",
        )

        self.assertEqual(updated["title"], "Renamed title")
        self.assertIn(
            call(
                "DELETE",
                f"/api/lens/shares/{stale_share_uuid}/",
                hfl_user=self.user,
            ),
            request_json.call_args_list,
        )
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.share_state_json["shares"],
            [
                {
                    "uuid": str(self.share_uuid),
                    "run_uuid": str(self.run_uuid),
                    "token": "current-share-token",
                }
            ],
        )


class CopilotSharingApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="share-api-owner@example.test",
            email="share-api-owner@example.test",
        )
        self.org, _membership = provision_registered_user_tenant(self.user)
        self.session = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            title="Shared API Chat",
            sl_session_uuid=uuid.uuid4(),
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )
        self.run_uuid = uuid.uuid4()
        self.share_uuid = uuid.uuid4()
        self.share = {
            "uuid": str(self.share_uuid),
            "run_uuid": str(self.run_uuid),
            "token": "shared-api-token",
            "title": "Shared answer",
        }
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch(
        "apps.lens_bridge.services.copilot_sharing.get_share_candidate"
    )
    def test_get_share_candidate_adds_an_hfl_organization_link(
        self,
        get_share_candidate,
    ):
        get_share_candidate.return_value = {
            "shareable": True,
            "question": "Question",
            "answer": "Answer",
            "run_uuid": str(self.run_uuid),
            "share": self.share,
        }

        response = self.client.get(
            reverse(
                "lens-copilot-session-share",
                kwargs={"pk": self.session.pk},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["share"]["share_path"].startswith(
            "/insight/copilot/shared?access="
        ))
        self.assertNotIn("token", response.data["share"])
        self.assertNotIn("question", self.session.share_state_json)

    @patch(
        "apps.lens_bridge.services.copilot_sharing.get_share_candidate"
    )
    def test_share_response_does_not_expose_unrecognized_upstream_fields(
        self,
        get_share_candidate,
    ):
        get_share_candidate.return_value = {
            "shareable": True,
            "question": "Question",
            "answer": "Answer",
            "run_uuid": str(self.run_uuid),
            "share": {
                **self.share,
                "public_url": (
                    "https://sourcelens.example/qa/shared-api-token"
                ),
                "future_secret": "upstream-private-value",
            },
        }

        response = self.client.get(
            reverse(
                "lens-copilot-session-share",
                kwargs={"pk": self.session.pk},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("token", response.data["share"])
        self.assertNotIn("public_url", response.data["share"])
        self.assertNotIn("future_secret", response.data["share"])

    @patch("apps.lens_bridge.services.copilot_sharing.create_share")
    def test_post_share_delegates_to_the_sourcelens_adapter(self, create_share):
        create_share.return_value = self.share

        response = self.client.post(
            reverse(
                "lens-copilot-session-share",
                kwargs={"pk": self.session.pk},
            ),
            {"title": "Shared answer"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 201)
        create_share.assert_called_once_with(self.session, title="Shared answer")
        self.assertIn("share_path", response.data)
        self.assertNotIn("token", response.data)

    @patch("apps.lens_bridge.services.copilot_sharing.revoke_share")
    def test_delete_share_uses_the_same_hfl_chat_owner(self, revoke_share):
        response = self.client.delete(
            reverse(
                "lens-copilot-session-share-detail",
                kwargs={
                    "pk": self.session.pk,
                    "share_uuid": self.share_uuid,
                },
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 204)
        revoke_share.assert_called_once_with(self.session, str(self.share_uuid))

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_shared_qa_rewrites_files_and_pdf_through_hfl(self, request_json):
        self.session.share_state_json = {
            "version": 1,
            "shares": [self.share],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        access = copilot_sharing.make_share_access_token(self.session, self.share)
        input_uuid = uuid.uuid4()
        output_uuid = uuid.uuid4()
        request_json.return_value = {
            **self.share,
            "question": "Question",
            "answer": "Answer",
            "future_secret": "upstream-private-value",
            "input_attachments": [
                {
                    "uuid": str(input_uuid),
                    "filename": "input.txt",
                    "upstream_url": "/api/lens/public/private-token/input",
                }
            ],
            "output_files": [
                {
                    "uuid": str(output_uuid),
                    "filename": "output.txt",
                    "future_secret": "file-private-value",
                }
            ],
        }

        response = self.client.get(
            reverse("lens-copilot-shared-qa"),
            {"access": access},
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            reverse(
                "lens-copilot-shared-qa-file",
                kwargs={"file_uuid": input_uuid},
            ),
            response.data["input_attachments"][0]["url"],
        )
        self.assertIn(
            reverse(
                "lens-copilot-shared-qa-file",
                kwargs={"file_uuid": output_uuid},
            ),
            response.data["output_files"][0]["url"],
        )
        self.assertIn(reverse("lens-copilot-shared-qa-pdf"), response.data["pdf_url"])
        self.assertNotIn("token", response.data)
        self.assertNotIn("future_secret", response.data)
        self.assertNotIn("upstream_url", response.data["input_attachments"][0])
        self.assertNotIn("future_secret", response.data["output_files"][0])

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    def test_shared_file_bytes_are_streamed_through_the_hfl_proxy(
        self,
        stream_binary,
    ):
        self.session.share_state_json = {
            "version": 1,
            "shares": [self.share],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        access = copilot_sharing.make_share_access_token(self.session, self.share)
        file_uuid = uuid.uuid4()
        stream_binary.return_value = SimpleNamespace(
            body=iter([b"shared-file-bytes"]),
            content_type="text/plain",
            content_length="17",
            content_disposition='attachment; filename="shared.txt"',
        )

        response = self.client.get(
            reverse(
                "lens-copilot-shared-qa-file",
                kwargs={"file_uuid": file_uuid},
            ),
            {"access": access},
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"shared-file-bytes")
        self.assertEqual(response["Cache-Control"], "private, max-age=0, no-store")
        stream_binary.assert_called_once_with(
            f"/api/lens/public/qa/{self.share['token']}/files/{file_uuid}/"
        )

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_another_organization_cannot_open_the_signed_share(
        self,
        request_json,
    ):
        self.session.share_state_json = {
            "version": 1,
            "shares": [self.share],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        access = copilot_sharing.make_share_access_token(self.session, self.share)
        other_user = get_user_model().objects.create_user(
            username="other-share-reader@example.test",
            email="other-share-reader@example.test",
        )
        other_org, _membership = provision_registered_user_tenant(other_user)
        self.client.force_authenticate(user=other_user)

        response = self.client.get(
            reverse("lens-copilot-shared-qa"),
            {"access": access},
            HTTP_X_ORG_KEY=other_org.key,
        )

        self.assertEqual(response.status_code, 404)
        request_json.assert_not_called()
