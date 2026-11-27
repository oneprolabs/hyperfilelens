"""Middleware that binds request-scoped observability context."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress

from django.http import HttpRequest, HttpResponse

from common.observability.context import org_key_var, request_id_var, user_id_var
from common.observability.request_id import get_request_id


class RequestIdMiddleware:
    """Set request ID and optional org/user context for logs and Sentry."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        rid = get_request_id(request)
        request.request_id = rid
        from common.impersonation import attribution

        token_impersonation = attribution.set(None)
        token_rid = request_id_var.set(rid)
        token_org = None
        token_user = None
        try:
            org_key = (request.META.get("HTTP_X_ORG_KEY") or "").strip()
            if not org_key:
                org_key = (request.GET.get("org") or "").strip()
            token_org = org_key_var.set(org_key)

            user_id = ""
            user = getattr(request, "user", None)
            if getattr(user, "is_authenticated", False):
                user_id = str(getattr(user, "id", "") or "")
            token_user = user_id_var.set(user_id)

            from common.impersonation import prepare_request

            response = prepare_request(request)
            if response is None:
                response = self.get_response(request)
            from common.impersonation import guard_stream

            guard_stream(response, request)
        finally:
            attribution.reset(token_impersonation)
            with suppress(ValueError):
                request_id_var.reset(token_rid)
            if token_org is not None:
                with suppress(ValueError):
                    org_key_var.reset(token_org)
            if token_user is not None:
                with suppress(ValueError):
                    user_id_var.reset(token_user)

        if getattr(request, "hfl_impersonation_ended", False):
            response["X-HFL-Impersonation-Ended"] = "true"
        if getattr(request, "hfl_impersonation", None):
            response["Cache-Control"] = "no-store"
        with suppress(Exception):
            response["X-Request-Id"] = rid
        return response
