"""Login organization defaults must be stable and never grant membership."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.iam.access import resolve_membership_for_org
from apps.iam.models import Membership, Organization
from apps.iam.services.login_organizations import login_memberships


class LoginOrganizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="login-org-user")
        self.second = Organization.objects.create(key="z-org", name="A Display Name")
        self.first = Organization.objects.create(key="a-org", name="Z Display Name")
        for org in (self.second, self.first):
            Membership.objects.create(user=self.user, organization=org, role=Membership.Role.OPERATOR)

    def keys(self):
        return [row.organization.key for row in login_memberships(self.user)]

    @patch("apps.iam.services.login_organizations.get_authz_provider", return_value=None)
    def test_uses_stable_keys_instead_of_membership_creation_or_display_order(self, _provider):
        self.assertEqual(self.keys(), ["a-org", "z-org"])
        self.assertEqual(resolve_membership_for_org(self.user, None).organization, self.first)

    @patch("apps.iam.services.login_organizations.get_authz_provider")
    def test_installation_preference_is_only_priority_within_existing_memberships(self, provider):
        provider.return_value = SimpleNamespace(
            preferred_installation_organization=lambda _user: "z-org",
        )
        self.assertEqual(self.keys(), ["z-org", "a-org"])
        self.assertEqual(resolve_membership_for_org(self.user, None).organization, self.second)
        self.assertEqual(resolve_membership_for_org(self.user, "a-org").organization, self.first)

    @patch("apps.iam.services.login_organizations.get_authz_provider")
    def test_installation_preference_cannot_create_membership(self, provider):
        Organization.objects.create(key="not-a-member", name="Not a member")
        provider.return_value = SimpleNamespace(
            preferred_installation_organization=lambda _user: "not-a-member",
        )
        self.assertEqual(self.keys(), ["a-org", "z-org"])

    @patch("apps.iam.services.login_organizations.get_authz_provider", return_value=None)
    def test_excludes_inactive_memberships_and_organizations(self, _provider):
        Membership.objects.filter(user=self.user, organization=self.first).update(is_active=False)
        self.assertEqual(self.keys(), ["z-org"])
        self.second.is_active = False
        self.second.save(update_fields=["is_active"])
        self.assertEqual(self.keys(), [])

    @patch("apps.iam.services.login_organizations.get_authz_provider", return_value=None)
    def test_inactive_user_has_no_login_choices(self, _provider):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.keys(), [])

    @patch("apps.iam.services.login_organizations.get_authz_provider", return_value=None)
    @patch("apps.iam.auth.views.login.password_login_enabled", return_value=True)
    @patch("apps.iam.auth.views.login.credentials_and_turnstile_present", return_value=True)
    @patch("apps.iam.auth.views.login.verify_turnstile_for_action", return_value=True)
    def test_password_login_publishes_the_shared_fallback_order(self, *_mocks):
        self.user.email = "login-org-user@example.test"
        self.user.set_password("LoginPass123")
        self.user.save(update_fields=["email", "password"])
        response = self.client.post(
            reverse("email_login"),
            {"email": self.user.email, "password": "LoginPass123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [row["org_key"] for row in response.data["data"]["available_orgs"]],
            ["a-org", "z-org"],
        )

    @patch("apps.iam.services.login_organizations.get_authz_provider", return_value=None)
    @patch("apps.iam.auth.views.oauth.is_google_oauth_enabled", return_value=True)
    @patch("apps.iam.auth.views.oauth.complete_social_user_registration")
    @patch("apps.iam.auth.token_cookies.record_user_login")
    def test_oauth_uses_the_same_default_instead_of_first_membership_id(
        self, audit, registration, *_mocks,
    ):
        registration.return_value = self.second
        self.client.force_login(self.user)
        response = self.client.get(reverse("oauth_callback"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            parse_qs(urlparse(response["Location"]).query).get("org_key"), ["a-org"],
        )
        self.assertIn("access_token", response.cookies)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["organization"], self.first)
