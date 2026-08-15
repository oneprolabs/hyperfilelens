"""Deploy profile API for runtime frontend configuration."""

from __future__ import annotations

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.auth.authentication import OptionalJWTAuthenticationFromCookies
from common.deploy.product import product_edition, product_version
from common.deploy.site import (
    admin_console_entry_visible,
    admin_console_public_url,
    default_landing_path,
    platform_ops_access_allowed,
    platform_ops_landing_path,
    platform_ops_landing_path_for_user,
    resolve_site_role,
    tenant_public_url,
)


class DeployProfileView(APIView):
    """
    GET /api/v1/meta/deploy-profile

    Anonymous-safe fields; authenticated users receive Platform Ops visibility.
    """

    permission_classes = [AllowAny]
    authentication_classes = [OptionalJWTAuthenticationFromCookies]

    def get(self, request):
        from apps.configuration.services.runtime_settings import (
            email_code_login_enabled,
            email_delivery_configured,
            email_signup_enabled,
            password_reset_available,
            platform_ops_enabled,
        )

        site_role = resolve_site_role(request)
        payload = {
            "site_role": site_role,
            "product_version": product_version(),
            "edition": product_edition(),
            "email_signup_enabled": email_signup_enabled() if site_role == "tenant" else False,
            "platform_ops_enabled": platform_ops_enabled(),
            # Tenant-only self-serve reset (EE + SMTP); ops stays password-only.
            "password_reset_available": (
                password_reset_available() if site_role == "tenant" else False
            ),
            "email_code_login_available": bool(
                site_role == "tenant"
                and email_code_login_enabled()
                and email_delivery_configured()
            ),
            "tenant_public_url": tenant_public_url(),
            "admin_console_url": admin_console_public_url(request),
            # Site-local post-login path (tenant "/" vs ops AI Models / Overview).
            "landing_path": default_landing_path(request),
            # Tenant → Admin Console deep link (never tenant "/").
            "admin_console_landing_path": platform_ops_landing_path(),
            "admin_console_entry_visible": False,
            "platform_ops_access_allowed": False,
        }

        if request.user and request.user.is_authenticated:
            payload["is_staff"] = bool(request.user.is_staff)
            payload["admin_console_entry_visible"] = admin_console_entry_visible(
                request,
            )
            payload["platform_ops_access_allowed"] = platform_ops_access_allowed(
                request,
            )
            from common.platform_authz import (
                get_platform_role,
                list_platform_permissions,
            )

            payload["platform_role"] = get_platform_role(request.user)
            payload["platform_permissions"] = list_platform_permissions(request.user)
            # Staff deep-link / ops post-login use role-aware landing.
            payload["admin_console_landing_path"] = platform_ops_landing_path_for_user(
                request.user,
            )
            if payload["platform_ops_access_allowed"]:
                payload["landing_path"] = platform_ops_landing_path_for_user(
                    request.user,
                )
            from apps.iam.constants import SUPPORT_SESSION_KEY

            support_key = request.session.get(SUPPORT_SESSION_KEY)
            if support_key and request.user.is_staff:
                payload["support_org_key"] = support_key
            else:
                payload["support_org_key"] = None
        else:
            payload["is_staff"] = False
            payload["platform_role"] = None
            payload["platform_permissions"] = []
            payload["support_org_key"] = None

        return Response(payload)
