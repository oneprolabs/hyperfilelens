import base64
import hashlib
import io
import json
from unittest import mock

from botocore.exceptions import ClientError
from django.test import SimpleTestCase

from apps.storage.services.internal.s3_client import (
    S3ClientError,
    _client,
    delete_s3_bucket_if_empty,
    delete_s3_prefix,
    s3_prefix_has_any_state,
)


class _Paginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **_kwargs):
        return iter(self.pages)


class S3PrefixCleanupTests(SimpleTestCase):
    ownership_marker = {
        "deployment_uuid": "deployment",
        "repository_uuid": "repository",
        "location_digest": "digest",
        "format_version": 1,
        "signature": "signature",
    }

    def _delete(self, **kwargs):
        ownership_marker_key = kwargs.pop(
            "ownership_marker_key",
            "repo/.hyperfilelens/repository-owner-v1.json",
        )
        return delete_s3_prefix(
            ownership_marker_key=ownership_marker_key,
            ownership_marker=self.ownership_marker,
            **kwargs,
        )

    def _client(self, *, key_prefix: str = "repo/"):
        client = mock.Mock()
        marker_key = f"{key_prefix}.hyperfilelens/repository-owner-v1.json"
        calls = {
            "list_multipart_uploads": 0,
            "list_object_versions": 0,
            "list_objects_v2": 0,
        }

        def paginator(name):
            if name == "list_multipart_uploads":
                calls[name] += 1
                if calls[name] == 1:
                    return _Paginator(
                        [
                            {
                                "Uploads": [
                                    {"Key": f"{key_prefix}upload", "UploadId": "u-1"}
                                ]
                            }
                        ]
                    )
                return _Paginator([{"Uploads": []}])
            calls[name] += 1
            if name == "list_object_versions":
                if calls[name] == 1:
                    return _Paginator(
                        [
                            {
                                "Versions": [
                                    {"Key": f"{key_prefix}a", "VersionId": "v-1"}
                                ],
                                "DeleteMarkers": [
                                    {"Key": f"{key_prefix}b", "VersionId": "m-1"}
                                ],
                            }
                        ]
                    )
                return _Paginator([{"Versions": [], "DeleteMarkers": []}])
            if calls[name] == 1:
                return _Paginator([{"Contents": [{"Key": f"{key_prefix}c"}]}])
            return _Paginator([{"Contents": [{"Key": marker_key}]}])

        client.get_paginator.side_effect = paginator
        client.get_object.return_value = {
            "Body": io.BytesIO(json.dumps(self.ownership_marker).encode("utf-8"))
        }
        client.delete_objects.return_value = {}
        client.list_object_versions.return_value = {}
        client.list_objects_v2.return_value = {}
        client.list_multipart_uploads.return_value = {}
        return client

    def test_delete_objects_request_contains_content_md5_for_serialized_body(self):
        client = _client(
            endpoint="https://s3.example.test",
            region="us-east-1",
            access_key_id="access-key",
            secret_access_key="secret-key",
            s3_url_style="path",
            use_tls=True,
            timeout_seconds=1,
        )
        captured = {}

        class RequestCaptured(Exception):
            pass

        def capture_request(request, **_kwargs):
            captured["body"] = bytes(request.body)
            captured["content_md5"] = request.headers.get("Content-MD5")
            raise RequestCaptured

        client.meta.events.register(
            "before-send.s3.DeleteObjects",
            capture_request,
            unique_id="test-capture-delete-objects-request",
        )
        try:
            with self.assertRaises(RequestCaptured):
                client.delete_objects(
                    Bucket="bucket",
                    Delete={"Objects": [{"Key": "repo/object"}], "Quiet": True},
                )
        finally:
            client.close()

        expected = base64.b64encode(
            hashlib.md5(captured["body"], usedforsecurity=False).digest()
        )
        if isinstance(captured["content_md5"], str):
            expected = expected.decode("ascii")
        self.assertEqual(captured["content_md5"], expected)

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_deletes_versions_markers_objects_and_uploads_under_normalized_prefix(
        self, build_client
    ):
        client = self._client()
        build_client.return_value = client

        result = self._delete(
            endpoint="https://s3.example.test",
            region="us-east-1",
            bucket="bucket",
            prefix="/repo",
            access_key_id="key",
            secret_access_key="secret",
        )

        self.assertEqual(result["prefix"], "repo/")
        self.assertEqual(result["deleted_versions"], 1)
        self.assertEqual(result["deleted_markers"], 1)
        self.assertEqual(result["deleted_objects"], 2)
        self.assertEqual(result["aborted_uploads"], 1)
        client.abort_multipart_upload.assert_called_once_with(
            Bucket="bucket", Key="repo/upload", UploadId="u-1"
        )
        final_delete = client.delete_objects.call_args_list[-1].kwargs["Delete"][
            "Objects"
        ]
        self.assertEqual(
            final_delete,
            [{"Key": "repo/.hyperfilelens/repository-owner-v1.json"}],
        )
        self.assertFalse(
            hasattr(client, "delete_bucket") and client.delete_bucket.called
        )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_rejects_changed_owner_before_any_delete(self, build_client):
        client = self._client()
        client.get_object.return_value = {
            "Body": io.BytesIO(
                json.dumps(
                    {**self.ownership_marker, "repository_uuid": "other"}
                ).encode("utf-8")
            )
        }
        build_client.return_value = client

        with self.assertRaisesRegex(S3ClientError, "another repository"):
            self._delete(
                endpoint="https://s3.example.test",
                region="us-east-1",
                bucket="bucket",
                prefix="repo/",
                access_key_id="key",
                secret_access_key="secret",
            )

        client.abort_multipart_upload.assert_not_called()
        client.delete_objects.assert_not_called()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_bucket_root_cleanup_uses_empty_listing_prefix_and_root_marker(
        self, build_client
    ):
        marker_key = ".hyperfilelens/repository-owner-v1.json"
        client = self._client(key_prefix="")
        build_client.return_value = client

        result = self._delete(
            endpoint="https://s3.example.test",
            region="us-east-1",
            bucket="bucket",
            prefix="/",
            access_key_id="key",
            secret_access_key="secret",
            ownership_marker_key=marker_key,
        )

        self.assertEqual(result["prefix"], "")
        client.get_object.assert_called_once_with(Bucket="bucket", Key=marker_key)

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_surfaces_partial_delete_errors(self, build_client):
        client = self._client()
        client.delete_objects.return_value = {
            "Errors": [{"Code": "AccessDenied", "Message": "denied"}],
        }
        build_client.return_value = client

        with self.assertRaisesRegex(S3ClientError, "AccessDenied"):
            self._delete(
                endpoint="https://s3.example.test",
                region="us-east-1",
                bucket="bucket",
                prefix="repo/",
                access_key_id="key",
                secret_access_key="secret",
            )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_retains_marker_when_object_listing_makes_no_progress(self, build_client):
        client = mock.Mock()

        def paginator(name):
            if name == "list_multipart_uploads":
                return _Paginator([{"Uploads": []}])
            if name == "list_object_versions":
                return _Paginator([{"Versions": [], "DeleteMarkers": []}])
            return _Paginator(
                [
                    {
                        "Contents": [
                            {"Key": "repo/data"},
                            {"Key": ("repo/.hyperfilelens/repository-owner-v1.json")},
                        ]
                    }
                ]
            )

        client.get_paginator.side_effect = paginator
        client.get_object.return_value = {
            "Body": io.BytesIO(json.dumps(self.ownership_marker).encode("utf-8"))
        }
        client.delete_objects.return_value = {}
        build_client.return_value = client

        with self.assertRaisesRegex(S3ClientError, "make progress"):
            self._delete(
                endpoint="https://s3.example.test",
                region="us-east-1",
                bucket="bucket",
                prefix="repo/",
                access_key_id="key",
                secret_access_key="secret",
            )

        deleted_keys = [
            item["Key"]
            for call in client.delete_objects.call_args_list
            for item in call.kwargs["Delete"]["Objects"]
        ]
        self.assertNotIn(
            "repo/.hyperfilelens/repository-owner-v1.json",
            deleted_keys,
        )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_falls_back_to_individual_delete_when_batch_delete_is_incompatible(
        self, build_client
    ):
        errors = (
            (
                "NotImplemented",
                "A header you provided implies functionality that is not implemented",
            ),
            (
                "MissingContentMD5",
                "Missing required header for this request: Content-Md5.",
            ),
            (
                "MissingArgument",
                "Missing Some Required Arguments.",
            ),
        )
        for code, message in errors:
            with self.subTest(code=code):
                client = self._client()
                client.delete_objects.side_effect = ClientError(
                    {
                        "Error": {"Code": code, "Message": message},
                        "ResponseMetadata": {"HTTPStatusCode": 501},
                    },
                    "DeleteObjects",
                )
                build_client.return_value = client

                self._delete(
                    endpoint="https://s3.example.test",
                    region="us-east-1",
                    bucket="bucket",
                    prefix="repo/",
                    access_key_id="key",
                    secret_access_key="secret",
                )

                self.assertEqual(client.delete_objects.call_count, 3)
                client.delete_object.assert_has_calls(
                    [
                        mock.call(Bucket="bucket", Key="repo/a", VersionId="v-1"),
                        mock.call(Bucket="bucket", Key="repo/b", VersionId="m-1"),
                        mock.call(Bucket="bucket", Key="repo/c"),
                        mock.call(
                            Bucket="bucket",
                            Key="repo/.hyperfilelens/repository-owner-v1.json",
                        ),
                    ]
                )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_deletes_objects_when_version_api_is_unsupported(self, build_client):
        client = self._client()
        unsupported_versions = ClientError(
            {
                "Error": {
                    "Code": "NotImplemented",
                    "Message": "A header you provided implies functionality that is not implemented",
                },
                "ResponseMetadata": {"HTTPStatusCode": 501},
            },
            "ListObjectVersions",
        )
        version_paginator = mock.Mock()
        version_paginator.paginate.side_effect = unsupported_versions
        object_calls = 0

        def paginator(name):
            nonlocal object_calls
            if name == "list_object_versions":
                return version_paginator
            if name == "list_multipart_uploads":
                return _Paginator([{"Uploads": []}])
            object_calls += 1
            if object_calls == 1:
                return _Paginator([{"Contents": [{"Key": "repo/object"}]}])
            return _Paginator(
                [
                    {
                        "Contents": [
                            {"Key": ("repo/.hyperfilelens/repository-owner-v1.json")}
                        ]
                    }
                ]
            )

        client.get_paginator.side_effect = paginator
        client.list_object_versions.side_effect = unsupported_versions
        client.list_objects_v2.return_value = {}
        client.list_multipart_uploads.return_value = {}
        client.delete_objects.return_value = {}
        build_client.return_value = client

        # Backends without versioning support are treated as version-free:
        # current objects are deleted, no version/marker accounting, and the
        # ownership marker itself is removed via a plain object delete.
        result = self._delete(
            endpoint="https://s3.example.test",
            region="us-east-1",
            bucket="bucket",
            prefix="repo/",
            access_key_id="key",
            secret_access_key="secret",
        )
        self.assertEqual(result["deleted_versions"], 0)
        self.assertEqual(result["deleted_markers"], 0)
        self.assertEqual(result["deleted_objects"], 2)
        deleted_keys = [
            item["Key"]
            for call in client.delete_objects.call_args_list
            for item in call.kwargs["Delete"]["Objects"]
        ]
        self.assertEqual(
            sorted(deleted_keys),
            [
                "repo/.hyperfilelens/repository-owner-v1.json",
                "repo/object",
            ],
        )


