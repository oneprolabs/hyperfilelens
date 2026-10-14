"""
Authentication via personal API keys (optional DRF authentication class).

Not registered in ``REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`` by default.
Console traffic uses JWT cookies; enable this class only when productizing API
key access (still requires ``X-Org-Key`` on tenant APIs).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed

from apps.iam.models import PersonalApiKey
from apps.iam.permissions_org import resolve_org_key

User = get_user_model()


class PersonalApiKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticate ``Authorization: Bearer <token>`` or ``X-Api-Key`` headers.

    Organization context (``X-Org-Key``) is still required for tenant APIs; this
    class only resolves the user from a personal API key.
    """

    keyword = "Bearer"

    def authenticate(self, request):
        token = self._extract_token(request)
        if not token:
            return None

        key = (
            PersonalApiKey.objects.select_related("user")
            .filter(token=token, is_active=True, user__is_active=True)
            .first()
        )
        if key is None:
            raise AuthenticationFailed("Invalid API key")

        org_key = resolve_org_key(request)
        if org_key:
            from apps.iam.models import Membership, Organization

            org = Organization.objects.filter(key=org_key, is_active=True).first()
            if org is None:
                raise AuthenticationFailed("Organization not found")
            if not Membership.objects.filter(
                user=key.user,
                organization=org,
                is_active=True,
            ).exists():
                raise AuthenticationFailed(
                    "API key user is not a member of the requested organization"
                )

        from apps.iam.services.account_setup import assert_account_ready

        assert_account_ready(key.user)
        return key.user, key

    @staticmethod
    def _extract_token(request) -> str:
        auth = str(request.headers.get("Authorization", "") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return str(request.headers.get("X-Api-Key", "") or "").strip()
