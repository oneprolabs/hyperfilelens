from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
    verify_s3_bucket_access,
)
from apps.storage.services.internal.s3_client import (
    S3ClientError,
    verify_s3_bucket_rw,
)


class VerifyS3BucketRwTests(TestCase):
    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_returns_probe_key_when_head_put_delete_succeed(self, client_factory):
        client = mock.Mock()
        client_factory.return_value = client

        result = verify_s3_bucket_rw(
            endpoint="https://s3.amazonaws.com",
            region="us-east-1",
            bucket="hfl-bucket",
            access_key_id="AK",
            secret_access_key="SK",
        )

        self.assertEqual(result["bucket"], "hfl-bucket")
        self.assertTrue(result["wrote"])
        self.assertTrue(result["deleted"])
        self.assertTrue(result["probe_key"].startswith(".hfl-verify/"))
        self.assertTrue(result["probe_key"].endswith(".tmp"))
        client.head_bucket.assert_called_once_with(Bucket="hfl-bucket")
        client.put_object.assert_called_once()
        self.assertEqual(client.delete_object.call_count, 1)

    def test_raises_when_bucket_is_blank(self):
        with self.assertRaises(S3ClientError):
            verify_s3_bucket_rw(
                endpoint="https://s3.amazonaws.com",
                region="us-east-1",
                bucket="   ",
                access_key_id="AK",
                secret_access_key="SK",
            )

    @mock.patch("apps.storage.services.internal.s3_client._client")
    def test_raises_on_head_bucket_failure(self, client_factory):
        from botocore.exceptions import ClientError

        client = mock.Mock()
        client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}},
            "HeadBucket",
        )
        client_factory.return_value = client

        with self.assertRaises(S3ClientError) as ctx:
            verify_s3_bucket_rw(
                endpoint="https://s3.amazonaws.com",
                region="us-east-1",
                bucket="hfl-bucket",
                access_key_id="AK",
                secret_access_key="SK",
            )
        self.assertIn("Unable to access bucket", str(ctx.exception))


class VerifyS3BucketAccessWrapperTests(TestCase):
    @mock.patch("apps.storage.services.internal.repository_initializer.verify_s3_bucket_rw")
    def test_wraps_success(self, rw):
        rw.return_value = {"bucket": "b", "probe_key": "k", "wrote": True, "deleted": True}
        result = verify_s3_bucket_access(
            endpoint="https://s3.amazonaws.com",
            region="us-east-1",
            bucket="b",
            access_key_id="AK",
            secret_access_key="SK",
        )
        self.assertEqual(result["bucket"], "b")
        self.assertTrue(result["wrote"])

    @mock.patch("apps.storage.services.internal.repository_initializer.verify_s3_bucket_rw")
    def test_translates_s3_error_to_repository_initialization_error(self, rw):
        rw.side_effect = S3ClientError("Unable to access bucket b: 403")
        with self.assertRaises(RepositoryInitializationError) as ctx:
            verify_s3_bucket_access(
                endpoint="https://s3.amazonaws.com",
                region="us-east-1",
                bucket="b",
                access_key_id="AK",
                secret_access_key="SK",
            )
        self.assertNotIn("SK", str(ctx.exception))


class VerifyAccessApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="verify-api@test.local",
            email="verify-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="verify-test-org", name="Verify Test Org"
        )
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)
        self.repo = Repository.objects.create(
            organization_id=self.org.id,
            name="verify-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="hfl-primary",
            config={
                "endpoint": "s3.amazonaws.com",
                "external_endpoint": "s3.amazonaws.com",
                "internal_endpoint": "s3.internal.example.com",
                "region": "us-east-1",
                "access_key_id": "AKIA_TEST",
                "secret_access_key": "super-secret",
                "s3_url_style": "virtual_hosted",
                "use_tls": True,
            },
        )

    def _headers(self, org: Organization | None = None):
        return {"HTTP_X_ORG_KEY": (org or self.org).key}

    @mock.patch("apps.storage.repositories.views.verify_s3_bucket_access")
    def test_verify_access_success(self, verify_s3_bucket_access):
        verify_s3_bucket_access.return_value = {
            "bucket": "hfl-primary",
            "probe_key": ".hfl-verify/abc.tmp",
            "wrote": True,
            "deleted": True,
        }
        response = self.client.post(
            f"/api/v1/storage/repositories/{self.repo.id}/verify-access/",
            {},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["ok"])
        self.assertEqual(response.data["bucket"], "hfl-primary")
        self.assertEqual(response.data["probe_key"], ".hfl-verify/abc.tmp")
        verify_s3_bucket_access.assert_called_once()
        kwargs = verify_s3_bucket_access.call_args.kwargs
        self.assertEqual(kwargs["bucket"], "hfl-primary")
        self.assertEqual(kwargs["access_key_id"], "AKIA_TEST")
        self.assertEqual(kwargs["secret_access_key"], "super-secret")
        self.assertEqual(kwargs["endpoint"], "s3.amazonaws.com")

    @mock.patch("apps.storage.repositories.views.verify_s3_bucket_access")
    def test_verify_access_failure_returns_stable_safe_error(self, verify_s3_bucket_access):
        verify_s3_bucket_access.side_effect = RepositoryInitializationError(
            "Unable to access bucket hfl-primary: 403"
        )
        response = self.client.post(
            f"/api/v1/storage/repositories/{self.repo.id}/verify-access/",
            {},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        problem = response.data["data"]
        self.assertEqual(problem["code"], "STORAGE.S3_VALIDATION_FAILED")
        self.assertEqual(
            problem["title"],
            "Object storage validation failed. Check the connection settings and IAM permissions, then try again.",
        )
        self.assertNotIn("diagnostic", problem["meta"])
        self.assertNotIn("hfl-primary", str(response.data))

    @mock.patch("apps.storage.repositories.views.verify_s3_bucket_access")
    def test_verify_access_merges_draft_overrides(self, verify_s3_bucket_access):
        verify_s3_bucket_access.return_value = {
            "bucket": "hfl-primary",
            "probe_key": ".hfl-verify/abc.tmp",
            "wrote": True,
            "deleted": True,
        }
        response = self.client.post(
            f"/api/v1/storage/repositories/{self.repo.id}/verify-access/",
            {
                "region": "ap-east-1",
                "s3_url_style": "path",
                "use_tls": False,
                "access_key_id": "AKIA_NEW",
                "secret_access_key": "new-secret",
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["ok"])
        kwargs = verify_s3_bucket_access.call_args.kwargs
        # Locked fields still come from the repository row.
        self.assertEqual(kwargs["bucket"], "hfl-primary")
        self.assertEqual(kwargs["endpoint"], "s3.amazonaws.com")
        # Overrides applied.
        self.assertEqual(kwargs["region"], "ap-east-1")
        self.assertEqual(kwargs["s3_url_style"], "path")
        self.assertFalse(kwargs["use_tls"])
        self.assertEqual(kwargs["access_key_id"], "AKIA_NEW")
        self.assertEqual(kwargs["secret_access_key"], "new-secret")

    @mock.patch("apps.storage.repositories.views.verify_s3_bucket_access")
    def test_verify_access_rejects_locked_field_overrides(self, verify_s3_bucket_access):
        response = self.client.post(
            f"/api/v1/storage/repositories/{self.repo.id}/verify-access/",
            {"endpoint": "https://attacker.example.com"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        verify_s3_bucket_access.assert_not_called()
