import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.api.views import _shared_article_media_proxy_url
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
    def test_create_share_pins_an_older_turn_even_when_a_newer_answer_exists(self, request_json):
        messages = self._messages()
        older_run = messages[1]["run"]
        request_json.side_effect = [
            messages,
            {"results": [], "next": None},
            {
                "uuid": str(self.share_uuid),
                "run_uuid": older_run,
                "token": "older-turn-token",
            },
        ]
        share = copilot_sharing.create_share(self.session, run_uuid=older_run, title="Older answer")
        self.assertEqual(share["run_uuid"], older_run)
        self.assertEqual(
            request_json.call_args_list[-1],
            call("POST", f"/api/lens/runs/{older_run}/share/",
                 json_body={"title": "Older answer"}, hfl_user=self.user),
        )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_specified_run_must_be_a_completed_answer_in_this_session(self, request_json):
        messages = self._messages()
        running_run = str(uuid.uuid4())
        messages.append({
            "role": "assistant", "run": running_run,
            "content": "Still running", "completed_at": None,
        })
        for run_uuid in (str(uuid.uuid4()), running_run):
            with self.subTest(run_uuid=run_uuid):
                request_json.reset_mock()
                request_json.return_value = messages
                with self.assertRaises(copilot_sharing.CopilotShareNotFoundError):
                    copilot_sharing.create_share(self.session, run_uuid=run_uuid)
                request_json.assert_called_once_with(
                    "GET", f"/api/lens/sessions/{self.session.sl_session_uuid}/messages/",
                    hfl_user=self.user,
                )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_existing_share_recovers_and_preserves_other_chat_share_identities(
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

        share = copilot_sharing.create_share(self.session, run_uuid=str(self.run_uuid))

        self.assertEqual(share["uuid"], str(self.share_uuid))
        self.assertEqual(request_json.call_count, 2)
        self.assertFalse(any(item.args[0] == "DELETE" for item in request_json.call_args_list))
        self.session.refresh_from_db()
        self.assertEqual(
            [row["uuid"] for row in self.session.share_state_json["shares"]],
            [str(self.share_uuid), str(old_share_uuid)],
        )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_candidate_lookup_preserves_all_shared_turns(
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
        self.assertFalse(any(item.args[0] == "DELETE" for item in request_json.call_args_list))
        self.session.refresh_from_db()
        self.assertEqual(
            [row["uuid"] for row in self.session.share_state_json["shares"]],
            [str(self.share_uuid), str(old_share_uuid)],
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
            run_uuid=str(self.run_uuid),
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

    def test_share_access_allows_multiple_independent_turns(self):
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

        _, resolved = copilot_sharing.require_active_share_access(
            organization_id=self.org.id,
            raw_token=access,
        )
        self.assertEqual(resolved["share_token"], "current-share-token")
        old_share = self.session.share_state_json["shares"][1]
        _, old_access = copilot_sharing.require_active_share_access(
            organization_id=self.org.id,
            raw_token=copilot_sharing.make_share_access_token(self.session, old_share),
        )
        self.assertEqual(old_access["share_token"], "old-share-token")

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_create_share_preserves_the_previous_chat_share(
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

        copilot_sharing.create_share(self.session, run_uuid=str(self.run_uuid), title="Latest question")

        self.assertFalse(any(item.args[0] == "DELETE" for item in request_json.call_args_list))
        self.session.refresh_from_db()
        self.assertEqual(
            {row["uuid"] for row in self.session.share_state_json["shares"]},
            {str(old_share_uuid), str(self.share_uuid)},
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
                run_uuid=str(self.run_uuid),
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
                run_uuid=str(self.run_uuid),
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
        self.assertEqual(self.session.share_state_json.get("shares", []), [])

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_teardown_revokes_known_shares_without_deleting_hfl_content(
        self,
        request_json,
    ):
        other_share_uuid = str(uuid.uuid4())
        self.session.share_state_json = {
            "version": 1,
            "shares": [
                {
                    "uuid": str(self.share_uuid),
                    "run_uuid": str(self.run_uuid),
                    "token": "share-token",
                },
                {"uuid": other_share_uuid, "run_uuid": str(uuid.uuid4()), "token": "other-token"},
            ],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        revoked = copilot_sharing.revoke_session_shares(self.session)

        self.assertEqual(revoked, 2)
        request_json.assert_has_calls(
            [
                call("DELETE", f"/api/lens/shares/{self.share_uuid}/", hfl_user=self.user),
                call("DELETE", f"/api/lens/shares/{other_share_uuid}/", hfl_user=self.user),
            ],
            any_order=True,
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
    def test_revoke_share_only_removes_the_requested_turn(
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

        request_json.assert_called_once_with(
            "DELETE", f"/api/lens/shares/{self.share_uuid}/", hfl_user=self.user,
        )
        self.session.refresh_from_db()
        self.assertEqual(
            [row["uuid"] for row in self.session.share_state_json["shares"]],
            [str(other_share_uuid)],
        )
        revoked = {"uuid": str(self.share_uuid), "run_uuid": str(self.run_uuid), "token": "share-token"}
        with self.assertRaises(copilot_sharing.CopilotShareNotFoundError):
            copilot_sharing.require_active_share_access(
                organization_id=self.org.id,
                raw_token=copilot_sharing.make_share_access_token(self.session, revoked),
            )
        remaining = self.session.share_state_json["shares"][0]
        copilot_sharing.require_active_share_access(
            organization_id=self.org.id,
            raw_token=copilot_sharing.make_share_access_token(self.session, remaining),
        )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_update_share_title_preserves_other_known_chat_shares(
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
        self.assertFalse(any(item.args[0] == "DELETE" for item in request_json.call_args_list))
        self.session.refresh_from_db()
        self.assertEqual(
            {row["uuid"] for row in self.session.share_state_json["shares"]},
            {str(stale_share_uuid), str(self.share_uuid)},
        )


class CopilotSharingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="concurrent-share@example.test")
        org, _membership = provision_registered_user_tenant(user)
        self.session = LensSessionLink.objects.create(
            organization=org, hfl_user=user, sl_session_uuid=uuid.uuid4(),
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
        )
        self.run_uuid = str(uuid.uuid4())
        self.share_uuid = str(uuid.uuid4())

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_concurrent_creates_preserve_both_turns(self, request_json):
        other_run = str(uuid.uuid4())
        shares = {
            run_uuid: {
                "uuid": str(uuid.uuid4()), "run_uuid": run_uuid,
                "token": f"test-{run_uuid}",
            }
            for run_uuid in (self.run_uuid, other_run)
        }
        started = Event()

        def request(method, path, **kwargs):
            if path.endswith("/messages/"):
                return [
                    message
                    for run_uuid in shares
                    for message in (
                        {"role": "user", "content": "Question", "run": run_uuid},
                        {"role": "assistant", "content": "Answer", "run": run_uuid,
                         "completed_at": "2026-10-08T07:00:00Z"},
                    )
                ]
            if method == "GET":
                return {"results": [], "next": None}
            run_uuid = path.split("/")[-3]
            return shares[run_uuid]

        def create(run_uuid):
            try:
                link = LensSessionLink.objects.get(pk=self.session.pk)
                started.wait(10)
                return copilot_sharing.create_share(link, run_uuid=run_uuid)
            finally:
                connection.close()

        request_json.side_effect = request
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create, self.run_uuid)
            second = pool.submit(create, other_run)
            started.set()
            self.assertEqual(first.result(timeout=15)["run_uuid"], self.run_uuid)
            self.assertEqual(second.result(timeout=15)["run_uuid"], other_run)
        self.session.refresh_from_db()
        self.assertEqual(
            {row["run_uuid"] for row in self.session.share_state_json["shares"]},
            set(shares),
        )
        for share in shares.values():
            copilot_sharing.require_active_share_access(
                organization_id=self.session.organization_id,
                raw_token=copilot_sharing.make_share_access_token(self.session, share),
            )

    @patch("apps.lens_bridge.services.copilot_sharing.sl_client.request_json")
    def test_chat_cleanup_waits_for_share_create_then_revokes_the_recorded_link(self, request_json):
        creating = Event()
        release = Event()
        cleaning = Event()

        def request(method, path, **kwargs):
            if path.endswith("/messages/"):
                return [
                    {"role": "user", "content": "Question"},
                    {"role": "assistant", "content": "Answer", "run": self.run_uuid,
                     "completed_at": "2026-10-08T07:00:00Z"},
                ]
            if method == "GET":
                return {"results": [], "next": None}
            if method == "POST":
                creating.set()
                if not release.wait(10):
                    raise RuntimeError("Timed out waiting to finish share creation")
                return {"uuid": self.share_uuid, "run_uuid": self.run_uuid, "token": "test-token"}
            return None

        def create():
            try:
                link = LensSessionLink.objects.get(pk=self.session.pk)
                return copilot_sharing.create_share(link, run_uuid=self.run_uuid)
            finally:
                connection.close()

        def cleanup():
            try:
                cleaning.set()
                with transaction.atomic():
                    link = LensSessionLink.objects.select_for_update().get(pk=self.session.pk)
                    link.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
                    link.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
                    link.save(update_fields=["cleanup_intent", "lifecycle_status"])
                    return copilot_sharing.revoke_session_shares(link)
            finally:
                connection.close()

        request_json.side_effect = request
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create)
            try:
                self.assertTrue(creating.wait(10))
                second = pool.submit(cleanup)
                self.assertTrue(cleaning.wait(10))
                self.assertFalse(second.done())
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)["uuid"], self.share_uuid)
            self.assertEqual(second.result(timeout=10), 1)
        self.session.refresh_from_db()
        self.assertEqual(self.session.share_state_json["shares"], [])
        self.assertIn(
            call("DELETE", f"/api/lens/shares/{self.share_uuid}/", hfl_user=self.session.hfl_user),
            request_json.call_args_list,
        )
        with self.assertRaises(copilot_sharing.CopilotShareNotFoundError):
            copilot_sharing.create_share(self.session, run_uuid=self.run_uuid)


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
            {"title": "Shared answer", "run_uuid": str(self.run_uuid)},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 201)
        create_share.assert_called_once_with(self.session, run_uuid=str(self.run_uuid), title="Shared answer")
        self.assertIn("share_path", response.data)
        self.assertNotIn("token", response.data)

    @patch("apps.lens_bridge.services.copilot_sharing.create_share")
    def test_create_requires_a_valid_explicit_run_uuid(self, create_share):
        url = reverse("lens-copilot-session-share", kwargs={"pk": self.session.pk})
        for body in ({"title": "Missing Run"}, {"run_uuid": "invalid"}):
            response = self.client.post(
                url, body, format="json", HTTP_X_ORG_KEY=self.org.key,
            )
            self.assertEqual(response.status_code, 400)
        create_share.assert_not_called()

    @patch("apps.lens_bridge.services.copilot_sharing.get_share_candidate")
    def test_candidate_accepts_a_specific_run_and_rejects_invalid_ids(self, get_share_candidate):
        get_share_candidate.return_value = {"shareable": False, "share": None}
        url = reverse("lens-copilot-session-share", kwargs={"pk": self.session.pk})
        response = self.client.get(url, {"run_uuid": str(self.run_uuid)}, HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(response.status_code, 200)
        get_share_candidate.assert_called_once_with(self.session, run_uuid=str(self.run_uuid))
        response = self.client.get(url, {"run_uuid": "invalid"}, HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_share_candidate.call_count, 1)

    @patch("apps.lens_bridge.services.copilot_sharing.create_share")
    def test_another_user_in_the_same_organization_cannot_manage_this_chat(self, create_share):
        user = get_user_model().objects.create_user(username="other-chat-owner@example.test")
        from apps.iam.models import Membership

        Membership.objects.create(organization=self.org, user=user, role=Membership.Role.AUDITOR)
        self.client.force_authenticate(user=user)
        response = self.client.post(
            reverse("lens-copilot-session-share", kwargs={"pk": self.session.pk}),
            {"run_uuid": str(self.run_uuid)}, format="json", HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, 404)
        create_share.assert_not_called()

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_same_organization_member_can_read_snapshot_but_anonymous_cannot(self, request_json):
        from apps.iam.models import Membership

        self.session.share_state_json = {"version": 1, "shares": [self.share]}
        self.session.save(update_fields=["share_state_json", "updated_at"])
        access = copilot_sharing.make_share_access_token(self.session, self.share)
        user = get_user_model().objects.create_user(username="share-viewer@example.test")
        Membership.objects.create(organization=self.org, user=user, role=Membership.Role.AUDITOR)
        self.client.force_authenticate(user=user)
        request_json.return_value = {**self.share, "question": "Shared question", "answer": "Shared answer"}
        with patch("apps.iam.permissions_org.get_effective_role", return_value=Membership.Role.AUDITOR):
            response = self.client.get(
                reverse("lens-copilot-shared-qa"), {"access": access}, HTTP_X_ORG_KEY=self.org.key,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["answer"], "Shared answer")
        self.client.force_authenticate(user=None)
        request_json.reset_mock()
        response = self.client.get(
            reverse("lens-copilot-shared-qa"), {"access": access}, HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertIn(response.status_code, (401, 403))
        request_json.assert_not_called()

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

    @patch("apps.lens_bridge.api.views.sl_client.request_json")
    def test_shared_qa_rewrites_article_media_through_hfl(self, request_json):
        self.session.share_state_json = {
            "version": 1,
            "shares": [self.share],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        access = copilot_sharing.make_share_access_token(self.session, self.share)
        request_json.return_value = {
            **self.share,
            "question": "Question",
            "answer": "![diagram](https://lens.example/media/articles/article-42/diagram.png)",
        }

        response = self.client.get(
            reverse("lens-copilot-shared-qa"),
            {"access": access},
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("shared-qa/article-media/article-42/diagram.png", response.data["answer"])
        self.assertIn("token=", response.data["answer"])
        self.assertIn("access=", response.data["answer"])

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    def test_shared_article_media_is_streamed_through_hfl(self, stream_binary):
        self.session.share_state_json = {
            "version": 1,
            "shares": [self.share],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        access = copilot_sharing.make_share_access_token(self.session, self.share)
        signed_url = _shared_article_media_proxy_url(access, "article-42", "diagram.png")
        stream_binary.return_value = SimpleNamespace(
            body=iter([b"png"]),
            content_type="image/png",
            content_length="3",
            content_disposition="",
        )

        response = self.client.get(
            signed_url,
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"png")
        stream_binary.assert_called_once_with("/media/articles/article-42/diagram.png")

    @patch("apps.lens_bridge.api.views.sl_client.stream_binary")
    def test_shared_article_media_rejects_a_different_access_token(self, stream_binary):
        self.session.share_state_json = {
            "version": 1,
            "shares": [self.share],
        }
        self.session.save(update_fields=["share_state_json", "updated_at"])
        access = copilot_sharing.make_share_access_token(self.session, self.share)
        signed_url = _shared_article_media_proxy_url(access, "article-42", "diagram.png")

        response = self.client.get(
            f"{signed_url}&access=not-the-signed-share",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 404)
        stream_binary.assert_not_called()

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
