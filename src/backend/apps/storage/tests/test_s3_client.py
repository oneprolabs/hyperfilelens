from types import SimpleNamespace
from unittest import TestCase, mock
from urllib.parse import urlparse

from apps.storage.services.internal.s3_client import (
    S3ClientError,
    _bucket_location_from_xml,
    _client,
    _merge_huawei_bucket_location,
    _register_huawei_bucket_location_compatibility,
    check_s3_bucket_readable,
    create_s3_bucket,
    ensure_s3_bucket,
    list_s3_buckets,
    list_s3_buckets_by_region,
    put_s3_object_if_absent,
    s3_prefix_contains_only_key,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
    S3UrlStyleProbeError,
    initialize_s3_repository,
    resolve_s3_url_style,
    validate_s3_connection,
)
from apps.storage.repositories.models import Repository
from botocore.exceptions import ClientError


class S3ClientUrlStyleTests(TestCase):
    @mock.patch("apps.storage.services.internal.s3_client.boto3.client")
    def test_list_s3_buckets_uses_auto_style_by_default(self, boto_client):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": [{"Name": "bucket-a"}]}
        boto_client.return_value = client

        buckets = list_s3_buckets(
            endpoint="https://obs.cn-north-5.myhuaweicloud.com",
            region="cn-north-5",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["bucket-a"])
        config = boto_client.call_args.kwargs["config"]
        self.assertEqual(config.s3["addressing_style"], "auto")
        self.assertEqual(config.request_checksum_calculation, "when_required")
        self.assertEqual(config.response_checksum_validation, "when_required")
        self.assertEqual(config.max_pool_connections, 10)

    @mock.patch("apps.storage.services.internal.s3_client.boto3.client")
    def test_list_s3_buckets_honors_path_style(self, boto_client):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": []}
        boto_client.return_value = client

        list_s3_buckets(
            endpoint="https://s3.example.com",
            region="us-east-1",
            access_key_id="AK",
            secret_access_key="SK",
            s3_url_style="path",
        )

        config = boto_client.call_args.kwargs["config"]
        self.assertEqual(config.s3["addressing_style"], "path")

    @mock.patch("apps.storage.services.internal.s3_client.boto3.client")
    def test_list_s3_buckets_honors_virtual_hosted_style(self, boto_client):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": []}
        boto_client.return_value = client

        list_s3_buckets(
            endpoint="https://obs.cn-north-5.myhuaweicloud.com",
            region="cn-north-5",
            access_key_id="AK",
            secret_access_key="SK",
            s3_url_style="virtual_hosted",
        )

        config = boto_client.call_args.kwargs["config"]
        self.assertEqual(config.s3["addressing_style"], "virtual")

    @mock.patch("apps.storage.services.internal.s3_client.boto3.client")
    def test_list_s3_buckets_wraps_sdk_redirect_type_error(self, boto_client):
        client = mock.Mock()
        client.list_buckets.side_effect = TypeError(
            "expected string or bytes-like object, got 'NoneType'"
        )
        boto_client.return_value = client

        with self.assertRaises(S3ClientError) as ctx:
            list_s3_buckets(
                endpoint="192.0.2.81:9443",
                region="",
                access_key_id="002",
                secret_access_key="not-a-real-secret",
                s3_url_style="auto",
                use_tls=False,
            )

        self.assertIsInstance(ctx.exception.__cause__, TypeError)


