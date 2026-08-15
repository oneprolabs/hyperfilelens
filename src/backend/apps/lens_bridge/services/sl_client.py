"""HTTP client for SourceLens REST/SSE APIs (Bridge admin + per-user chat tokens)."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urljoin

import requests
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import APIException

from apps.lens_bridge import deploy

logger = logging.getLogger(__name__)

_ADMIN_TOKEN_LOCK = threading.Lock()
_ADMIN_ACCESS_TOKEN: str | None = None
_ADMIN_REFRESH_TOKEN: str | None = None
_ADMIN_ACCESS_EXPIRES_AT: float = 0.0


class LensBridgeError(APIException):
    status_code = 502
    default_detail = "SourceLens request failed."
    default_code = "lens_bridge_error"


class LensBridgeNotConfigured(LensBridgeError):
    status_code = 503
    default_detail = "SourceLens bridge is not configured."
    default_code = "lens_bridge_not_configured"


class LensBridgeUnavailable(LensBridgeError):
    status_code = 503
    default_detail = "SourceLens is temporarily unavailable."
    default_code = "lens_bridge_unavailable"


def _transport_error(exc: requests.RequestException) -> LensBridgeUnavailable:
    logger.warning("SourceLens transport failed: %s", exc)
    return LensBridgeUnavailable()


def _base_url() -> str:
    base = deploy.lens_base_url()
    if not base:
        raise LensBridgeNotConfigured()
    return base


def _ensure_credentials() -> None:
    if not deploy.lens_bridge_configured():
        raise LensBridgeNotConfigured()


def _unwrap_sl_body(body: Any) -> Any:
    """Unwrap SourceLens ``{code, message, data}`` envelope when present."""

    if isinstance(body, dict) and "data" in body and "code" in body:
        code = body.get("code")
        if code not in (0, "0", None):
            message = (
                body.get("message")
                or body.get("detail")
                or "SourceLens request failed."
            )
            raise LensBridgeError(str(message))
        return body.get("data")
    return body


def _extract_tokens(payload: dict[str, Any]) -> tuple[str, str | None]:
    access = payload.get("access") or payload.get("access_token")
    refresh = payload.get("refresh") or payload.get("refresh_token")
    if not access:
        raise LensBridgeError("SourceLens login response missing access token.")
    return str(access), str(refresh) if refresh else None


def _decode_login_payload(response: requests.Response) -> Any:
    """Decode an authentication response without exposing upstream content."""
    try:
        return _unwrap_sl_body(response.json())
    except ValueError as exc:
        logger.warning("SourceLens authentication returned invalid JSON.")
        raise LensBridgeUnavailable() from exc


def _login() -> None:
    global _ADMIN_ACCESS_TOKEN, _ADMIN_REFRESH_TOKEN, _ADMIN_ACCESS_EXPIRES_AT
    _ensure_credentials()
    url = urljoin(_base_url() + "/", "api/v1/auth/login")
    try:
        response = requests.post(
            url,
            json={
                "email": deploy.lens_bridge_email(),
                "password": deploy.lens_bridge_password(),
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise _transport_error(exc) from exc
    legacy_username = deploy.lens_bridge_legacy_username()
    if response.status_code in {400, 401} and legacy_username:
        logger.info(
            "SourceLens email login was rejected; retrying the legacy "
            "username credential for an in-place upgrade."
        )
        try:
            response = requests.post(
                url,
                json={
                    "username": legacy_username,
                    "password": deploy.lens_bridge_password(),
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise _transport_error(exc) from exc
    if response.status_code >= 400:
        if response.status_code >= 500:
            raise LensBridgeUnavailable()
        logger.warning(
            "SourceLens login failed: %s %s", response.status_code, response.text[:500]
        )
        raise LensBridgeError("SourceLens authentication failed.")
    payload = _decode_login_payload(response)
    if not isinstance(payload, dict):
        raise LensBridgeError("SourceLens login returned unexpected payload.")
    access, refresh = _extract_tokens(payload)
    _ADMIN_ACCESS_TOKEN = access
    _ADMIN_REFRESH_TOKEN = refresh
    # JWT lifetime unknown; refresh proactively every 25 minutes.
    _ADMIN_ACCESS_EXPIRES_AT = time.time() + 25 * 60


def _refresh_access() -> None:
    global _ADMIN_ACCESS_TOKEN, _ADMIN_REFRESH_TOKEN, _ADMIN_ACCESS_EXPIRES_AT
    if not _ADMIN_REFRESH_TOKEN:
        _login()
        return
    url = urljoin(_base_url() + "/", "api/v1/auth/token/refresh")
    try:
        response = requests.post(
            url,
            json={"refresh": _ADMIN_REFRESH_TOKEN},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise _transport_error(exc) from exc
    if response.status_code >= 400:
        logger.info("SourceLens token refresh failed; re-login.")
        _login()
        return
    try:
        payload = _decode_login_payload(response)
    except LensBridgeUnavailable:
        logger.info("SourceLens token refresh returned invalid JSON; re-login.")
        _login()
        return
    if not isinstance(payload, dict):
        _login()
        return
    access, refresh = _extract_tokens(payload)
    _ADMIN_ACCESS_TOKEN = access
    if refresh:
        _ADMIN_REFRESH_TOKEN = refresh
    _ADMIN_ACCESS_EXPIRES_AT = time.time() + 25 * 60


def login_user(
    *,
    email: str,
    password: str,
    legacy_username: str = "",
) -> str:
    """Authenticate one ordinary SourceLens user and return its access token.

    ``legacy_username`` keeps chat available during the short mixed-version
    window where the new HFL API is live but pre-email SourceLens is still
    draining. It is never attempted after email authentication succeeds.
    """
    url = urljoin(_base_url() + "/", "api/v1/auth/login")
    try:
        response = requests.post(
            url,
            json={"email": email, "password": password},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise _transport_error(exc) from exc
    if response.status_code in {400, 401} and legacy_username:
        logger.info(
            "SourceLens chat user email login was rejected; retrying the "
            "legacy username credential for an in-place upgrade."
        )
        try:
            response = requests.post(
                url,
                json={"username": legacy_username, "password": password},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise _transport_error(exc) from exc
    if response.status_code >= 400:
        if response.status_code >= 500:
            raise LensBridgeUnavailable()
        logger.warning(
            "SourceLens chat user login failed email=%s status=%s",
            email,
            response.status_code,
        )
        raise LensBridgeError("SourceLens chat user authentication failed.")
    try:
        payload = _unwrap_sl_body(response.json())
    except ValueError as exc:
        raise LensBridgeError(
            "SourceLens chat user login returned invalid JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise LensBridgeError("SourceLens chat user login returned unexpected payload.")
    access, _refresh = _extract_tokens(payload)
    return access


def _get_admin_access_token() -> str:
    global _ADMIN_ACCESS_TOKEN
    with _ADMIN_TOKEN_LOCK:
        if not _ADMIN_ACCESS_TOKEN or time.time() >= _ADMIN_ACCESS_EXPIRES_AT:
            if _ADMIN_REFRESH_TOKEN:
                _refresh_access()
            else:
                _login()
        if not _ADMIN_ACCESS_TOKEN:
            raise LensBridgeError("SourceLens access token unavailable.")
        return _ADMIN_ACCESS_TOKEN


def _get_access_token(*, hfl_user: AbstractBaseUser | None = None) -> str:
    if hfl_user is not None:
        from apps.lens_bridge.services.chat_user_provisioning import (
            mint_sl_access_token,
        )

        return mint_sl_access_token(hfl_user)
    return _get_admin_access_token()


def _auth_headers(
    extra: dict[str, str] | None = None,
    *,
    hfl_user: AbstractBaseUser | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {_get_access_token(hfl_user=hfl_user)}"}
    if extra:
        headers.update(extra)
    return headers


def _invalidate_access_token(
    hfl_user: AbstractBaseUser | None,
) -> None:
    if hfl_user is not None:
        from apps.lens_bridge.services.chat_user_provisioning import (
            invalidate_user_token,
        )

        invalidate_user_token(hfl_user.pk)
        return
    with _ADMIN_TOKEN_LOCK:
        global _ADMIN_ACCESS_TOKEN
        _ADMIN_ACCESS_TOKEN = None


def _format_sl_error(body: Any) -> str:
    """Extract a readable message from SourceLens / DRF error payloads."""

    if isinstance(body, (list, tuple)):
        parts = [_format_sl_error(item) for item in body]
        return "; ".join(part for part in parts if part)

    if body is None:
        return ""

    if not isinstance(body, dict):
        return str(body)

    # SourceLens wraps validation errors as {code, message, data}.  The
    # generic message is often just "failed" while data contains the stable
    # product-facing reason (for example ATTACHMENT_DIMENSIONS_TOO_LARGE).
    # Prefer that reason without taking ownership of SourceLens validation.
    if body.get("code") not in (0, "0", None) and "data" in body:
        nested = _format_sl_error(body.get("data"))
        if nested:
            return nested

    non_field = body.get("non_field_errors")
    if isinstance(non_field, list) and non_field:
        return "; ".join(str(item) for item in non_field)

    detail = body.get("detail")
    if detail:
        return str(detail)

    message = body.get("message")
    if message:
        return str(message)

    for field, errors in body.items():
        if field in {"code", "type", "title", "errors", "meta", "data"}:
            continue
        if isinstance(errors, list) and errors:
            return f"{field}: {'; '.join(str(item) for item in errors)}"
        if isinstance(errors, dict):
            nested = _format_sl_error(errors)
            if nested:
                return f"{field}: {nested}"
    return str(body)


def _raise_for_response(response: requests.Response) -> Any:
    if response.status_code < 400:
        if not response.content:
            return None
        try:
            body = response.json()
        except ValueError:
            return response.text
        return _unwrap_sl_body(body)
    if response.status_code >= 500:
        logger.warning("SourceLens upstream returned status=%s", response.status_code)
        raise LensBridgeUnavailable()
    detail = response.text[:2000]
    try:
        body = response.json()
        detail = _format_sl_error(body) or detail
    except ValueError:
        body = detail
    exc = LensBridgeError(detail=str(detail))
    exc.status_code = response.status_code if 400 <= response.status_code < 600 else 502
    raise exc


def request_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 60,
    hfl_user: AbstractBaseUser | None = None,
) -> Any:
    """Authenticated JSON request to SourceLens."""

    if not path.startswith("/"):
        path = f"/{path}"
    url = urljoin(_base_url() + "/", path.lstrip("/"))
    headers = _auth_headers(
        {"Accept": "application/json", **(extra_headers or {})},
        hfl_user=hfl_user,
    )
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    try:
        response = requests.request(
            method.upper(),
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise _transport_error(exc) from exc
    if response.status_code == 401:
        _invalidate_access_token(hfl_user)
        headers = _auth_headers(
            {"Accept": "application/json", **(extra_headers or {})},
            hfl_user=hfl_user,
        )
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = requests.request(
                method.upper(),
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise _transport_error(exc) from exc
    return _raise_for_response(response)


def request_multipart(
    path: str,
    *,
    uploaded_file,
    hfl_user: AbstractBaseUser,
    timeout: int = 120,
) -> Any:
    """Forward one uploaded file to a SourceLens multipart endpoint."""

    if not path.startswith("/"):
        path = f"/{path}"
    url = urljoin(_base_url() + "/", path.lstrip("/"))

    def _send() -> requests.Response:
        uploaded_file.seek(0)
        return requests.post(
            url,
            headers=_auth_headers(
                {"Accept": "application/json"},
                hfl_user=hfl_user,
            ),
            files={
                "file": (
                    str(getattr(uploaded_file, "name", "attachment")),
                    uploaded_file,
                    str(
                        getattr(
                            uploaded_file,
                            "content_type",
                            "application/octet-stream",
                        )
                        or "application/octet-stream"
                    ),
                )
            },
            timeout=timeout,
        )

    try:
        response = _send()
        if response.status_code == 401:
            response.close()
            _invalidate_access_token(hfl_user)
            response = _send()
    except requests.RequestException as exc:
        raise _transport_error(exc) from exc
    return _raise_for_response(response)


@dataclass(frozen=True)
class BinaryStreamResponse:
    """Streaming SourceLens response metadata and byte iterator."""

    body: "_BinaryStreamBody"
    content_type: str
    content_length: str
    content_disposition: str
    cache_control: str


class _BinaryStreamBody(Iterator[bytes]):
    """Close an upstream response even when Django never starts iteration."""

    def __init__(self, response: requests.Response) -> None:
        self._response = response
        self._chunks = iter(response.iter_content(chunk_size=64 * 1024))
        self._closed = False

    def __iter__(self) -> "_BinaryStreamBody":
        return self

    def __next__(self) -> bytes:
        while True:
            try:
                chunk = next(self._chunks)
            except StopIteration:
                self.close()
                raise
            except requests.RequestException as exc:
                self.close()
                raise _transport_error(exc) from exc
            except Exception:
                self.close()
                raise
            if chunk:
                return chunk

    def close(self) -> None:
        """Release the iterator and HTTP response exactly once."""

        if self._closed:
            return
        self._closed = True
        close_chunks = getattr(self._chunks, "close", None)
        try:
            if callable(close_chunks):
                close_chunks()
        finally:
            self._response.close()


def _private_cache_control(value: str) -> str:
    """Keep attachment responses private across the HFL proxy boundary."""

    directives = {
        part.strip().lower()
        for part in str(value or "").split(",")
        if part.strip()
    }
    if "private" in directives and "public" not in directives:
        return value
    return "private, no-store"


def stream_binary(
    path: str,
    *,
    hfl_user: AbstractBaseUser,
    timeout: int = 120,
) -> BinaryStreamResponse:
    """Stream authenticated attachment bytes without persisting them in HFL."""

    if not path.startswith("/"):
        path = f"/{path}"
    url = urljoin(_base_url() + "/", path.lstrip("/"))

    def _send() -> requests.Response:
        return requests.get(
            url,
            headers=_auth_headers(
                {
                    "Accept": "*/*",
                    "Accept-Encoding": "identity",
                },
                hfl_user=hfl_user,
            ),
            stream=True,
            timeout=timeout,
        )

    try:
        response = _send()
        if response.status_code == 401:
            response.close()
            _invalidate_access_token(hfl_user)
            response = _send()
    except requests.RequestException as exc:
        raise _transport_error(exc) from exc
    if response.status_code >= 400:
        try:
            _raise_for_response(response)
        finally:
            response.close()

    return BinaryStreamResponse(
        body=_BinaryStreamBody(response),
        content_type=response.headers.get(
            "Content-Type", "application/octet-stream"
        ),
        content_length=(
            ""
            if response.headers.get("Content-Encoding")
            else response.headers.get("Content-Length", "")
        ),
        content_disposition=response.headers.get("Content-Disposition", ""),
        cache_control=_private_cache_control(
            response.headers.get("Cache-Control", "")
        ),
    )


def list_managed_datasources(*, target_path: str) -> list[dict[str, Any]]:
    """Return managed datasource candidates matching one target path."""

    raw = request_json(
        "GET",
        "/api/lens/admin/datasources/",
        params={
            "filters": json.dumps(
                [{"key": "target_path", "value": target_path}]
            ),
            "page_size": 100,
        },
    )
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("results"), list):
        return [
            row for row in raw["results"] if isinstance(row, dict)
        ]
    return []


def get_managed_datasource(datasource_uuid: str) -> dict[str, Any] | None:
    """Return one SourceLens datasource, treating a missing row as absent."""

    try:
        raw = request_json(
            "GET",
            f"/api/lens/admin/datasources/{datasource_uuid}/",
        )
    except LensBridgeError as exc:
        if exc.status_code == 404:
            return None
        raise
    return raw if isinstance(raw, dict) else None


def create_managed_datasource(
    *,
    name: str,
    lensnode_uuid: str,
    target_path: str,
) -> dict[str, Any]:
    """Create one active SourceLens managed-workspace datasource."""

    raw = request_json(
        "POST",
        "/api/lens/admin/datasources/",
        json_body={
            "name": name,
            "source_type": "managed_workspace",
            "lensnode_uuid": lensnode_uuid,
            "target_path": target_path,
            "status": "active",
            "config": {},
            "sync_policy": {},
        },
    )
    if not isinstance(raw, dict) or not raw.get("uuid"):
        raise LensBridgeError(
            "SourceLens managed datasource create returned no uuid."
        )
    return raw


def start_managed_datasource_conversion(
    *,
    datasource_uuid: str,
    conversion: dict[str, Any],
    operation_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """Start one explicit managed-workspace conversion task.

    SourceLens v0.21 safely ignores the operation headers. A newer SourceLens
    contract can persist them as task metadata and make ambiguous POST recovery
    exact without changing the HFL client again.
    """

    raw = request_json(
        "POST",
        f"/api/lens/admin/datasources/{datasource_uuid}/convert/",
        json_body={"conversion": conversion, "force": force},
        extra_headers={
            "Idempotency-Key": operation_id,
            "X-HFL-Operation-ID": operation_id,
        },
    )
    if not isinstance(raw, dict) or not raw.get("task_id"):
        raise LensBridgeError(
            "SourceLens conversion request returned no task id."
        )
    return raw


def list_managed_datasource_conversion_tasks(
    datasource_uuid: str,
) -> list[dict[str, Any]]:
    """Return recent conversion tasks for one managed datasource."""

    raw = request_json(
        "GET",
        f"/api/lens/admin/datasources/{datasource_uuid}/conversion-tasks/",
        params={"page_size": 100},
    )
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("results"), list):
        return [row for row in raw["results"] if isinstance(row, dict)]
    return []


def get_task_by_id(task_id: str) -> dict[str, Any] | None:
    """Return full SourceLens task state by its stable task identifier."""

    try:
        raw = request_json(
            "GET",
            f"/api/v1/tasks/executions/by-task-id/{task_id}/",
            params={"sync": "false"},
        )
    except LensBridgeError as exc:
        if exc.status_code == 404:
            return None
        raise
    return raw if isinstance(raw, dict) else None


def cancel_managed_datasource_conversion(datasource_uuid: str) -> bool:
    """Cancel active conversion and report whether SourceLens found one."""

    try:
        request_json(
            "POST",
            f"/api/lens/admin/datasources/{datasource_uuid}/cancel-conversion/",
        )
    except LensBridgeError as exc:
        if exc.status_code == 404:
            return False
        raise
    return True


def delete_managed_datasource(datasource_uuid: str) -> None:
    """Delete a SourceLens datasource record idempotently."""

    try:
        request_json(
            "DELETE",
            f"/api/lens/admin/datasources/{datasource_uuid}/",
        )
    except LensBridgeError as exc:
        if exc.status_code == 404:
            return
        raise


def stream_sse(
    path: str,
    *,
    timeout: int = 600,
    hfl_user: AbstractBaseUser | None = None,
) -> Iterator[bytes]:
    """Yield raw SSE bytes from SourceLens."""

    if not path.startswith("/"):
        path = f"/{path}"
    url = urljoin(_base_url() + "/", path.lstrip("/"))
    try:
        response = requests.get(
            url,
            headers=_auth_headers(
                {"Accept": "text/event-stream"},
                hfl_user=hfl_user,
            ),
            stream=True,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise _transport_error(exc) from exc
    if response.status_code >= 400:
        try:
            _raise_for_response(response)
        finally:
            response.close()

    def _iter() -> Iterator[bytes]:
        try:
            try:
                for chunk in response.iter_content(chunk_size=512):
                    if chunk:
                        yield chunk
            except requests.RequestException as exc:
                raise _transport_error(exc) from exc
        finally:
            response.close()

    return _iter()


def ping(*, timeout: int = 10) -> dict[str, Any]:
    """Check base health, bridge authentication, and a real business endpoint."""

    if not deploy.lens_bridge_configured():
        return {"configured": False, "reachable": False}
    try:
        health = requests.get(
            urljoin(_base_url() + "/", "health"),
            timeout=timeout,
        )
        reachable = health.status_code == 200
    except requests.RequestException:
        reachable = False
    token_ok = False
    business_ready = False
    readiness_error = ""
    if reachable:
        try:
            _get_admin_access_token()
            token_ok = True
        except (LensBridgeError, ImproperlyConfigured) as exc:
            logger.warning(
                "SourceLens readiness authentication failed error_type=%s",
                type(exc).__name__,
            )
            readiness_error = "SourceLens authentication is unavailable."
        if token_ok:
            try:
                request_json(
                    "GET",
                    "/api/lens/admin/lensnodes/",
                    params={"page": 1, "page_size": 1},
                    timeout=timeout,
                )
                business_ready = True
            except (LensBridgeError, ImproperlyConfigured) as exc:
                logger.warning(
                    "SourceLens business readiness failed error_type=%s",
                    type(exc).__name__,
                )
                readiness_error = "SourceLens business API is temporarily unavailable."
    return {
        "configured": True,
        "reachable": reachable,
        "authenticated": token_ok,
        "business_ready": business_ready,
        "status": "ready" if business_ready else "degraded",
        "warning": readiness_error,
        "base_url": deploy.lens_base_url(),
    }
