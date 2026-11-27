"""Optional EE impersonation bridge; never grants Community authority."""

from contextvars import ContextVar
from typing import Any

from rest_framework.exceptions import PermissionDenied

HEADER = "X-HFL-Impersonation"
QUERY = "impersonation"
_provider: Any = None
attribution: ContextVar[dict | None] = ContextVar(
    "impersonation_attribution", default=None
)


def register_impersonation_provider(provider: Any) -> None:
    global _provider
    _provider = provider


def get_impersonation_provider() -> Any:
    return _provider


def end_actor_sessions(actor_id: int, request: Any) -> None:
    """Notify the optional provider after a verified normal-login logout."""
    handler = getattr(_provider, "end_actor_sessions", None)
    if callable(handler):
        handler(actor_id, request)


def selector(request: Any) -> str:
    return str(request.headers.get(HEADER) or request.GET.get(QUERY) or "").strip()


def authenticate_impersonation(request: Any) -> tuple | None:
    if not selector(request):
        return None
    if _provider is None:
        request.hfl_impersonation_ended = True
        getattr(request, "_request", request).hfl_impersonation_ended = True
        raise PermissionDenied("Impersonation is unavailable.")
    raw = getattr(request, "_request", request)
    cached = getattr(raw, "_hfl_impersonation_result", None)
    return cached if cached is not None else _provider.authenticate(request)


def session_payload(request: Any) -> dict | None:
    return getattr(request, "hfl_impersonation", None)


def prepare_request(request: Any):
    """Fence even public/anonymous views before they can use actor cookies."""
    if not selector(request) or not request.path.startswith(
        ("/api/", "/accounts/", "/_allauth/", "/admin/")
    ):
        return None
    if request.path.rstrip("/") in {
        "/api/v1/impersonation/session",
        "/api/v1/impersonation/exchange",
    }:
        return None
    from django.http import JsonResponse
    from rest_framework.exceptions import APIException

    try:
        result = authenticate_impersonation(request)
        request._hfl_impersonation_result = result
        # A selector is transport context, not a business filter. Strict query
        # serializers must see only the original endpoint's supported fields.
        request.META["HTTP_X_HFL_IMPERSONATION"] = selector(request)
        request.__dict__.pop("headers", None)
        if QUERY in request.GET:
            query = request.GET.copy()
            query.pop(QUERY)
            request.GET = query
        request.user = result[0]
    except APIException as exc:
        response = JsonResponse(
            {"code": "IMPERSONATION_DENIED", "error": exc.detail},
            status=exc.status_code,
        )
        if getattr(request, "hfl_impersonation_ended", False):
            response["X-HFL-Impersonation-Ended"] = "true"
        response["Cache-Control"] = "no-store"
        return response
    return None


def guard_stream(response: Any, request: Any) -> None:
    """Recheck long-lived browser streams without touching Agent credentials."""
    if (
        not getattr(response, "streaming", False)
        or not session_payload(request)
        or _provider is None
    ):
        return
    import time
    from rest_framework.exceptions import APIException

    stream = response.streaming_content
    provenance = attribution.get()
    last_checked = 0.0

    def check():
        nonlocal last_checked
        if time.monotonic() - last_checked >= 5:
            _provider.authenticate(request)
            last_checked = time.monotonic()

    if response.is_async:
        from asgiref.sync import sync_to_async

        async def chunks():
            iterator = stream.__aiter__()
            try:
                while True:
                    token = attribution.set(provenance)
                    try:
                        await sync_to_async(check, thread_sensitive=True)()
                        chunk = await anext(iterator)
                        await sync_to_async(check, thread_sensitive=True)()
                    except (StopAsyncIteration, APIException):
                        break
                    finally:
                        attribution.reset(token)
                    yield chunk
            finally:
                close = getattr(iterator, "aclose", None)
                if callable(close):
                    await close()

        response.streaming_content = chunks()
    else:

        def chunks():
            iterator = iter(stream)
            try:
                while True:
                    token = attribution.set(provenance)
                    try:
                        check()
                        chunk = next(iterator)
                        check()
                    except (StopIteration, APIException):
                        break
                    finally:
                        attribution.reset(token)
                    yield chunk
            finally:
                close = getattr(iterator, "close", None)
                if callable(close):
                    close()

        response.streaming_content = chunks()
