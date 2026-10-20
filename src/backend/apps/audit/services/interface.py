"""
Audit write facade — cross-app callers should use this module only.
"""

from __future__ import annotations

from typing import Any

from apps.audit.constants import AuditResult
from apps.audit.models import AuditLog
from apps.audit.services.internal.sanitize import sanitize_request_body
from apps.iam.models import Organization


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return str(forwarded).split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR")


def write_audit_log(
    *,
    organization: Organization | None,
    user=None,
    action: str,
    target_type: str = "",
    target_id: str = "",
    ip_address: str | None = None,
    user_agent: str = "",
    metadata: dict[str, Any] | None = None,
    correlation_id: str = "",
    resource_type: str = "",
    resource_id: str = "",
    resource_name: str = "",
    details: str = "",
    changes: dict[str, Any] | None = None,
    result: str = AuditResult.SUCCESS,
    error_message: str = "",
    error_code: str = "",
    request_method: str = "",
    request_path: str = "",
    request_query: dict[str, Any] | None = None,
    request_body: dict[str, Any] | None = None,
    request_id: str = "",
    session_id: str = "",
) -> AuditLog:
    user_email = ""
    user_name = ""
    if user is not None:
        user_email = getattr(user, "email", "") or ""
        user_name = (
            f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
            or user_email
        )

    rid = str(request_id or correlation_id or "")[:100]
    cid = str(correlation_id or request_id or "")[:36]

    log = AuditLog.objects.create(
        organization=organization,
        user=user,
        user_email=user_email,
        user_name=user_name,
        action=str(action or "").strip(),
        target_type=str(target_type or "").strip(),
        target_id=str(target_id or "").strip(),
        resource_type=str(resource_type or "").strip() or str(target_type or "").strip(),
        resource_id=str(resource_id or "")[:120] or str(target_id or "")[:120],
        resource_name=str(resource_name or "")[:255],
        ip_address=ip_address,
        user_agent=str(user_agent or "")[:500],
        metadata=metadata or {},
        correlation_id=cid,
        request_id=rid,
        details=str(details or ""),
        changes=changes or {},
        result=result or AuditResult.SUCCESS,
        error_message=str(error_message or ""),
        error_code=str(error_code or "")[:50],
        request_method=str(request_method or "")[:10],
        request_path=str(request_path or "")[:1000],
        request_query=request_query or {},
        request_body=sanitize_request_body(request_body or {}),
        session_id=str(session_id or "")[:100],
    )
    try:
        from apps.alert.services.internal.event_alerts import handle_audit_event

        handle_audit_event(
            organization=organization,
            action=str(action or "").strip(),
            resource_type=str(resource_type or "").strip() or str(target_type or "").strip(),
            resource_id=str(resource_id or "")[:120] or str(target_id or "")[:120],
            resource_name=str(resource_name or "")[:255],
            metadata=metadata or {},
        )
    except Exception:
        pass
    return log


def write_audit_log_from_request(
    request,
    *,
    organization: Organization | None,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    resource_name: str = "",
    target_type: str = "",
    target_id: str = "",
    details: str = "",
    changes: dict[str, Any] | None = None,
    result: str = AuditResult.SUCCESS,
    error_message: str = "",
    metadata: dict[str, Any] | None = None,
    user=None,
) -> AuditLog:
    user = user or getattr(request, "user", None)
    if user is not None and not getattr(user, "is_authenticated", True):
        user = None
    query = {}
    if hasattr(request, "GET"):
        try:
            query = dict(request.GET)
        except Exception:
            query = {}
    body = {}
    if hasattr(request, "data"):
        try:
            body = sanitize_request_body(request.data if isinstance(request.data, dict) else dict(request.data))
        except Exception:
            body = {}
    return write_audit_log(
        organization=organization,
        user=user,
        action=action,
        target_type=target_type or resource_type,
        target_id=target_id or resource_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        ip_address=_client_ip(request),
        user_agent=str(request.META.get("HTTP_USER_AGENT", "") or ""),
        metadata=metadata,
        request_id=str(request.META.get("HTTP_X_REQUEST_ID", "") or "")[:100],
        correlation_id=str(request.META.get("HTTP_X_REQUEST_ID", "") or "")[:36],
        details=details,
        changes=changes,
        result=result,
        error_message=error_message,
        request_method=getattr(request, "method", ""),
        request_path=getattr(request, "path", ""),
        request_query=query,
        request_body=body,
        session_id=getattr(getattr(request, "session", None), "session_key", "") or "",
    )