class HuaweiBucketLocationAddressingTests(TestCase):
    def test_get_bucket_location_uses_virtual_hosted_addressing(self):
        client = _client(
            endpoint="obs.cn-north-9.myhuaweicloud.com",
            region="cn-north-9",
            access_key_id="AK",
            secret_access_key="SK",
            s3_url_style="virtual_hosted",
            use_tls=True,
            timeout_seconds=5,
        )
        _register_huawei_bucket_location_compatibility(client)

        url = client.generate_presigned_url(
            "get_bucket_location",
            Params={"Bucket": "region-test-bucket"},
            ExpiresIn=60,
        )

        parsed = urlparse(url)
        self.assertEqual(
            parsed.hostname,
            "region-test-bucket.obs.cn-north-9.myhuaweicloud.com",
        )
        self.assertEqual(parsed.path, "/")

    def test_parses_wrapped_huawei_location_constraint(self):
        response_body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <GetBucketLocationOutput xmlns="http://obs.myhuaweicloud.com/doc/2015-06-30/">
          <LocationConstraint>cn-north-9</LocationConstraint>
        </GetBucketLocationOutput>
        """
        parsed = {"LocationConstraint": None}

        _merge_huawei_bucket_location(
            parsed,
            SimpleNamespace(status_code=200, content=response_body),
        )

        self.assertEqual(parsed["LocationConstraint"], "cn-north-9")

    def test_parses_huawei_location_root_text(self):
        response_body = (
            b'<Location xmlns="http://obs.myhuaweicloud.com/doc/2015-06-30/">'
            b"cn-south-1</Location>"
        )

        self.assertEqual(
            _bucket_location_from_xml(response_body),
            "cn-south-1",
        )


class S3BucketRegionListingTests(TestCase):
    @staticmethod
    def _merge_raw_list_response(client, parsed, xml):
        handler = client.meta.events.register.call_args.args[1]
        handler(
            parsed,
            SimpleNamespace(status_code=200, content=xml.encode("utf-8")),
        )
        return parsed

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_aws_uses_native_region_filter_and_pagination(self, client_factory):
        client = mock.Mock()
        client.list_buckets.side_effect = [
            {
                "Buckets": [{"Name": "bucket-z", "BucketRegion": "us-west-2"}],
                "ContinuationToken": "next-page",
            },
            {"Buckets": [{"Name": "bucket-a"}]},
        ]
        client_factory.return_value = client

        buckets = list_s3_buckets_by_region(
            platform="aws",
            endpoint="s3.us-west-2.amazonaws.com",
            region="us-west-2",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["bucket-a", "bucket-z"])
        self.assertEqual(
            client.list_buckets.call_args_list,
            [
                mock.call(BucketRegion="us-west-2", MaxBuckets=1000),
                mock.call(
                    BucketRegion="us-west-2",
                    MaxBuckets=1000,
                    ContinuationToken="next-page",
                ),
            ],
        )
        client.get_bucket_location.assert_not_called()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_falls_back_when_native_region_filter_is_unsupported(self, client_factory):
        client = mock.Mock()
        unsupported = ClientError(
            {
                "Error": {"Code": "NotImplemented", "Message": "unsupported"},
                "ResponseMetadata": {"HTTPStatusCode": 501},
            },
            "ListBuckets",
        )
        client.list_buckets.side_effect = [
            unsupported,
            {"Buckets": [{"Name": "bucket-a", "BucketRegion": "us-east-1"}]},
        ]
        client_factory.return_value = client

        buckets = list_s3_buckets_by_region(
            platform="aws",
            endpoint="s3.amazonaws.com",
            region="us-east-1",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["bucket-a"])
        self.assertEqual(client.list_buckets.call_count, 2)

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_native_authentication_error_does_not_fall_back(self, client_factory):
        client = mock.Mock()
        client.list_buckets.side_effect = ClientError(
            {
                "Error": {"Code": "InvalidAccessKeyId", "Message": "invalid"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            "ListBuckets",
        )
        client_factory.return_value = client

        with self.assertRaisesRegex(S3ClientError, "InvalidAccessKeyId"):
            list_s3_buckets_by_region(
                platform="aws",
                endpoint="s3.amazonaws.com",
                region="us-east-1",
                access_key_id="AK",
                secret_access_key="SK",
            )

        client.list_buckets.assert_called_once_with(
            BucketRegion="us-east-1",
            MaxBuckets=1000,
        )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_filters_metadata_and_queries_only_missing_regions(self, client_factory):
        client = mock.Mock()
        client.list_buckets.return_value = {
            "Buckets": [
                {"Name": "metadata-match", "Location": "oss-cn-hangzhou"},
                {"Name": "alias-match", "BucketRegion": "cn-hangzhou"},
                {"Name": "metadata-other", "Location": "oss-cn-shanghai"},
                {"Name": "lookup-match"},
            ]
        }
        client.get_bucket_location.return_value = {"LocationConstraint": "cn-hangzhou"}
        client_factory.return_value = client

        buckets = list_s3_buckets_by_region(
            platform="aliyun",
            endpoint="oss-cn-hangzhou.aliyuncs.com",
            region="oss-cn-hangzhou",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(
            buckets,
            ["alias-match", "lookup-match", "metadata-match"],
        )
        client.get_bucket_location.assert_called_once_with(Bucket="lookup-match")

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_restores_aliyun_location_from_raw_list_xml(self, client_factory):
        client = mock.Mock()

        def list_buckets(**_kwargs):
            parsed = {
                "Buckets": [
                    {"Name": "catalog-region"},
                    {"Name": "native-region", "BucketRegion": "oss-cn-hangzhou"},
                ]
            }
            return self._merge_raw_list_response(
                client,
                parsed,
                """<?xml version="1.0" encoding="UTF-8"?>
                <ListAllMyBucketsResult xmlns="http://doc.oss-cn-hangzhou.aliyuncs.com">
                  <Buckets>
                    <Bucket>
                      <Name>catalog-region</Name>
                      <Location>oss-cn-hangzhou</Location>
                    </Bucket>
                    <Bucket>
                      <Name>native-region</Name>
                      <Location>oss-cn-shanghai</Location>
                    </Bucket>
                  </Buckets>
                </ListAllMyBucketsResult>""",
            )

        client.list_buckets.side_effect = list_buckets
        client_factory.return_value = client

        buckets = list_s3_buckets_by_region(
            platform="aliyun",
            endpoint="oss-cn-hangzhou.aliyuncs.com",
            region="oss-cn-hangzhou",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["catalog-region", "native-region"])
        client.meta.events.register.assert_called_once_with(
            "after-call.s3.ListBuckets",
            mock.ANY,
            unique_id="hfl-list-buckets-regions",
        )
        client.meta.events.register_last.assert_not_called()
        client.get_bucket_location.assert_not_called()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_restores_huawei_location_from_raw_list_xml(self, client_factory):
        client = mock.Mock()

        def list_buckets(**_kwargs):
            parsed = {"Buckets": [{"Name": "ulanqab"}, {"Name": "beijing"}]}
            return self._merge_raw_list_response(
                client,
                parsed,
                """<?xml version="1.0" encoding="UTF-8"?>
                <ListAllMyBucketsResult xmlns="http://obs.myhuaweicloud.com/doc/2015-06-30/">
                  <Buckets>
                    <Bucket><Name>ulanqab</Name><Location>cn-north-9</Location></Bucket>
                    <Bucket><Name>beijing</Name><Location>cn-north-4</Location></Bucket>
                  </Buckets>
                </ListAllMyBucketsResult>""",
            )

        client.list_buckets.side_effect = list_buckets
        client_factory.return_value = client

        buckets = list_s3_buckets_by_region(
            platform="huaweicloud",
            endpoint="obs.cn-north-9.myhuaweicloud.com",
            region="cn-north-9",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["ulanqab"])
        self.assertEqual(
            client.meta.events.register_last.call_args_list,
            [
                mock.call(
                    "before-endpoint-resolution.s3",
                    mock.ANY,
                    unique_id="hfl-huawei-bucket-location-addressing",
                ),
                mock.call(
                    "after-call.s3.GetBucketLocation",
                    mock.ANY,
                    unique_id="hfl-huawei-bucket-location-response",
                ),
            ],
        )
        client.get_bucket_location.assert_not_called()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_uses_redirect_region_header_from_location_lookup(self, client_factory):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": [{"Name": "redirected"}]}
        client.get_bucket_location.side_effect = ClientError(
            {
                "Error": {"Code": "PermanentRedirect", "Message": "redirect"},
                "ResponseMetadata": {
                    "HTTPStatusCode": 301,
                    "HTTPHeaders": {"x-oss-bucket-region": "cn-hangzhou"},
                },
            },
            "GetBucketLocation",
        )
        client_factory.return_value = client

        buckets = list_s3_buckets_by_region(
            platform="aliyun",
            endpoint="oss-cn-hangzhou.aliyuncs.com",
            region="oss-cn-hangzhou",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["redirected"])

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_uses_vendor_endpoint_from_location_error(self, client_factory):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": [{"Name": "redirected"}]}
        client.get_bucket_location.side_effect = ClientError(
            {
                "Error": {
                    "Code": "PermanentRedirect",
                    "Message": "redirect",
                    "Endpoint": "oss-demo.oss-cn-hangzhou.aliyuncs.com",
                },
                "ResponseMetadata": {"HTTPStatusCode": 301},
            },
            "GetBucketLocation",
        )
        client_factory.return_value = client

        buckets = list_s3_buckets_by_region(
            platform="aliyun",
            endpoint="oss-cn-hangzhou.aliyuncs.com",
            region="oss-cn-hangzhou",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["redirected"])

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_excludes_and_logs_bucket_when_location_lookup_fails(self, client_factory):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": [{"Name": "unresolved"}]}
        client.get_bucket_location.side_effect = ClientError(
            {
                "Error": {"Code": "AccessDenied", "Message": "denied"},
                "ResponseMetadata": {
                    "HTTPStatusCode": 403,
                    "RequestId": "request-123",
                },
            },
            "GetBucketLocation",
        )
        client_factory.return_value = client

        with self.assertLogs(
            "apps.storage.services.internal.s3_client", level="WARNING"
        ) as captured:
            buckets = list_s3_buckets_by_region(
                platform="huaweicloud",
                endpoint="obs.cn-south-1.myhuaweicloud.com",
                region="cn-south-1",
                access_key_id="AK",
                secret_access_key="SK",
            )

        self.assertEqual(buckets, [])
        self.assertTrue(any("bucket excluded" in line for line in captured.output))
        self.assertTrue(
            any("error_code=AccessDenied" in line for line in captured.output)
        )
        self.assertTrue(any("http_status=403" in line for line in captured.output))
        self.assertTrue(
            any("request_id=request-123" in line for line in captured.output)
        )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_aws_empty_location_maps_to_us_east_1_after_fallback(self, client_factory):
        client = mock.Mock()
        unsupported = ClientError(
            {
                "Error": {"Code": "UnsupportedOperation", "Message": "unsupported"},
                "ResponseMetadata": {"HTTPStatusCode": 400},
            },
            "ListBuckets",
        )
        client.list_buckets.side_effect = [
            unsupported,
            {"Buckets": [{"Name": "legacy-default-region"}]},
        ]
        client.get_bucket_location.return_value = {"LocationConstraint": None}
        client_factory.return_value = client

        buckets = list_s3_buckets_by_region(
            platform="aws",
            endpoint="s3.amazonaws.com",
            region="us-east-1",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["legacy-default-region"])


class ValidateS3ConnectionRoutingTests(TestCase):
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.list_s3_buckets_by_region"
    )
    def test_managed_provider_uses_region_filtered_listing(self, list_by_region):
        list_by_region.return_value = ["hangzhou-bucket"]

        buckets = validate_s3_connection(
            platform="aliyun",
            endpoint="oss-cn-hangzhou.aliyuncs.com",
            region="oss-cn-hangzhou",
            access_key_id="AK",
            secret_access_key="SK",
            s3_url_style="virtual_hosted",
            use_tls=True,
        )

        self.assertEqual(buckets, ["hangzhou-bucket"])
        list_by_region.assert_called_once_with(
            platform="aliyun",
            endpoint="oss-cn-hangzhou.aliyuncs.com",
            region="oss-cn-hangzhou",
            access_key_id="AK",
            secret_access_key="SK",
            s3_url_style="virtual_hosted",
            use_tls=True,
        )

    @mock.patch("apps.storage.services.internal.repository_initializer.list_s3_buckets")
    def test_custom_provider_keeps_unfiltered_listing(self, list_buckets):
        list_buckets.return_value = ["bucket-a", "bucket-b"]

        buckets = validate_s3_connection(
            platform="custom",
            endpoint="s3.example.com",
            region="region-a",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(buckets, ["bucket-a", "bucket-b"])
        list_buckets.assert_called_once_with(
            endpoint="s3.example.com",
            region="region-a",
            access_key_id="AK",
            secret_access_key="SK",
            s3_url_style=None,
            use_tls=True,
        )


class EnsureS3BucketTests(TestCase):
    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_skips_create_when_bucket_already_listed(self, client_factory):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": [{"Name": "bucket-a"}]}
        client_factory.return_value = client

        ensure_s3_bucket(
            endpoint="https://obs.cn-north-9.myhuaweicloud.com",
            region="cn-north-9",
            bucket="bucket-a",
            access_key_id="AK",
            secret_access_key="SK",
        )

        client.create_bucket.assert_not_called()
        client.head_bucket.assert_not_called()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_creates_bucket_when_missing_from_list(self, client_factory):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": []}
        client_factory.return_value = client

        ensure_s3_bucket(
            endpoint="https://obs.cn-north-9.myhuaweicloud.com",
            region="cn-north-9",
            bucket="new-bucket",
            access_key_id="AK",
            secret_access_key="SK",
            s3_url_style="virtual_hosted",
        )

        client.create_bucket.assert_called_once_with(
            Bucket="new-bucket",
            CreateBucketConfiguration={"LocationConstraint": "cn-north-9"},
        )
        client.head_bucket.assert_not_called()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_treats_bucket_already_owned_as_success(self, client_factory):
        client = mock.Mock()
        client.list_buckets.return_value = {"Buckets": []}
        client.create_bucket.side_effect = ClientError(
            {
                "Error": {"Code": "BucketAlreadyOwnedByYou", "Message": "owned"},
                "ResponseMetadata": {"HTTPStatusCode": 409},
            },
            "CreateBucket",
        )
        client_factory.return_value = client

        ensure_s3_bucket(
            endpoint="https://obs.cn-north-9.myhuaweicloud.com",
            region="cn-north-9",
            bucket="new-bucket",
            access_key_id="AK",
            secret_access_key="SK",
        )


class CreateS3BucketTests(TestCase):
    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_creates_without_reusing_an_existing_bucket(self, client_factory):
        client = mock.Mock()
        client_factory.return_value = client

        created = create_s3_bucket(
            endpoint="https://s3.example.com",
            region="us-east-1",
            bucket="new-bucket",
            access_key_id="AK",
            secret_access_key="SK",
        )

        client.create_bucket.assert_called_once_with(Bucket="new-bucket")
        client.list_buckets.assert_not_called()
        self.assertTrue(created)

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_rejects_an_existing_bucket_name_on_first_attempt(self, client_factory):
        client = mock.Mock()
        client.create_bucket.side_effect = ClientError(
            {
                "Error": {"Code": "BucketAlreadyOwnedByYou", "Message": "owned"},
                "ResponseMetadata": {"HTTPStatusCode": 409},
            },
            "CreateBucket",
        )
        client_factory.return_value = client

        with self.assertRaisesRegex(S3ClientError, "already exists"):
            create_s3_bucket(
                endpoint="https://s3.example.com",
                region="us-east-1",
                bucket="existing-bucket",
                access_key_id="AK",
                secret_access_key="SK",
            )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_recovery_allows_an_existing_owned_bucket(self, client_factory):
        client = mock.Mock()
        client.create_bucket.side_effect = ClientError(
            {
                "Error": {"Code": "BucketAlreadyOwnedByYou", "Message": "owned"},
                "ResponseMetadata": {"HTTPStatusCode": 409},
            },
            "CreateBucket",
        )
        client_factory.return_value = client

        created = create_s3_bucket(
            endpoint="https://s3.example.com",
            region="us-east-1",
            bucket="leftover-bucket",
            access_key_id="AK",
            secret_access_key="SK",
            allow_existing_owned=True,
        )

        client.list_objects_v2.assert_not_called()
        self.assertFalse(created)

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_legacy_gateway_retries_without_location_constraint(self, client_factory):
        client = mock.Mock()
        client.create_bucket.side_effect = [
            ClientError(
                {
                    "Error": {
                        "Code": "InvalidLocationConstraint",
                        "Message": "region not supported",
                    },
                    "ResponseMetadata": {"HTTPStatusCode": 400},
                },
                "CreateBucket",
            ),
            None,
        ]
        client_factory.return_value = client

        create_s3_bucket(
            endpoint="https://minio.example.com",
            region="cn-north-1",
            bucket="new-bucket",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(client.create_bucket.call_count, 2)
        self.assertEqual(
            client.create_bucket.call_args_list[0],
            mock.call(
                Bucket="new-bucket",
                CreateBucketConfiguration={"LocationConstraint": "cn-north-1"},
            ),
        )
        self.assertEqual(
            client.create_bucket.call_args_list[1],
            mock.call(Bucket="new-bucket"),
        )


class S3PrefixContainsOnlyKeyTests(TestCase):
    def _contains_only(self, client) -> bool:
        with mock.patch(
            "apps.storage.services.internal.s3_client._client",
            return_value=client,
        ):
            return s3_prefix_contains_only_key(
                endpoint="https://s3.example.com",
                region="us-east-1",
                bucket="backup-bucket",
                prefix="hfl/",
                allowed_key="hfl/.hyperfilelens/repository-owner-v1.json",
                access_key_id="AK",
                secret_access_key="SK",
            )

    def test_accepts_current_and_historical_state_for_the_allowed_marker(self):
        client = mock.Mock()
        object_pages = mock.Mock()
        object_pages.paginate.return_value = [
            {"Contents": [{"Key": "hfl/.hyperfilelens/repository-owner-v1.json"}]}
        ]
        version_pages = mock.Mock()
        version_pages.paginate.return_value = [
            {
                "Versions": [{"Key": "hfl/.hyperfilelens/repository-owner-v1.json"}],
                "DeleteMarkers": [],
            }
        ]
        client.get_paginator.side_effect = [object_pages, version_pages]
        client.list_multipart_uploads.return_value = {}

        self.assertTrue(self._contains_only(client))

    def test_rejects_any_other_object_in_the_prefix(self):
        client = mock.Mock()
        object_pages = mock.Mock()
        object_pages.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "hfl/.hyperfilelens/repository-owner-v1.json"},
                    {"Key": "hfl/kopia.repository"},
                ]
            }
        ]
        client.get_paginator.return_value = object_pages

        self.assertFalse(self._contains_only(client))
        client.list_multipart_uploads.assert_not_called()


class InitializeS3RepositoryBucketModeTests(TestCase):
    def _repository(self, mode):
        return Repository(
            repo_type=Repository.Type.S3,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="bucket",
            s3_bucket_mode=mode,
            config={
                "region": "us-east-1",
                "prefix": "repo/",
                "s3_url_style": "virtual_hosted",
            },
        )

    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_repository"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_bucket"
    )
    def test_new_mode_strictly_creates_bucket(
        self, create_bucket, check_bucket, _create_repo
    ):
        initialize_s3_repository(self._repository(Repository.S3BucketMode.NEW))

        create_bucket.assert_called_once()
        check_bucket.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_repository"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_bucket"
    )
    def test_existing_mode_never_creates_bucket(
        self, create_bucket, check_bucket, _create_repo
    ):
        initialize_s3_repository(self._repository(Repository.S3BucketMode.EXISTING))

        check_bucket.assert_called_once()
        create_bucket.assert_not_called()


class ResolveS3UrlStyleTests(TestCase):
    _bucket_args = {
        "endpoint": "s3.example.com",
        "region": "us-east-1",
        "bucket": "backup-bucket",
        "access_key_id": "AK",
        "secret_access_key": "SK",
        "use_tls": True,
    }

    @mock.patch("apps.storage.services.internal.repository_initializer.sleep")
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    def test_auto_prefers_virtual_hosted(self, check_bucket, sleep):
        resolved = resolve_s3_url_style(s3_url_style="auto", **self._bucket_args)

        self.assertEqual(resolved, "virtual_hosted")
        self.assertEqual(
            [call.kwargs["s3_url_style"] for call in check_bucket.call_args_list],
            ["path", "virtual_hosted"],
        )
        sleep.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    def test_auto_uses_path_for_ip_literal_endpoint(self, check_bucket):
        resolved = resolve_s3_url_style(
            s3_url_style="auto",
            **{**self._bucket_args, "endpoint": "192.168.8.82:9000"},
        )

        self.assertEqual(resolved, "path")
        check_bucket.assert_called_once_with(
            **{**self._bucket_args, "endpoint": "192.168.8.82:9000"},
            s3_url_style="path",
        )

    @mock.patch("apps.storage.services.internal.repository_initializer.sleep")
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    def test_auto_falls_back_to_path(self, check_bucket, _sleep):
        check_bucket.side_effect = [None] + [S3ClientError("virtual failed")] * 3

        resolved = resolve_s3_url_style(s3_url_style="auto", **self._bucket_args)

        self.assertEqual(resolved, "path")
        self.assertEqual(
            [call.kwargs["s3_url_style"] for call in check_bucket.call_args_list],
            ["path", "virtual_hosted", "virtual_hosted", "virtual_hosted"],
        )

    @mock.patch("apps.storage.services.internal.repository_initializer.sleep")
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    def test_auto_reports_both_probe_failures(self, check_bucket, _sleep):
        check_bucket.side_effect = S3ClientError("unavailable")

        with self.assertRaisesRegex(S3UrlStyleProbeError, "Virtual Hosted.*Path"):
            resolve_s3_url_style(s3_url_style="auto", **self._bucket_args)


class InitializeAutoS3UrlStyleTests(TestCase):
    def _repository(self, mode):
        repository = Repository(
            repo_type=Repository.Type.S3,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="bucket",
            s3_bucket_mode=mode,
            config={"region": "us-east-1", "prefix": "repo/", "s3_url_style": "auto"},
        )
        repository.save = mock.Mock()
        return repository

    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_repository"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    def test_auto_resolution_is_persisted_before_kopia_initialization(
        self, check_bucket, create_repo
    ):
        repository = self._repository(Repository.S3BucketMode.EXISTING)

        initialize_s3_repository(repository)

        self.assertEqual(repository.config["s3_url_style"], "virtual_hosted")
        repository.save.assert_called_once_with(update_fields=["config", "updated_at"])
        self.assertEqual(
            [call.kwargs["s3_url_style"] for call in check_bucket.call_args_list],
            ["path", "virtual_hosted"],
        )
        create_repo.assert_called_once_with(repository)

    @mock.patch("apps.storage.services.internal.repository_initializer.sleep")
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_repository"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.delete_s3_bucket_if_empty"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_bucket"
    )
    def test_new_bucket_auto_probe_failure_rolls_back_created_bucket(
        self, create_bucket, check_bucket, delete_bucket, create_repo, _sleep
    ):
        repository = self._repository(Repository.S3BucketMode.NEW)
        check_bucket.side_effect = S3ClientError("unavailable")
        delete_bucket.return_value = {
            "bucket": "bucket",
            "status": "deleted",
            "reason": "bucket_empty",
        }

        with self.assertRaises(RepositoryInitializationError):
            initialize_s3_repository(repository)

        create_bucket.assert_called_once()
        delete_bucket.assert_called_once()
        create_repo.assert_not_called()

    @mock.patch("apps.storage.services.internal.repository_initializer.sleep")
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_repository"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.delete_s3_bucket_if_empty"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.check_s3_bucket_readable"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_initializer.create_s3_bucket"
    )
    def test_recovery_auto_probe_failure_does_not_delete_reused_bucket(
        self,
        create_bucket,
        check_bucket,
        delete_bucket,
        create_repo,
        _sleep,
    ):
        repository = self._repository(Repository.S3BucketMode.NEW)
        create_bucket.return_value = False
        check_bucket.side_effect = S3ClientError("unavailable")

        with self.assertRaises(RepositoryInitializationError):
            initialize_s3_repository(repository, recovery=True)

        delete_bucket.assert_not_called()
        create_repo.assert_not_called()


class CheckS3BucketReadableTests(TestCase):
    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_uses_head_bucket_without_mutating_storage(self, client_factory):
        client = mock.Mock()
        client_factory.return_value = client

        check_s3_bucket_readable(
            endpoint="https://s3.example.com",
            region="us-east-1",
            bucket="backup-bucket",
            access_key_id="AK",
            secret_access_key="SK",
        )

        client.head_bucket.assert_called_once_with(Bucket="backup-bucket")
        client.list_buckets.assert_not_called()
        client.create_bucket.assert_not_called()
        client.put_object.assert_not_called()
        client.delete_object.assert_not_called()


class S3OwnershipMarkerAtomicCreateTests(TestCase):
    def _put(self, **overrides):
        args = {
            "platform": Repository.S3Platform.AWS,
            "endpoint": "https://s3.us-east-1.amazonaws.com",
            "region": "us-east-1",
            "bucket": "backup-bucket",
            "key": "hfl/.hyperfilelens/repository-owner-v1.json",
            "body": b"{}",
            "access_key_id": "AK",
            "secret_access_key": "SK",
        }
        args.update(overrides)
        return put_s3_object_if_absent(**args)

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_standard_s3_uses_if_none_match(self, client_factory):
        client = mock.Mock()
        client_factory.return_value = client

        self.assertTrue(self._put())

        client.put_object.assert_called_once_with(
            Bucket="backup-bucket",
            Key="hfl/.hyperfilelens/repository-owner-v1.json",
            Body=b"{}",
            ContentLength=2,
            ContentType="application/json",
            IfNoneMatch="*",
        )
        client.meta.events.register_first.assert_not_called()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_aliyun_uses_signed_forbid_overwrite_header(self, client_factory):
        client = mock.Mock()
        client_factory.return_value = client

        self.assertTrue(
            self._put(
                platform=Repository.S3Platform.ALIYUN,
                endpoint="https://oss-cn-beijing.aliyuncs.com",
                region="oss-cn-beijing",
            )
        )

        client.put_object.assert_called_once_with(
            Bucket="backup-bucket",
            Key="hfl/.hyperfilelens/repository-owner-v1.json",
            Body=b"{}",
            ContentLength=2,
            ContentType="application/json",
        )
        registration = client.meta.events.register_first.call_args
        self.assertEqual(registration.args[0], "before-sign.s3.PutObject")
        request = SimpleNamespace(headers={})
        registration.args[1](request=request)
        self.assertEqual(request.headers["x-oss-forbid-overwrite"], "true")

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_legacy_custom_aliyun_endpoint_uses_aliyun_semantics(self, client_factory):
        client = mock.Mock()
        client_factory.return_value = client

        self.assertTrue(
            self._put(
                platform=Repository.S3Platform.CUSTOM,
                endpoint="oss-cn-beijing.aliyuncs.com",
                region="oss-cn-beijing",
            )
        )

        self.assertNotIn("IfNoneMatch", client.put_object.call_args.kwargs)
        client.meta.events.register_first.assert_called_once()

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_legacy_custom_huawei_endpoint_uses_huawei_semantics(self, client_factory):
        client = mock.Mock()
        client_factory.return_value = client

        self.assertTrue(
            self._put(
                platform=Repository.S3Platform.CUSTOM,
                endpoint="obs.cn-north-4.myhuaweicloud.com",
                region="cn-north-4",
            )
        )

        self.assertNotIn("IfNoneMatch", client.put_object.call_args.kwargs)
        registration = client.meta.events.register_first.call_args
        request = SimpleNamespace(headers={})
        registration.args[1](request=request)
        self.assertEqual(request.headers["x-obs-forbid-overwrite"], "true")

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_aliyun_existing_marker_returns_false(self, client_factory):
        client = mock.Mock()
        client.put_object.side_effect = ClientError(
            {
                "Error": {
                    "Code": "FileAlreadyExists",
                    "Message": "The object already exists.",
                },
                "ResponseMetadata": {"HTTPStatusCode": 409},
            },
            "PutObject",
        )
        client_factory.return_value = client

        self.assertFalse(
            self._put(
                platform=Repository.S3Platform.ALIYUN,
                endpoint="https://oss-cn-beijing.aliyuncs.com",
            )
        )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_huawei_uses_signed_forbid_overwrite_header(self, client_factory):
        client = mock.Mock()
        client_factory.return_value = client

        self.assertTrue(
            self._put(
                platform=Repository.S3Platform.HUAWEICLOUD,
                endpoint="https://obs.cn-north-4.myhuaweicloud.com",
                region="cn-north-4",
            )
        )

        self.assertNotIn("IfNoneMatch", client.put_object.call_args.kwargs)
        registration = client.meta.events.register_first.call_args
        self.assertEqual(registration.args[0], "before-sign.s3.PutObject")
        request = SimpleNamespace(headers={})
        registration.args[1](request=request)
        self.assertEqual(request.headers["x-obs-forbid-overwrite"], "true")

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_huawei_existing_marker_returns_false(self, client_factory):
        client = mock.Mock()
        client.put_object.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ObjectAlreadyExists",
                    "Message": "The object already exists.",
                },
                "ResponseMetadata": {"HTTPStatusCode": 409},
            },
            "PutObject",
        )
        client_factory.return_value = client

        self.assertFalse(
            self._put(
                platform=Repository.S3Platform.HUAWEICLOUD,
                endpoint="https://obs.cn-north-4.myhuaweicloud.com",
            )
        )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_legacy_gateway_falls_back_to_plain_overwrite(self, client_factory):
        client = mock.Mock()
        client.head_object.side_effect = ClientError(
            {
                "Error": {"Code": "NoSuchKey", "Message": "missing"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadObject",
        )
        client.put_object.side_effect = [
            ClientError(
                {
                    "Error": {
                        "Code": "NotImplemented",
                        "Message": "conditional write unsupported",
                    },
                    "ResponseMetadata": {"HTTPStatusCode": 501},
                },
                "PutObject",
            ),
            None,
        ]
        client_factory.return_value = client

        self.assertTrue(self._put())

        self.assertEqual(client.put_object.call_count, 2)
        first_call = client.put_object.call_args_list[0]
        second_call = client.put_object.call_args_list[1]
        self.assertIn("IfNoneMatch", first_call.kwargs)
        self.assertNotIn("IfNoneMatch", second_call.kwargs)

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_legacy_gateway_does_not_overwrite_an_existing_marker(self, client_factory):
        client = mock.Mock()
        client.put_object.side_effect = ClientError(
            {
                "Error": {
                    "Code": "NotImplemented",
                    "Message": "conditional write unsupported",
                },
                "ResponseMetadata": {"HTTPStatusCode": 501},
            },
            "PutObject",
        )
        client.head_object.return_value = {"ContentLength": 10}
        client_factory.return_value = client

        self.assertFalse(self._put())
        client.put_object.assert_called_once()
