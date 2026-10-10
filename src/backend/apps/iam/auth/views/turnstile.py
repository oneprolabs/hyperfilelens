"""Public Turnstile configuration for authentication pages."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.http.public_api import AnonymousPublicViewMixin
from common.i18n.client_lang import activate_request_language

from apps.iam.services.turnstile_verification import (
    turnstile_configured,
    turnstile_required,
)
from apps.configuration.services.runtime_settings import turnstile_site_key


class TurnstileConfigView(AnonymousPublicViewMixin, APIView):
    """Return the effective Turnstile mode for anonymous auth pages."""

    @extend_schema(
        tags=["auth"],
        summary="Get Turnstile configuration",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        activate_request_language(request)
        enabled = turnstile_required(request)
        configured = turnstile_configured() if enabled else False
        data: dict[str, str | bool] = {
            "enabled": enabled,
            "configured": configured,
            "login_exempt": enabled and not turnstile_required(request, action="login"),
        }
        if enabled and configured:
            data["site_key"] = turnstile_site_key()
        response = Response(
            {"code": "0000", "data": data},
            status=status.HTTP_200_OK,
        )
        # The password-login exemption is request/IP specific, never public data.
        response["Cache-Control"] = "private, no-store"
        return response
