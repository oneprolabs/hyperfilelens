from unittest import mock
from botocore.exceptions import ClientError
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.storage.repositories.models import Repository, Credential
from apps.storage.services.interface import create_repository
from apps.storage.services.internal.s3_client import S3ClientError
from apps.storage.services.internal.s3_creation_preflight import (
    verify_empty_prefix,
    PROBE_NAME,
    PROBE_METADATA,
    PROBE_BODY,
)

MODULE = "apps.storage.services.internal.s3_creation_preflight"


def missing():
    return ClientError({"Error": {"Code": "404"}}, "HeadObject")


class PrefixProbeTests(SimpleTestCase):
    def setUp(self):
        self.client = mock.MagicMock()
        patcher = mock.patch(f"{MODULE}._client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.head_object.side_effect = missing()
        self.client.list_objects_v2.return_value = {}
        self.client.put_object.return_value = {}
        self.args = dict(
            prefix="hfl6I09163025/",
            endpoint="s3.example.com",
            region="us-east-1",
            bucket="backup",
            access_key_id="key",
            secret_access_key="secret",
            s3_url_style="path",
            use_tls=True,
        )

    def test_provider_hooks_use_real_botocore_event_signature(self):
        from botocore.hooks import EventAliaser, HierarchicalEmitter
        from botocore.awsrequest import AWSRequest

        for platform, header in [
            ("huaweicloud", "x-obs-forbid-overwrite"),
            ("aliyun", "x-oss-forbid-overwrite"),
        ]:
            with self.subTest(platform=platform):
                events = EventAliaser(HierarchicalEmitter())
                self.client.meta.events = events
                verify_empty_prefix(platform=platform, **self.args)
                request = AWSRequest(method="PUT", url="https://example.test/probe")
                events.emit("before-sign.s3.PutObject", request=request)
                self.assertEqual(request.headers[header], "true")

    def test_upload_delete_without_downloading(self):
        verify_empty_prefix(**self.args)
        self.client.get_object.assert_not_called()
        self.assertEqual(
            self.client.put_object.call_args.kwargs["Key"],
            self.args["prefix"] + PROBE_NAME,
        )
        self.assertEqual(self.client.put_object.call_args.kwargs["IfNoneMatch"], "*")
        self.client.delete_object.assert_called_once()

    def test_occupied_prefix_does_not_upload(self):
        self.client.list_objects_v2.return_value = {"Contents": [{"Key": "real-data"}]}
        with self.assertRaisesMessage(S3ClientError, "already contains data"):
            verify_empty_prefix(**self.args)
        self.client.put_object.assert_not_called()

    def test_recognized_residue_is_deleted_before_upload(self):
        self.client.head_object.side_effect = [
            dict(Metadata=PROBE_METADATA, ContentLength=len(PROBE_BODY)),
            missing(),
        ]
        verify_empty_prefix(**self.args)
        self.assertEqual(self.client.delete_object.call_count, 2)
        names = [call[0] for call in self.client.method_calls]
        self.assertLess(names.index("delete_object"), names.index("put_object"))

    def test_unknown_reserved_file_is_not_deleted_or_overwritten(self):
        self.client.head_object.side_effect = None
        self.client.head_object.return_value = {"Metadata": {}, "ContentLength": 10}
        with self.assertRaisesMessage(S3ClientError, "unrecognized metadata"):
            verify_empty_prefix(**self.args)
        self.client.put_object.assert_not_called()
        self.client.delete_object.assert_not_called()

    def test_versioned_residue_and_upload_delete_exact_versions(self):
        self.client.head_object.side_effect = [
            dict(
                Metadata=PROBE_METADATA, ContentLength=len(PROBE_BODY), VersionId="old"
            ),
            missing(),
        ]
        self.client.put_object.return_value = {"VersionId": "new"}
        verify_empty_prefix(**self.args)
        self.assertEqual(
            [c.kwargs["VersionId"] for c in self.client.delete_object.call_args_list],
            ["old", "new"],
        )

    def test_listing_failure_does_not_write(self):
        self.client.list_objects_v2.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}}, "ListObjectsV2"
        )
        with self.assertRaises(S3ClientError):
            verify_empty_prefix(**self.args)
        self.client.put_object.assert_not_called()
        self.client.delete_object.assert_not_called()

    def test_upload_failure_does_not_delete_an_unknown_object(self):
        self.client.put_object.side_effect = ClientError(
            {"Error": {"Code": "PreconditionFailed"}}, "PutObject"
        )
        with self.assertRaises(S3ClientError):
            verify_empty_prefix(**self.args)
        self.client.delete_object.assert_not_called()

    def test_data_appearing_after_probe_is_rejected(self):
        self.client.list_objects_v2.side_effect = [
            {},
            {},
            {"Contents": [{"Key": "concurrent-data"}]},
        ]
        with self.assertRaisesMessage(S3ClientError, "already contains data"):
            verify_empty_prefix(**self.args)

    def test_delete_failure_reports_residue_path(self):
        self.client.delete_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}}, "DeleteObject"
        )
        with self.assertRaisesMessage(S3ClientError, self.args["prefix"] + PROBE_NAME):
            verify_empty_prefix(**self.args)


class RepositoryPreflightTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="s3-preflight", name="S3 preflight")
        self.args = dict(
            organization_id=self.org.id,
            name="Repository",
            repo_type="s3",
            s3_bucket="backup",
            s3_platform="custom",
            s3_bucket_mode="existing",
            config=dict(
                endpoint="s3.example.com",
                prefix="hfl6I09163025/",
                access_key_id="key",
                secret_access_key="secret",
                s3_url_style="path",
            ),
        )
        self.absent = self.patch(f"{MODULE}.ensure_s3_bucket_absent")
        self.bucket = self.patch(f"{MODULE}.create_s3_bucket", return_value=True)
        self.rollback = self.patch(
            f"{MODULE}.delete_s3_bucket_if_empty", return_value={"status": "deleted"}
        )
        self.probe = self.patch(f"{MODULE}.verify_empty_prefix")
        self.patch(
            "apps.storage.services.internal.repository_initializer.resolve_s3_url_style",
            return_value="path",
        )
        self.patch("apps.storage.services.interface.enqueue_repository_create_task")

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def assert_no_records(self):
        self.assertEqual(Repository.objects.count(), 0)
        self.assertEqual(Credential.objects.count(), 0)

    def test_new_bucket_failure_creates_no_record(self):
        self.args["s3_bucket_mode"] = "new"
        self.bucket.side_effect = S3ClientError("denied")
        with self.assertRaises(ValidationError):
            create_repository(**self.args)
        self.assert_no_records()
        self.probe.assert_not_called()

    def test_probe_failure_creates_no_record(self):
        self.probe.side_effect = S3ClientError("denied")
        with self.assertRaises(ValidationError):
            create_repository(**self.args)
        self.assert_no_records()

    def test_both_bucket_modes_probe_before_creating_record(self):
        def probe(**kwargs):
            self.assert_no_records()

        self.probe.side_effect = probe
        repository = create_repository(**self.args)
        self.assertEqual(repository.status, "creating")
        self.bucket.assert_not_called()

    def test_parent_prefix_conflict_prevents_probe_and_record(self):
        Repository.objects.create(
            organization_id=self.org.id,
            name="Parent",
            repo_type="s3",
            s3_bucket="backup",
            s3_platform="custom",
            config=dict(endpoint="s3.example.com", prefix="hfl/"),
        )
        self.args["config"]["prefix"] = "hfl/child/"
        with self.assertRaises(ValidationError):
            create_repository(**self.args)
        self.probe.assert_not_called()
        self.assertEqual(Repository.objects.count(), 1)

    def test_request_always_runs_probe(self):
        self.probe.side_effect = S3ClientError("denied")
        with self.assertRaises(ValidationError):
            create_repository(**self.args)
        self.assert_no_records()

    def test_concurrent_preparation_is_rejected_before_writes(self):
        lock = self.patch(f"{MODULE}.repository_execution_lock")
        lock.return_value.__enter__.return_value = False
        with self.assertRaisesMessage(ValidationError, "Another repository creation"):
            create_repository(**self.args)
        self.assert_no_records()
        self.probe.assert_not_called()
        self.bucket.assert_not_called()

    def test_different_prefix_cannot_reuse_bucket_preparation(self):
        self.args["s3_bucket_mode"] = "new"
        self.probe.side_effect = S3ClientError("denied")
        with self.assertRaises(ValidationError):
            create_repository(**self.args)
        self.args["config"]["prefix"] = "different/"
        self.bucket.side_effect = S3ClientError("already exists")
        with self.assertRaises(ValidationError):
            create_repository(**self.args)
        self.assertEqual(self.bucket.call_count, 2)
        self.assert_no_records()

    def test_new_bucket_retains_origin_and_skips_duplicate_creation(self):
        self.args["s3_bucket_mode"] = "new"
        repository = create_repository(**self.args)
        self.bucket.assert_called_once()
        self.assertEqual(repository.s3_bucket_mode, "new")
        self.assertTrue(repository.config["s3_bucket_prepared"])
        self.rollback.assert_not_called()

    def test_new_bucket_validation_failure_rolls_back(self):
        self.args["s3_bucket_mode"] = "new"
        self.probe.side_effect = S3ClientError("probe denied")
        with self.assertRaises(ValidationError):
            create_repository(**self.args)
        self.rollback.assert_called_once()
        self.assert_no_records()

    def test_unexpected_probe_failure_also_rolls_back(self):
        self.args["s3_bucket_mode"] = "new"
        self.probe.side_effect = TypeError("bad hook")
        with self.assertRaises(TypeError):
            create_repository(**self.args)
        self.rollback.assert_called_once()
        self.assert_no_records()

    def test_failed_rollback_is_visible(self):
        self.args["s3_bucket_mode"] = "new"
        self.probe.side_effect = S3ClientError("probe denied")
        self.rollback.return_value = {"status": "failed", "reason": "access denied"}
        with self.assertRaisesMessage(ValidationError, "could not be rolled back"):
            create_repository(**self.args)
        self.assert_no_records()

    def test_existing_bucket_in_new_mode_stops_before_any_mutation(self):
        from apps.storage.services.internal.s3_creation_preflight import (
            S3BucketAlreadyExists,
        )

        self.args["s3_bucket_mode"] = "new"
        self.absent.side_effect = S3BucketAlreadyExists("Bucket already exists.")
        with self.assertRaises(ValidationError) as error:
            create_repository(**self.args)
        self.assertIn("s3_bucket", error.exception.detail)
        self.bucket.assert_not_called()
        self.probe.assert_not_called()
        self.rollback.assert_not_called()
        self.assert_no_records()


class BucketExistenceTests(SimpleTestCase):
    @mock.patch(f"{MODULE}._client")
    def test_only_missing_bucket_may_proceed(self, make_client):
        from apps.storage.services.internal.s3_creation_preflight import (
            ensure_s3_bucket_absent,
            S3BucketAlreadyExists,
        )

        client = make_client.return_value
        args = dict(
            bucket="example",
            endpoint="example.test",
            region="test",
            access_key_id="test",
            secret_access_key="test",
            s3_url_style="path",
            use_tls=True,
        )
        with self.assertRaises(S3BucketAlreadyExists):
            ensure_s3_bucket_absent(**args)
        client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404"}}, "HeadBucket"
        )
        ensure_s3_bucket_absent(**args)
        client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403"}}, "HeadBucket"
        )
        with self.assertRaises(S3ClientError):
            ensure_s3_bucket_absent(**args)
        client.create_bucket.assert_not_called()
