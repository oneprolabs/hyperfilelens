"""Empty-extension socket must reject, never ignore, delegated selectors."""

from django.test import TestCase
from rest_framework.test import APIClient
from common import impersonation


class CommunityImpersonationBoundaryTests(TestCase):
    def setUp(self):
        previous = impersonation.get_impersonation_provider()
        self.addCleanup(impersonation.register_impersonation_provider, previous)
        impersonation.register_impersonation_provider(None)
        self.client = APIClient()

    def test_selector_is_rejected_before_an_anonymous_logout_can_clear_cookies(self):
        response = self.client.post(
            "/api/v1/auth/logout", {}, format="json", HTTP_X_HFL_IMPERSONATION="unknown"
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("access_token", response.cookies)
        self.assertNotIn("hfl_sessionid", response.cookies)

    def test_query_selector_is_not_silently_ignored_for_direct_downloads(self):
        response = self.client.get("/api/v1/not-a-download?impersonation=unknown")
        self.assertEqual(response.status_code, 403)

    def test_no_enterprise_control_is_available_without_a_provider(self):
        self.assertEqual(
            self.client.post(
                "/api/v1/impersonation/exchange", {}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/api/v1/impersonation/session").status_code, 404
        )
