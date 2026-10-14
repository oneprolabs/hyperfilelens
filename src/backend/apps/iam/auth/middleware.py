"""Authentication bridges for browser-only administrative surfaces."""

from __future__ import annotations

from django.contrib.auth import login as django_login
from rest_framework import exceptions

from apps.iam.auth.authentication import JWTAuthenticationFromCookies
from common.deploy.site import resolve_site_role


class AdminJWTSessionMiddleware:
    """Exchange a valid staff JWT cookie for a Django Admin session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path.startswith("/admin/")
            and resolve_site_role(request) == "ops"
            and not request.user.is_authenticated
        ):
            try:
                authenticated = JWTAuthenticationFromCookies().authenticate(request)
            except (exceptions.AuthenticationFailed, exceptions.PermissionDenied):
                authenticated = None
            if authenticated is not None:
                user, _token = authenticated
                if user.is_active and user.is_staff:
                    django_login(
                        request,
                        user,
                        backend="django.contrib.auth.backends.ModelBackend",
                    )
        return self.get_response(request)
