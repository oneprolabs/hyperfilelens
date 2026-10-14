"""Optional request-local Enterprise query SPI, with Community fallback.

Write callers can request a fresh context after acquiring their authority
locks. No authorization state is cached across requests or on User objects.
"""

from typing import Any

from apps.iam.permissions_org import resolve_org_key
from common.extension_spi import get_authz_provider


def request_authorization(request: Any, *, fresh: bool = False) -> Any:
    provider = get_authz_provider()
    factory = getattr(provider, "authorization_context", None)
    if not callable(factory):
        return None
    key = (id(provider), request.user.pk, resolve_org_key(request))
    cached = getattr(request, "_hfl_query_authorization", None)
    if fresh or cached is None or cached[0] != key:
        context = factory(request.user, key[2])
        request._hfl_query_authorization = (key, context)
        return context
    return cached[1]
