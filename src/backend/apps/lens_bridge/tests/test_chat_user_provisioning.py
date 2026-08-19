from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from apps.lens_bridge.models import LensSlUserLink
from apps.lens_bridge.services import chat_user_provisioning
from apps.lens_bridge.services.sl_client import LensBridgeError


@override_settings(SECRET_KEY="test-hfl-secret")
class ChatUserProvisioningTests(SimpleTestCase):
    def setUp(self):
        chat_user_provisioning.invalidate_user_token(7)
        self.user = SimpleNamespace(pk=7, username="alice")

    @patch("apps.lens_bridge.services.chat_user_provisioning.sl_client.request_json")
    def test_provisions_chat_user_through_management_api(self, request_json):
        request_json.side_effect = [
            {"count": 0, "results": []},
            {
                "id": 23,
                "username": "hfl-u-7",
                "email": "hfl-u-7@users.hyperfilelens.invalid",
                "language": "en-US",
            },
        ]
        link = Mock(sl_username="hfl-u-7", sl_email="")

        chat_user_provisioning._provision_remote(
            self.user,
            link=link,
            gateway_operator=False,
        )

        self.assertEqual(
            request_json.call_args_list,
            [
                call(
                    "GET",
                    "/api/v1/management/users/",
                    params={"page": 1, "page_size": 100},
                ),
                call(
                    "POST",
                    "/api/v1/management/users/",
                    json_body={
                        "username": "hfl-u-7",
                        "email": "hfl-u-7@users.hyperfilelens.invalid",
                        "password": chat_user_provisioning._sl_password_for_hfl_user(
                            self.user
                        ),
                        "is_staff": False,
                        "role_ids": [],
                        "preferred_platform": "workspace",
                        "language": "en-US",
                    },
                ),
            ],
        )
        self.assertEqual(link.sl_user_id, 23)
        self.assertEqual(
            link.sl_email,
            "hfl-u-7@users.hyperfilelens.invalid",
        )
        link.save.assert_called_once()

    @patch("apps.lens_bridge.services.chat_user_provisioning.sl_client.request_json")
    def test_backfills_legacy_chat_user_email(self, request_json):
        request_json.side_effect = [
            {
                "count": 1,
                "results": [{"id": 23, "username": "hfl-u-7", "email": ""}],
            },
            {
                "id": 23,
                "username": "hfl-u-7",
                "email": "hfl-u-7@users.hyperfilelens.invalid",
                "language": "en-US",
            },
        ]
        link = Mock(sl_username="hfl-u-7", sl_email="")

        chat_user_provisioning._provision_remote(
            self.user,
            link=link,
            gateway_operator=False,
        )

        request_json.assert_has_calls(
            [
                call(
                    "PATCH",
                    "/api/v1/management/users/23/",
                    json_body={"email": "hfl-u-7@users.hyperfilelens.invalid"},
                )
            ]
        )
        self.assertEqual(
            link.sl_email,
            "hfl-u-7@users.hyperfilelens.invalid",
        )

    @patch("apps.lens_bridge.services.chat_user_provisioning.sl_client.request_json")
    def test_rejects_unconfirmed_legacy_email_migration(self, request_json):
        legacy_user = {"id": 23, "username": "hfl-u-7", "email": ""}
        invalid_updates = (
            None,
            [],
            {"id": 23, "username": "hfl-u-7", "email": "wrong@example.com"},
        )

        for invalid_update in invalid_updates:
            with self.subTest(invalid_update=invalid_update):
                request_json.reset_mock()
                request_json.side_effect = [
                    {"count": 1, "results": [legacy_user]},
                    invalid_update,
                ]
                link = Mock(sl_username="hfl-u-7", sl_email="")

                with self.assertRaises(LensBridgeError):
                    chat_user_provisioning._provision_remote(
                        self.user,
                        link=link,
                        gateway_operator=False,
                    )

                link.save.assert_not_called()

    @patch("apps.lens_bridge.services.chat_user_provisioning.sl_client.request_json")
    def test_rejects_invalid_remote_user_identifiers_and_counts(self, request_json):
        invalid_counts = (True, -1, "invalid", [])
        for invalid_count in invalid_counts:
            with self.subTest(invalid_count=invalid_count):
                request_json.return_value = {
                    "count": invalid_count,
                    "results": [],
                }
                with self.assertRaises(LensBridgeError):
                    chat_user_provisioning._find_remote_user("hfl-u-7")

        for invalid_user_id in (True, -1, "invalid", []):
            with self.subTest(invalid_user_id=invalid_user_id):
                request_json.return_value = {
                    "count": 1,
                    "results": [
                        {
                            "id": invalid_user_id,
                            "username": "hfl-u-7",
                            "email": "hfl-u-7@users.hyperfilelens.invalid",
                        }
                    ],
                }
                link = Mock(sl_username="hfl-u-7", sl_email="")
                with self.assertRaises(LensBridgeError):
                    chat_user_provisioning._provision_remote(
                        self.user,
                        link=link,
                        gateway_operator=False,
                    )
                link.save.assert_not_called()

    @patch("apps.lens_bridge.services.chat_user_provisioning._provision_remote")
    @patch("apps.lens_bridge.services.chat_user_provisioning.LensSlUserLink.objects")
    def test_error_link_is_retried(self, objects, provision_remote):
        link = Mock(
            provision_status=LensSlUserLink.ProvisionStatus.ERROR,
            gateway_operator=False,
            sl_email="hfl-u-7@users.hyperfilelens.invalid",
        )
        objects.filter.return_value.first.return_value = link

        result = chat_user_provisioning.ensure_sl_chat_user(self.user)

        self.assertIs(result, link)
        provision_remote.assert_called_once_with(
            self.user,
            link=link,
            gateway_operator=False,
        )

    @patch("apps.lens_bridge.services.chat_user_provisioning._provision_remote")
    @patch("apps.lens_bridge.services.chat_user_provisioning.LensSlUserLink.objects")
    def test_ready_link_with_current_email_is_not_reprovisioned(
        self,
        objects,
        provision_remote,
    ):
        link = SimpleNamespace(
            provision_status=chat_user_provisioning.LensSlUserLink.ProvisionStatus.READY,
            gateway_operator=False,
            sl_email="hfl-u-7@users.hyperfilelens.invalid",
        )
        objects.filter.return_value.first.return_value = link

        result = chat_user_provisioning.ensure_sl_chat_user(self.user)

        self.assertIs(result, link)
        provision_remote.assert_not_called()

    @patch("apps.lens_bridge.services.chat_user_provisioning.sl_client.login_user")
    @patch("apps.lens_bridge.services.chat_user_provisioning.ensure_sl_chat_user")
    def test_mints_token_by_logging_in_as_chat_user(self, ensure_user, login_user):
        ensure_user.return_value = SimpleNamespace(
            sl_user_id=23,
            sl_username="hfl-u-7",
            sl_email="hfl-u-7@users.hyperfilelens.invalid",
        )
        login_user.return_value = "chat-access-token"

        token = chat_user_provisioning.mint_sl_access_token(self.user)

        self.assertEqual(token, "chat-access-token")
        login_user.assert_called_once_with(
            email="hfl-u-7@users.hyperfilelens.invalid",
            password=chat_user_provisioning._sl_password_for_hfl_user(self.user),
            legacy_username="hfl-u-7",
        )


class SyncSlUserLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="language-user",
            email="language-user@example.com",
            password="not-used",
        )
        self.link = LensSlUserLink.objects.create(
            hfl_user=self.user,
            sl_user_id=99,
            sl_username=chat_user_provisioning.sl_username_for_hfl_user(self.user),
            sl_email=chat_user_provisioning.sl_email_for_hfl_user(self.user),
            gateway_operator=False,
            provision_status=LensSlUserLink.ProvisionStatus.READY,
        )

    def tearDown(self):
        chat_user_provisioning.invalidate_user_token(self.user.pk)

    @patch("apps.lens_bridge.services.chat_user_provisioning.sl_client.request_json")
    def test_pushes_mapped_language_and_invalidates_token(self, request_json):
        request_json.return_value = {"id": 99, "language": "zh-CN"}
        chat_user_provisioning._USER_TOKENS[self.user.pk] = ("cached-token", 99999)

        result = chat_user_provisioning.sync_sl_user_language(self.user, "zh-hans")

        self.assertTrue(result)
        request_json.assert_called_once_with(
            "PATCH",
            "/api/v1/management/users/99/",
            json_body={"language": "zh-CN"},
        )
        self.assertNotIn(self.user.pk, chat_user_provisioning._USER_TOKENS)

    def test_returns_false_without_ready_link(self):
        self.link.delete()

        self.assertFalse(
            chat_user_provisioning.sync_sl_user_language(self.user, "zh-hans")
        )

    @patch("apps.lens_bridge.services.chat_user_provisioning.sl_client.request_json")
    def test_returns_false_on_remote_failure(self, request_json):
        request_json.side_effect = LensBridgeError("boom")

        self.assertFalse(
            chat_user_provisioning.sync_sl_user_language(self.user, "zh-hans")
        )

    @patch("apps.lens_bridge.services.chat_user_provisioning.sl_client.request_json")
    def test_returns_false_on_invalid_remote_response(self, request_json):
        request_json.return_value = None

        self.assertFalse(
            chat_user_provisioning.sync_sl_user_language(self.user, "en")
        )
