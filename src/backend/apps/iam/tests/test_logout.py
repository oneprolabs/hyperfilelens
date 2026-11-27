"""Browser logout must revoke login authority without a refresh-path cookie."""

from datetime import timedelta
from email.message import Message
from http.cookiejar import CookieJar
from http.cookies import SimpleCookie
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request

import jwt
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.iam.auth.token_cookies import (
    LOGOUT_COOKIE_NAME,
    LOGOUT_COOKIE_PATH,
    LogoutToken,
    set_auth_cookies,
)
from apps.iam.services.token_service import (
    blacklist_all_user_tokens,
    get_token_family_version,
    get_user_token_version,
    is_token_blacklisted,
    store_refresh_token_family,
)


def browser_cookie_header(cookies, *, path, elapsed=0):
    """Apply real cookie expiry/path rules (APIClient does not enforce them)."""
    jar = CookieJar()
    headers = Message()
    for cookie in cookies.values():
        headers.add_header("Set-Cookie", cookie.OutputString())
    jar.extract_cookies(
        SimpleNamespace(info=lambda: headers),
        Request("https://app.test/api/v1/auth/email-login"),
    )
    request = Request("https://app.test" + path)
    with patch("http.cookiejar.time.time", return_value=time.time() + elapsed):
        jar.add_cookie_header(request)
    result = SimpleCookie()
    result.load(request.get_header("Cookie", ""))
    return result


class LogoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="logout-user")
        self.refresh = self.new_login("logout-family")
        self.access = self.refresh.access_token

    def new_login(self, family):
        blacklist_all_user_tokens(self.user.pk, "new_login")
        token = RefreshToken.for_user(self.user)
        token["family_id"] = family
        token["token_version"] = store_refresh_token_family(
            self.user.pk, family, token["jti"],
        )
        return token

    def logout(self):
        return self.client.post("/api/v1/auth/logout", {}, format="json")

    def test_access_cookie_alone_revokes_login_and_blacklists_access(self):
        self.client.cookies["access_token"] = str(self.access)
        version = get_user_token_version(self.user.pk)

        response = self.logout()

        self.assertEqual(response.status_code, 200)
        self.assertGreater(get_user_token_version(self.user.pk), version)
        self.assertTrue(is_token_blacklisted(self.access["jti"]))
        self.assertEqual(response.cookies["access_token"]["max-age"], 0)
        self.assertEqual(response.cookies["refresh_token"]["max-age"], 0)

    def test_expired_access_can_only_revoke_its_still_current_family(self):
        self.access.set_exp(lifetime=-timedelta(seconds=1))
        self.client.cookies["access_token"] = str(self.access)
        version = get_user_token_version(self.user.pk)

        self.assertEqual(self.logout().status_code, 200)

        self.assertGreater(get_user_token_version(self.user.pk), version)

    def auth_cookies(self):
        response = HttpResponse()
        set_auth_cookies(response, str(self.access), str(self.refresh), "logout-family")
        return response.cookies

    def test_browser_removes_expired_access_and_excludes_refresh_from_logout(self):
        cookies = self.auth_cookies()
        version = get_user_token_version(self.user.pk)
        self.client.cookies = browser_cookie_header(
            cookies, path=LOGOUT_COOKIE_PATH,
            elapsed=int(cookies["access_token"]["max-age"]) + 1,
        )
        self.assertNotIn("access_token", self.client.cookies)
        self.assertNotIn("refresh_token", self.client.cookies)
        self.assertIn(LOGOUT_COOKIE_NAME, self.client.cookies)
        self.assertEqual(self.logout().status_code, 200)
        self.assertGreater(get_user_token_version(self.user.pk), version)
        self.assertEqual(self.client.cookies[LOGOUT_COOKIE_NAME]["max-age"], 0)

    def test_logout_proof_is_http_only_strict_and_only_sent_to_logout(self):
        cookies = self.auth_cookies()
        proof = cookies[LOGOUT_COOKIE_NAME]
        self.assertTrue(proof["httponly"])
        self.assertEqual(proof["samesite"], "Strict")
        for path in ("/api/v1/auth/user", "/api/v1/auth/token/refresh", "/api/v1/tasks"):
            self.assertNotIn(
                LOGOUT_COOKIE_NAME, browser_cookie_header(cookies, path=path),
            )
        self.assertEqual(LogoutToken(proof.value)["token_type"], "logout")
        # Even manually substituting it for an access/refresh token is rejected.
        self.client.cookies["access_token"] = proof.value
        self.assertIn(self.client.get("/api/v1/auth/user").status_code, (401, 403))
        self.client.cookies.clear()
        self.client.cookies["refresh_token"] = proof.value
        self.assertEqual(
            self.client.post("/api/v1/auth/token/refresh").status_code, 401,
        )

    def test_old_logout_proof_cannot_revoke_a_new_login(self):
        self.client.cookies[LOGOUT_COOKIE_NAME] = self.auth_cookies()[LOGOUT_COOKIE_NAME].value
        self.new_login("newer-family")
        version = get_user_token_version(self.user.pk)
        self.assertEqual(self.logout().status_code, 200)
        self.assertEqual(get_user_token_version(self.user.pk), version)

    def test_untrusted_logout_proofs_cannot_revoke(self):
        proof = LogoutToken(self.auth_cookies()[LOGOUT_COOKIE_NAME].value)
        forged = jwt.encode(
            proof.payload, "untrusted-signing-key-with-at-least-32-characters",
            algorithm="HS256",
        )
        for raw in (forged, "bad", str(self.access), str(self.refresh)):
            with self.subTest(raw=raw[:12]):
                self.client.cookies[LOGOUT_COOKIE_NAME] = raw
                version = get_user_token_version(self.user.pk)
                self.assertEqual(self.logout().status_code, 200)
                self.assertEqual(get_user_token_version(self.user.pk), version)

    def test_refresh_cookie_fallback_revokes_only_once_when_both_are_sent(self):
        for with_access in (False, True):
            with self.subTest(with_access=with_access):
                refresh = self.new_login(f"refresh-fallback-{with_access}")
                self.client.cookies.clear()
                self.client.cookies["refresh_token"] = str(refresh)
                if with_access:
                    self.client.cookies["access_token"] = str(refresh.access_token)
                version = get_user_token_version(self.user.pk)

                self.assertEqual(self.logout().status_code, 200)

                self.assertEqual(get_user_token_version(self.user.pk), version + 1)

    def test_old_login_cannot_revoke_a_newer_login(self):
        old_access = str(self.access)
        fresh = self.new_login("newer-family")
        version = get_user_token_version(self.user.pk)
        self.client.cookies["access_token"] = old_access
        self.client.cookies["refresh_token"] = str(self.refresh)

        self.assertEqual(self.logout().status_code, 200)

        self.assertEqual(get_user_token_version(self.user.pk), version)
        self.assertEqual(get_token_family_version(self.user.pk, "newer-family"), version)
        self.assertFalse(is_token_blacklisted(fresh.access_token["jti"]))

    def test_access_identity_does_not_revoke_an_unrelated_refresh_account(self):
        other = get_user_model().objects.create_user(username="unrelated-login")
        blacklist_all_user_tokens(other.pk, "new_login")
        refresh = RefreshToken.for_user(other)
        refresh["family_id"] = "unrelated-family"
        refresh["token_version"] = store_refresh_token_family(
            other.pk, "unrelated-family", refresh["jti"],
        )
        other_version = get_user_token_version(other.pk)
        self.client.cookies["access_token"] = str(self.access)
        self.client.cookies["refresh_token"] = str(refresh)

        self.assertEqual(self.logout().status_code, 200)

        self.assertEqual(get_user_token_version(other.pk), other_version)
        self.assertEqual(
            get_token_family_version(other.pk, "unrelated-family"), other_version,
        )
        self.assertGreater(
            get_user_token_version(self.user.pk), self.access["token_version"],
        )

    def test_forged_malformed_and_wrong_type_proofs_never_revoke(self):
        forged = jwt.encode(
            dict(self.access.payload),
            "untrusted-signing-key-with-at-least-32-characters",
            algorithm="HS256",
        )
        for token in (forged, "not-a-token", str(self.refresh)):
            with self.subTest(token=token[:12]):
                self.client.cookies["access_token"] = token
                version = get_user_token_version(self.user.pk)

                self.assertEqual(self.logout().status_code, 200)

                self.assertEqual(get_user_token_version(self.user.pk), version)

    def test_signed_out_logout_is_idempotent(self):
        version = get_user_token_version(self.user.pk)
        for _ in range(2):
            self.assertEqual(self.logout().status_code, 200)
        self.assertEqual(get_user_token_version(self.user.pk), version)

    def test_logout_does_not_require_an_enterprise_provider(self):
        from common.impersonation import (
            get_impersonation_provider,
            register_impersonation_provider,
        )

        previous = get_impersonation_provider()
        self.addCleanup(register_impersonation_provider, previous)
        register_impersonation_provider(None)
        self.client.cookies["access_token"] = str(self.access)

        self.assertEqual(self.logout().status_code, 200)

        self.assertGreater(
            get_user_token_version(self.user.pk), self.access["token_version"],
        )
