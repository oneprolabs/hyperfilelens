"""
allauth adapters for social login (Google OAuth).
"""

from __future__ import annotations

import logging
import uuid

from django.utils.translation import gettext as _

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist, PermissionDenied
from django.shortcuts import redirect

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from apps.iam.services.oauth_error_events import build_oauth_error_redirect
from apps.iam.services.registration_service import complete_social_user_registration
from apps.configuration.services.runtime_settings import google_oauth_ready
from common.deploy.site import tenant_public_url

User = get_user_model()
logger = logging.getLogger(__name__)


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return google_oauth_ready()

    def get_provider(self, request, provider, client_id=None):
        if provider != "google":
            return super().get_provider(request, provider, client_id=client_id)
        if not google_oauth_ready():
            raise PermissionDenied("Google OAuth is unavailable")
        try:
            return super().get_provider(request, provider, client_id=client_id)
        except (MultipleObjectsReturned, ObjectDoesNotExist) as exc:
            logger.error(
                "Google OAuth provider configuration is not ready (%s)",
                type(exc).__name__,
            )
            raise PermissionDenied("Google OAuth is not configured") from exc

    def get_login_redirect_url(self, request):
        return settings.LOGIN_REDIRECT_URL

    def get_signup_redirect_url(self, request):
        return settings.LOGIN_REDIRECT_URL

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data or {}
        user.first_name = (extra.get("given_name") or user.first_name or "")[:150]
        user.last_name = (extra.get("family_name") or user.last_name or "")[:150]
        email = (extra.get("email") or user.email or "").strip().lower()
        if email:
            user.email = email

        if not user.username:
            base_username = email.split("@")[0] if email else f"user_{uuid.uuid4().hex[:8]}"
            username = base_username[:150]
            counter = 1
            while User.objects.filter(username=username).exists():
                suffix = f"_{counter}"
                username = f"{base_username[: 150 - len(suffix)]}{suffix}"
                counter += 1
            user.username = username

        return user

    def on_authentication_error(
        self,
        request,
        provider,
        error=None,
        exception=None,
        extra_context=None,
    ):
        reason = "oauth_failed"
        extra = extra_context or {}
        if extra.get("state_id") and exception is None:
            reason = "state_lost"
        elif exception is not None:
            message = str(exception).lower()
            if "invalid_grant" in message or "redirect_uri" in message:
                reason = "invalid_grant"

        logger.warning(
            "Google OAuth authentication error (reason=%s, error=%s, exception=%r, extra=%s)",
            reason,
            error,
            exception,
            extra,
        )
        raise ImmediateHttpResponse(
            redirect(build_oauth_error_redirect(tenant_public_url(), reason))
        )

    def pre_social_login(self, request, sociallogin):
        email = (
            sociallogin.account.extra_data.get("email")
            or sociallogin.user.email
            or ""
        ).strip().lower()
        if not email:
            raise ImmediateHttpResponse(
                redirect(
                    build_oauth_error_redirect(settings.FRONTEND_URL, "no_email")
                )
            )

        if sociallogin.user.pk:
            complete_social_user_registration(sociallogin.user)

    @transaction.atomic
    def save_user(self, request, sociallogin, form=None):
        from apps.iam.services.member_onboarding import lock_identity_provisioning

        lock_identity_provisioning()
        email = str(sociallogin.user.email or "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise PermissionDenied(_("An account already uses this email. Sign in to that account first."))
        user = super().save_user(request, sociallogin, form)
        complete_social_user_registration(user)
        return user