class S3BucketCleanupTests(SimpleTestCase):
    def _call(self):
        return delete_s3_bucket_if_empty(
            endpoint="https://s3.example.test",
            region="us-east-1",
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
        )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_deletes_bucket_when_all_full_bucket_checks_are_empty(self, build_client):
        client = mock.Mock()
        client.list_objects_v2.return_value = {}
        client.list_object_versions.return_value = {}
        client.list_multipart_uploads.return_value = {}
        build_client.return_value = client

        result = self._call()

        self.assertEqual(result["status"], "deleted")
        client.delete_bucket.assert_called_once_with(Bucket="bucket")

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_skips_bucket_with_objects_outside_repository_prefix(self, build_client):
        client = mock.Mock()
        client.list_objects_v2.return_value = {"Contents": [{"Key": "other/data"}]}
        build_client.return_value = client

        result = self._call()

        self.assertEqual(result["status"], "skipped_not_empty")
        self.assertEqual(result["reason"], "objects_present")
        client.delete_bucket.assert_not_called()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_bucket_not_empty_race_is_recorded_as_skip(self, build_client):
        client = mock.Mock()
        client.list_objects_v2.return_value = {}
        client.list_object_versions.return_value = {}
        client.list_multipart_uploads.return_value = {}
        client.delete_bucket.side_effect = ClientError(
            {
                "Error": {"Code": "BucketNotEmpty", "Message": "not empty"},
                "ResponseMetadata": {"HTTPStatusCode": 409},
            },
            "DeleteBucket",
        )
        build_client.return_value = client

        result = self._call()

        self.assertEqual(result["status"], "skipped_not_empty")
        self.assertEqual(result["reason"], "bucket_became_non_empty")

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_delete_error_is_recorded_without_raising(self, build_client):
        client = mock.Mock()
        client.list_objects_v2.return_value = {}
        client.list_object_versions.return_value = {}
        client.list_multipart_uploads.return_value = {}
        client.delete_bucket.side_effect = ClientError(
            {
                "Error": {"Code": "AccessDenied", "Message": "denied"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            "DeleteBucket",
        )
        build_client.return_value = client

        result = self._call()

        self.assertEqual(result["status"], "failed")
        self.assertIn("AccessDenied", result["reason"])


class S3PrefixStateTests(SimpleTestCase):
    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_detects_hidden_versions_delete_markers_and_uploads(self, build_client):
        for response_field, response_value in (
            ("Versions", [{"Key": "repo/old", "VersionId": "v-1"}]),
            ("DeleteMarkers", [{"Key": "repo/old", "VersionId": "m-1"}]),
        ):
            with self.subTest(response_field=response_field):
                client = mock.Mock()
                client.list_objects_v2.return_value = {}
                client.list_object_versions.return_value = {
                    response_field: response_value
                }
                build_client.return_value = client

                self.assertTrue(self._has_state())

                client.list_multipart_uploads.assert_not_called()

        client = mock.Mock()
        client.list_objects_v2.return_value = {}
        client.list_object_versions.return_value = {}
        client.list_multipart_uploads.return_value = {
            "Uploads": [{"Key": "repo/incomplete", "UploadId": "u-1"}]
        }
        build_client.return_value = client

        self.assertTrue(self._has_state())

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_treats_versions_as_absent_when_version_api_is_unsupported(
        self, build_client
    ):
        # Older S3-compatible servers (e.g. early MinIO releases) do not
        # implement ListObjectVersions. Because the objects listing already
        # proved the Prefix holds no current objects, versions are treated as
        # absent instead of failing the whole probe.
        client = mock.Mock()
        client.list_objects_v2.return_value = {}
        client.list_object_versions.side_effect = ClientError(
            {
                "Error": {
                    "Code": "NotImplemented",
                    "Message": (
                        "A header you provided implies functionality that is not "
                        "implemented"
                    ),
                },
                "ResponseMetadata": {"HTTPStatusCode": 501},
            },
            "ListObjectVersions",
        )
        client.list_multipart_uploads.return_value = {}
        build_client.return_value = client

        self.assertIs(self._has_state(), False)
        client.list_multipart_uploads.assert_called_once()

        # A multipart upload still makes the Prefix count as occupied.
        client.list_multipart_uploads.return_value = {
            "Uploads": [{"Key": "repo/incomplete", "UploadId": "u-1"}]
        }
        self.assertIs(self._has_state(), True)

    def _has_state(self):
        return s3_prefix_has_any_state(
            endpoint="https://s3.example.test",
            region="us-east-1",
            bucket="bucket",
            prefix="repo/",
            access_key_id="key",
            secret_access_key="secret",
        )
