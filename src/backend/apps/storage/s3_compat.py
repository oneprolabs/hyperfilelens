"""Compatibility helpers shared by S3-compatible storage clients."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from botocore.exceptions import ClientError


_BATCH_DELETE_COMPATIBILITY_ERROR_CODES = frozenset(
    {
        "MissingArgument",
        "MissingContentMD5",
        "NotImplemented",
        "UnsupportedArgument",
        "UnsupportedOperation",
    }
)


def _serialized_request_body(body: Any) -> bytes:
    if body is None:
        return b""
    if isinstance(body, str):
        return body.encode("utf-8")
    if isinstance(body, (bytes, bytearray, memoryview)):
        return bytes(body)

    position = body.tell()
    try:
        body.seek(0)
        payload = body.read()
    finally:
        body.seek(position)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return bytes(payload)
    raise TypeError("S3 request body must be bytes or a seekable binary stream.")


def _add_delete_objects_content_md5(request: Any, **_kwargs: Any) -> None:
    """Add the checksum required by the common DeleteObjects protocol."""

    if request.headers.get("Content-MD5"):
        return
    payload = _serialized_request_body(request.body)
    request.headers["Content-MD5"] = base64.b64encode(
        hashlib.md5(payload, usedforsecurity=False).digest()
    ).decode("ascii")


def register_s3_delete_objects_compatibility(client: Any) -> None:
    """Register portable DeleteObjects request handling on an S3 client."""

    client.meta.events.register(
        "before-sign.s3.DeleteObjects",
        _add_delete_objects_content_md5,
        unique_id="hfl-delete-objects-content-md5",
    )


def is_s3_batch_delete_compatibility_error(exc: ClientError) -> bool:
    """Return whether exact single-object deletion is a safe fallback."""

    code = str(exc.response.get("Error", {}).get("Code") or "").strip()
    return code in _BATCH_DELETE_COMPATIBILITY_ERROR_CODES


__all__ = [
    "is_s3_batch_delete_compatibility_error",
    "register_s3_delete_objects_compatibility",
]
