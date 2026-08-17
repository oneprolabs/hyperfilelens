from __future__ import annotations

from dataclasses import dataclass

from botocore.exceptions import (
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
    ReadTimeoutError,
    SSLError,
)

from common.errors import AppError


@dataclass(frozen=True, slots=True)
class S3ValidationFailure:
    code: str
    message: str
    retryable: bool = False
    diagnostic: str = ""


_CREDENTIAL_CODES = {
    "AuthFailure",
    "InvalidAccessKeyId",
    "InvalidSecurity",
    "InvalidToken",
    "SignatureDoesNotMatch",
    "TokenRefreshRequired",
    "UnrecognizedClientException",
}
_PERMISSION_CODES = {
    "AccessDenied",
    "AccessDeniedException",
    "AllAccessDisabled",
    "AuthorizationError",
    "Forbidden",
    "Unauthorized",
}
_BUCKET_MISSING_CODES = {"NoSuchBucket", "NotFound", "XNoSuchBucket"}
_CONFIGURATION_CODES = {
    "AuthorizationHeaderMalformed",
    "IncompleteBody",
    "InvalidArgument",
    "InvalidBucketName",
    "InvalidEndpoint",
    "InvalidLocationConstraint",
    "InvalidRegion",
    "InvalidRequest",
    "MalformedXML",
    "NotImplemented",
    "PermanentRedirect",
    "XMinioInvalidRequest",
}
_CLOCK_SKEW_CODES = {"RequestTimeTooSkewed", "RequestExpired"}


def classify_s3_validation_error(
    exc: Exception,
    *,
    operation: str,
) -> S3ValidationFailure:
    chain = list(_exception_chain(exc))
    error_code = next((code for item in chain if (code := _client_error_code(item))), "")

    if error_code in _CREDENTIAL_CODES or any(
        isinstance(item, (NoCredentialsError, PartialCredentialsError)) for item in chain
    ):
        return S3ValidationFailure(
            "STORAGE.S3_CREDENTIALS_INVALID",
            "Object storage credentials were rejected. Check the Access Key and Secret Key, then try again.",
        )
    if error_code in _BUCKET_MISSING_CODES:
        return S3ValidationFailure(
            "STORAGE.S3_BUCKET_NOT_FOUND",
            "The bucket was not found. Check the bucket name, endpoint, and Region, then try again.",
        )
    if error_code in _PERMISSION_CODES:
        if operation == "bucket_access":
            return S3ValidationFailure(
                "STORAGE.S3_BUCKET_ACCESS_DENIED",
                "The credentials cannot read and write the selected bucket. Grant the required bucket permissions, then try again.",
            )
        return S3ValidationFailure(
            "STORAGE.S3_PERMISSION_DENIED",
            "The credentials cannot list object storage buckets. Grant the required IAM permission, then try again.",
        )
    if any(isinstance(item, (ConnectTimeoutError, ReadTimeoutError)) for item in chain):
        return S3ValidationFailure(
            "STORAGE.S3_TIMEOUT",
            "The object storage request timed out. Check the endpoint and network connectivity, then try again.",
            retryable=True,
        )
    if any(isinstance(item, SSLError) for item in chain):
        return S3ValidationFailure(
            "STORAGE.S3_TLS_FAILED",
            "The TLS certificate could not be verified. Check the endpoint certificate and TLS setting, then try again.",
        )
    if any(
        isinstance(item, (EndpointConnectionError, ConnectionClosedError))
        for item in chain
    ):
        return S3ValidationFailure(
            "STORAGE.S3_NETWORK_UNAVAILABLE",
            "The object storage endpoint could not be reached. Check the endpoint and network connectivity, then try again.",
            retryable=True,
        )
    if error_code in _CLOCK_SKEW_CODES:
        return S3ValidationFailure(
            "STORAGE.S3_CLOCK_SKEW",
            "The object storage request was rejected because the server clock is out of sync. Check the server time and NTP synchronization, then try again.",
            retryable=True,
        )
    if error_code in _CONFIGURATION_CODES or any(
        isinstance(item, (ParamValidationError, TypeError, ValueError)) for item in chain
    ):
        return S3ValidationFailure(
            "STORAGE.S3_CONFIGURATION_INVALID",
            "The object storage connection settings are invalid. Check the endpoint, Region, URL style, and TLS setting, then try again.",
        )
    return S3ValidationFailure(
        "STORAGE.S3_VALIDATION_FAILED",
        "Object storage validation failed. Check the connection settings and IAM permissions, then try again.",
        diagnostic=f"provider_error_code={error_code}" if error_code else "",
    )


def s3_validation_app_error(exc: Exception, *, operation: str) -> AppError:
    failure = classify_s3_validation_error(exc, operation=operation)
    return AppError(
        code=failure.code,
        status=400,
        retryable=failure.retryable,
        title=failure.message,
        diagnostic=failure.diagnostic,
    )


def _exception_chain(exc: Exception):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _client_error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    error = response.get("Error")
    if not isinstance(error, dict):
        return ""
    return str(error.get("Code") or "")
