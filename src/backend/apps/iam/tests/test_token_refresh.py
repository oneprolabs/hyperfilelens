from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.iam.services.token_service import (
    TokenError,
    blacklist_all_user_tokens,
    check_refresh_token_rotation,
    complete_refresh_token_rotation_claim,
    generate_token_family_id,
    release_refresh_token_rotation_claim,
    store_refresh_token_family,
)


class TokenRefreshTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="refresh-token@test.local",
            email="refresh-token@test.local",
            password="test-pass",
        )

    def _refresh_token(self) -> str:
        family_id = generate_token_family_id()
        refresh = RefreshToken.for_user(self.user)
        refresh["family_id"] = family_id
        token_version = store_refresh_token_family(
            self.user.id,
            family_id,
            refresh.payload.get("jti"),
        )
        refresh["token_version"] = token_version
        return str(refresh)

    def _token_pair(self) -> tuple[str, str]:
        family_id = generate_token_family_id()
        refresh = RefreshToken.for_user(self.user)
        refresh["family_id"] = family_id
        token_version = store_refresh_token_family(
            self.user.id,
            family_id,
            refresh.payload.get("jti"),
        )
        refresh["token_version"] = token_version
        access = AccessToken.for_user(self.user)
        access["token_version"] = token_version
        return str(access), str(refresh)

    def test_refresh_rotates_refresh_cookie_and_allows_consecutive_refreshes(self):
        original_refresh = self._refresh_token()
        self.client.cookies["refresh_token"] = original_refresh

        first = self.client.post("/api/v1/auth/token/refresh")

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.content)
        self.assertIn("access_token", first.cookies)
        self.assertIn("refresh_token", first.cookies)
        rotated_refresh = first.cookies["refresh_token"].value
        self.assertNotEqual(rotated_refresh, original_refresh)

        self.client.cookies["refresh_token"] = rotated_refresh
        second = self.client.post("/api/v1/auth/token/refresh")

        self.assertEqual(second.status_code, status.HTTP_200_OK, second.content)
        self.assertIn("access_token", second.cookies)
        self.assertIn("refresh_token", second.cookies)

    def test_refresh_works_when_access_cookie_is_expired(self):
        refresh = self._refresh_token()
        expired_access = AccessToken.for_user(self.user)
        expired_access.set_exp(lifetime=-timedelta(seconds=1))
        self.client.cookies["access_token"] = str(expired_access)
        self.client.cookies["refresh_token"] = refresh

        response = self.client.post("/api/v1/auth/token/refresh")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertIn("access_token", response.cookies)
        self.assertIn("refresh_token", response.cookies)

    def test_expired_access_cookie_reports_token_expired(self):
        expired_access = AccessToken.for_user(self.user)
        expired_access.set_exp(lifetime=-timedelta(seconds=1))
        self.client.cookies["access_token"] = str(expired_access)

        response = self.client.get("/api/v1/auth/user")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn(b"TOKEN_EXPIRED", response.content)
        self.assertNotIn(b"REFRESH_EXPIRED", response.content)

    def test_missing_access_cookie_reports_plain_unauthorized(self):
        response = self.client.get("/api/v1/auth/user")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn(b"AUTH_401_UNAUTHORIZED", response.content)
        self.assertNotIn(b"REFRESH_EXPIRED", response.content)

    def test_session_probe_reports_signed_out_without_unauthorized_status(self):
        response = self.client.get("/api/v1/auth/token/refresh")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertFalse(response.data["authenticated"])
        self.assertFalse(response.data["refresh_available"])
        self.assertIsNone(response.data["user"])
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_session_probe_returns_user_for_valid_access_cookie(self):
        access, refresh = self._token_pair()
        self.client.cookies["access_token"] = access
        self.client.cookies["refresh_token"] = refresh

        response = self.client.get("/api/v1/auth/token/refresh")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(response.data["authenticated"])
        self.assertTrue(response.data["refresh_available"])
        self.assertEqual(response.data["user"]["id"], self.user.id)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_session_probe_marks_expired_access_as_refreshable(self):
        refresh = self._refresh_token()
        expired_access = AccessToken.for_user(self.user)
        expired_access.set_exp(lifetime=-timedelta(seconds=1))
        self.client.cookies["access_token"] = str(expired_access)
        self.client.cookies["refresh_token"] = refresh

        response = self.client.get("/api/v1/auth/token/refresh")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertFalse(response.data["authenticated"])
        self.assertTrue(response.data["refresh_available"])

    def test_overlapping_refresh_claims_have_one_retryable_loser(self):
        original_refresh = self._refresh_token()
        decoded = RefreshToken(original_refresh)
        family_id = decoded["family_id"]
        token_jti = decoded["jti"]
        barrier = Barrier(2)

        def claim():
            barrier.wait()
            return check_refresh_token_rotation(
                self.user.id,
                family_id,
                token_jti,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: claim(), range(2)))

        winner = next(result for result in results if result[0])
        loser = next(result for result in results if not result[0])
        self.assertEqual(loser, (False, TokenError.REFRESH_CONCURRENT, None))
        self.assertTrue(
            complete_refresh_token_rotation_claim(
                self.user.id,
                family_id,
                token_jti,
                winner[2],
            )
        )

    def test_overlapping_http_refreshes_return_success_and_retryable_conflict(self):
        original_refresh = self._refresh_token()
        first_entered_issuance = Event()
        allow_first_to_finish = Event()
        original_for_user = RefreshToken.for_user

        def issue_after_overlap(user):
            first_entered_issuance.set()
            if not allow_first_to_finish.wait(timeout=5):
                raise TimeoutError("concurrent refresh did not reach the API")
            return original_for_user(user)

        first_client = APIClient()
        first_client.cookies["refresh_token"] = original_refresh
        second_client = APIClient()
        second_client.cookies["refresh_token"] = original_refresh

        with patch(
            "apps.iam.auth.views.login.RefreshToken.for_user",
            side_effect=issue_after_overlap,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                first_future = executor.submit(
                    first_client.post,
                    "/api/v1/auth/token/refresh",
                )
                self.assertTrue(first_entered_issuance.wait(timeout=5))
                try:
                    second = second_client.post("/api/v1/auth/token/refresh")
                finally:
                    allow_first_to_finish.set()
                first = first_future.result(timeout=5)

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.content)
        self.assertIn("refresh_token", first.cookies)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT, second.content)
        self.assertIn(b"REFRESH_CONCURRENT", second.content)
        self.assertEqual(second["Retry-After"], "1")
        self.assertNotIn("access_token", second.cookies)
        self.assertNotIn("refresh_token", second.cookies)

    def test_only_the_claim_owner_can_release_an_inflight_refresh(self):
        original_refresh = self._refresh_token()
        decoded = RefreshToken(original_refresh)
        family_id = decoded["family_id"]
        token_jti = decoded["jti"]
        winner = check_refresh_token_rotation(
            self.user.id,
            family_id,
            token_jti,
        )
        self.assertTrue(winner[0])

        release_refresh_token_rotation_claim(
            self.user.id,
            family_id,
            token_jti,
            winner[2] + 1,
        )
        blocked = check_refresh_token_rotation(
            self.user.id,
            family_id,
            token_jti,
        )
        self.assertEqual(blocked, (False, TokenError.REFRESH_CONCURRENT, None))

        release_refresh_token_rotation_claim(
            self.user.id,
            family_id,
            token_jti,
            winner[2],
        )
        retried = check_refresh_token_rotation(
            self.user.id,
            family_id,
            token_jti,
        )
        self.assertTrue(retried[0])

    def test_stale_owner_cannot_complete_or_release_a_successor_claim(self):
        original_refresh = self._refresh_token()
        decoded = RefreshToken(original_refresh)
        family_id = decoded["family_id"]
        token_jti = decoded["jti"]
        first = check_refresh_token_rotation(
            self.user.id,
            family_id,
            token_jti,
        )
        self.assertTrue(first[0])

        state_key = f"refresh_family:{self.user.id}:{family_id}:used_jti:{token_jti}"
        successor_claim = first[2] + 1
        cache.set(state_key, successor_claim, timeout=30)

        release_refresh_token_rotation_claim(
            self.user.id,
            family_id,
            token_jti,
            first[2],
        )
        self.assertEqual(cache.get(state_key), successor_claim)
        self.assertFalse(
            complete_refresh_token_rotation_claim(
                self.user.id,
                family_id,
                token_jti,
                first[2],
            )
        )
        self.assertEqual(cache.get(state_key), successor_claim)

    def test_reusing_rotated_refresh_token_is_rejected_immediately(self):
        original_refresh = self._refresh_token()
        self.client.cookies["refresh_token"] = original_refresh
        first = self.client.post("/api/v1/auth/token/refresh")
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.content)

        reuse_client = APIClient()
        reuse_client.cookies["refresh_token"] = original_refresh
        reuse = reuse_client.post("/api/v1/auth/token/refresh")

        self.assertEqual(reuse.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn(b"TOKEN_REUSED", reuse.content)
        self.assertEqual(reuse.cookies["refresh_token"]["max-age"], 0)

    def test_refresh_rejects_replay_recorded_before_atomic_claim_upgrade(self):
        original_refresh = self._refresh_token()
        decoded = RefreshToken(original_refresh)
        family_id = decoded["family_id"]
        token_jti = decoded["jti"]
        cache.set(
            f"refresh_family:{self.user.id}:{family_id}:used_jtis",
            {token_jti},
            timeout=60,
        )
        self.client.cookies["refresh_token"] = original_refresh

        response = self.client.post("/api/v1/auth/token/refresh")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn(b"TOKEN_REUSED", response.content)
        self.assertEqual(response.cookies["refresh_token"]["max-age"], 0)

    def test_refresh_issuance_failure_releases_claim_for_retry(self):
        original_refresh = self._refresh_token()
        self.client.cookies["refresh_token"] = original_refresh
        self.client.raise_request_exception = False

        with patch(
            "apps.iam.auth.views.login.RefreshToken.for_user",
            side_effect=RuntimeError("temporary issuance failure"),
        ):
            failed = self.client.post("/api/v1/auth/token/refresh")

        self.assertEqual(failed.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        retried = self.client.post("/api/v1/auth/token/refresh")

        self.assertEqual(retried.status_code, status.HTTP_200_OK, retried.content)
        self.assertIn("refresh_token", retried.cookies)

    def test_new_login_invalidates_old_access_and_refresh_tokens(self):
        old_access, old_refresh = self._token_pair()

        blacklist_all_user_tokens(self.user.id, "new_login")
        self._token_pair()

        old_access_client = APIClient()
        old_access_client.cookies["access_token"] = old_access
        old_access_response = old_access_client.get("/api/v1/iam/orgs/")

        self.assertEqual(old_access_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn(b"OTHER_DEVICE_LOGIN", old_access_response.content)

        old_refresh_client = APIClient()
        old_refresh_client.cookies["refresh_token"] = old_refresh
        old_refresh_response = old_refresh_client.post("/api/v1/auth/token/refresh")

        self.assertEqual(old_refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn(b"OTHER_DEVICE_LOGIN", old_refresh_response.content)
