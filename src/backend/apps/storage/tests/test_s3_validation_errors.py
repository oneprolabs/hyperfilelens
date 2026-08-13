from django.test import SimpleTestCase

from botocore.exceptions import ClientError, EndpointConnectionError, SSLError

from apps.storage.services.internal.s3_client import S3ClientError
from apps.storage.services.internal.s3_validation_errors import (
    classify_s3_validation_error,
    s3_validation_app_error,
)


def _wrapped_client_error(code: str, message: str = "provider detail") -> S3ClientError:
    upstream = ClientError(
        {"Error": {"Code": code, "Message": message}},
        "ListBuckets",
    )
    try:
        raise S3ClientError("unsafe wrapper text") from upstream
    except S3ClientError as exc:
        return exc


class S3ValidationErrorTests(SimpleTestCase):
    def test_invalid_credentials_are_classified_without_provider_detail(self):
        exc = _wrapped_client_error("InvalidAccessKeyId", "secret-token-value")

        failure = classify_s3_validation_error(exc, operation="list_buckets")
        app_error = s3_validation_app_error(exc, operation="list_buckets")

        self.assertEqual(failure.code, "STORAGE.S3_CREDENTIALS_INVALID")
        self.assertEqual(app_error.code, failure.code)
        self.assertEqual(app_error.title, failure.message)
        self.assertNotIn("secret-token-value", app_error.title)
        self.assertFalse(app_error.diagnostic)

    def test_permission_message_depends_on_validation_operation(self):
        exc = _wrapped_client_error("AccessDenied")

        list_failure = classify_s3_validation_error(exc, operation="list_buckets")
        access_failure = classify_s3_validation_error(exc, operation="bucket_access")

        self.assertEqual(list_failure.code, "STORAGE.S3_PERMISSION_DENIED")
        self.assertEqual(access_failure.code, "STORAGE.S3_BUCKET_ACCESS_DENIED")
        self.assertIn("list", list_failure.message)
        self.assertIn("read and write", access_failure.message)

    def test_network_and_tls_failures_are_distinct(self):
        network = classify_s3_validation_error(
            EndpointConnectionError(endpoint_url="https://s3.example.test"),
            operation="list_buckets",
        )
        tls = classify_s3_validation_error(
            SSLError(endpoint_url="https://s3.example.test", error="certificate failed"),
            operation="bucket_access",
        )

        self.assertEqual(network.code, "STORAGE.S3_NETWORK_UNAVAILABLE")
        self.assertTrue(network.retryable)
        self.assertEqual(tls.code, "STORAGE.S3_TLS_FAILED")
        self.assertNotIn("s3.example.test", network.message)
        self.assertNotIn("certificate failed", tls.message)

    def test_unknown_failure_uses_safe_generic_message(self):
        failure = classify_s3_validation_error(
            S3ClientError("access-key=AKIA_SECRET secret=super-secret upstream payload"),
            operation="bucket_access",
        )

        self.assertEqual(failure.code, "STORAGE.S3_VALIDATION_FAILED")
        self.assertNotIn("AKIA_SECRET", failure.message)
        self.assertNotIn("super-secret", failure.message)
