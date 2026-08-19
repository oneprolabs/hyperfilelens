"""
User-related views.
"""

import logging

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.auth.serializers import UserDetailsSerializer
from apps.iam.profile_models import Profile

logger = logging.getLogger(__name__)


def _profile_language(user):
    try:
        return user.profile.language
    except Profile.DoesNotExist:
        return None


class CustomUserDetailsView(RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return UserDetailsSerializer

    def get_object(self):
        # Check if auth_error was set (force logout detected)
        auth_error = getattr(self.request, 'auth_error', None)
        if auth_error:
            error_code = auth_error.get("error_code", "OTHER_DEVICE_LOGIN")
            message = auth_error.get("message", _("Your account was logged in from another device"))
            raise AuthenticationFailed(
                detail={"error_code": error_code, "message": message},
                code=error_code.lower(),
            )
        return self.request.user

    def get(self, request, *args, **kwargs):
        user = self.get_object()
        serializer = self.get_serializer(user)
        return Response(serializer.data)

    def perform_update(self, serializer):
        user = self.get_object()
        old_language = _profile_language(user)
        super().perform_update(serializer)
        new_language = _profile_language(user)
        if old_language != new_language:
            _sync_answer_language(user, new_language)


def _sync_answer_language(user, language):
    """Best-effort push of the profile language to the SL answer language."""
    if not language:
        return
    try:
        # Imported lazily to avoid a circular import between iam and lens_bridge.
        from apps.lens_bridge.services.chat_user_provisioning import (
            sync_sl_user_language,
        )

        sync_sl_user_language(user, language)
    except Exception:  # noqa: BLE001 - best-effort sync must not break profile edits
        logger.warning(
            "Failed to sync answer language for HFL user %s to SourceLens.",
            user.pk,
            exc_info=True,
        )

