import copy

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from types import SimpleNamespace
from unittest import mock
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node, NodeTask
from apps.node.agent_paths import repository_mount_point
from apps.protection.models import BackupConfig
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
    RepositoryAlreadyExistsError,
)
from apps.storage.services.internal.nas_repository import (
    nas_proxy_repository_subdir,
    nas_repository_payload,
)
from apps.storage.services.internal.repository_access import repository_payload_for_node
from apps.storage.services.internal.repository_credential_rotation import (
    run_repository_credential_rotation_task,
)
from apps.storage.services.internal.repository_secrets import (
    build_repository_runtime_payload,
)
from apps.storage.repositories.models import (
    Credential,
    Repository,
    RepositoryExecutionTarget,
    RepositoryLocationClaim,
    RepositoryTask,
    RepositoryUsageShard,
)
from apps.storage.provider_catalog.catalog import load_default_catalog
from apps.storage.provider_catalog.models import StorageProviderOverride
from apps.storage.provider_catalog.schema import provider_checksum
from apps.storage.services.internal.repository_operations import (
    create_repository_operation_task,
    discover_repository_execution_targets,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    mark_repository_location_residual,
    reserve_repository_location,
)
from apps.storage.services.interface import update_repository
from apps.task.models import Task


class StorageRepositoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="storage-api@test.local",
            email="storage-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="storage-test-org", name="Storage Test Org"
        )
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        for provider in (
            {
                "id": "aws",
                "display_name": "Amazon S3 Test Provider",
                "enabled": True,
                "regions": [
                    {
                        "id": "us-east-1",
                        "display_name": "US East Test",
                        "region_group": "north_america",
                        "region_group_en": "North America",
                        "external_endpoint": "s3.amazonaws.com",
                        "internal_endpoint": "s3.amazonaws.com",
                        "driver": "s3",
                        "s3_url_style": "virtual_hosted",
                        "use_tls": True,
                    }
                ],
            },
            {
                "id": "aliyun",
                "display_name": "Alibaba Cloud Test Provider",
                "enabled": True,
                "regions": [
                    {
                        "id": "cn-hangzhou",
                        "display_name": "Hangzhou Test",
                        "region_group": "asia_pacific",
                        "region_group_en": "Asia Pacific",
                        "external_endpoint": "oss-cn-hangzhou.aliyuncs.com",
                        "internal_endpoint": "oss-cn-hangzhou-internal.aliyuncs.com",
                        "driver": "s3",
                        "s3_url_style": "virtual_hosted",
                        "use_tls": True,
                    }
                ],
            },
        ):
            StorageProviderOverride.objects.create(
                provider_id=provider["id"],
                schema_version=1,
                config=provider,
                checksum=provider_checksum(provider),
                updated_by_id=self.user.pk,
            )
        self.client.force_authenticate(user=self.user)

    def _headers(self, org: Organization | None = None):
        return {"HTTP_X_ORG_KEY": (org or self.org).key}

    def _s3_payload(self):
        return {
            "name": "primary-s3",
            "repo_type": "s3",
            "s3_platform": "aws",
            "s3_bucket": "hfl-primary",
            "s3_bucket_mode": "existing",
            "config": {
                "region": "us-east-1",
                "endpoint": "https://s3.amazonaws.com",
                "prefix": "kopia",
                "access_key_id": "AKIA_TEST",
                "secret_access_key": "super-secret",
                "s3_url_style": "virtual_hosted",
                "use_tls": True,
            },
        }

    def _post_repository(self, payload):
        with mock.patch("apps.storage.tasks.execute_repository_operation.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                return self.client.post(
                    "/api/v1/storage/repositories/",
                    payload,
                    format="json",
                    **self._headers(),
                )

    def _run_create_task(
        self,
        repository,
        *,
        operation_type=RepositoryTask.OperationType.CREATE_REPOSITORY,
    ):
        from apps.storage.services.internal.repository_create import (
            run_repository_create_task,
        )

        repository_task = RepositoryTask.objects.get(
            repository=repository,
            operation_type=operation_type,
        )
        return run_repository_create_task(repository_task_id=repository_task.id)

    def test_create_s3_repository_requires_bucket_mode(self):
        payload = self._s3_payload()
        payload.pop("s3_bucket_mode")

        response = self.client.post(
            "/api/v1/storage/repositories/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "s3_bucket_mode",
            {item["field"] for item in response.data["data"]["errors"]},
        )

    def test_new_managed_bucket_rejects_invalid_name_with_stable_field_error(self):
        payload = self._s3_payload()
        payload["s3_bucket_mode"] = "new"
        payload["s3_bucket"] = "Invalid_Bucket"

        response = self.client.post(
            "/api/v1/storage/repositories/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        problem = response.data["data"]
        self.assertEqual(problem["code"], "STORAGE.S3_BUCKET_NAME_INVALID")
        self.assertEqual(problem["errors"][0]["field"], "s3_bucket")

    def test_empty_new_bucket_keeps_the_required_field_error(self):
        payload = self._s3_payload()
        payload["s3_bucket_mode"] = "new"
        payload["s3_bucket"] = ""

        response = self.client.post(
            "/api/v1/storage/repositories/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        problem = response.data["data"]
        self.assertEqual(problem["code"], "VALIDATION.FAILED")
        self.assertIn(
            "s3_bucket",
            {item["field"] for item in problem["errors"]},
        )

    def test_existing_bucket_does_not_apply_new_bucket_name_rules(self):
        payload = self._s3_payload()
        payload["s3_bucket_mode"] = "existing"
        payload["s3_bucket"] = "Legacy_Bucket"

        response = self._post_repository(payload)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)

    def test_create_s3_repository_allows_bucket_root(self):
        payload = self._s3_payload()
        payload["config"]["prefix"] = "   "

        response = self.client.post(
            "/api/v1/storage/repositories/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        repository = Repository.objects.get(id=response.data["id"])
        self.assertEqual(repository.config["prefix"], "")
        self.assertEqual(repository.location_claims.get().root_path, "/")

    def test_create_s3_repository_rejects_overlapping_prefix(self):
        first = self._post_repository(self._s3_payload())
        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED, first.content)

        nested_payload = self._s3_payload()
        nested_payload["name"] = "nested-s3"
        nested_payload["config"]["prefix"] = "kopia/child"
        nested = self._post_repository(nested_payload)

        self.assertEqual(
            nested.status_code, status.HTTP_400_BAD_REQUEST, nested.content
        )
        self.assertIn("overlaps repository", str(nested.data))
        self.assertFalse(Repository.objects.filter(name="nested-s3").exists())

    def test_create_managed_s3_repository_rejects_unknown_catalog_region(self):
        payload = self._s3_payload()
        payload["config"]["region"] = "unknown-region"

        response = self.client.post(
            "/api/v1/storage/repositories/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            {
                "field": "config.region",
                "code": "VALIDATION.FIELD_INVALID",
                "message": "Region is not in the current Provider Catalog.",
            },
            response.data["data"]["errors"],
        )

    def test_create_managed_s3_repository_rejects_catalog_endpoint_mismatch(self):
        payload = self._s3_payload()
        payload["config"]["endpoint"] = "https://attacker.example.com"

        response = self.client.post(
            "/api/v1/storage/repositories/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            {
                "field": "config.endpoint",
                "code": "VALIDATION.FIELD_INVALID",
                "message": "Endpoint does not match the current Provider Catalog region.",
            },
            response.data["data"]["errors"],
        )

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_custom_s3_repository_is_not_catalog_managed(
        self, _initialize, _enqueue
    ):
        payload = self._s3_payload()
        payload["s3_platform"] = Repository.S3Platform.CUSTOM
        payload["config"]["region"] = "private-region"
        payload["config"]["endpoint"] = "https://s3.internal.example.com"

        response = self._post_repository(payload)

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["status"], Repository.Status.CREATING)
        self.assertEqual(response.data["s3_platform"], Repository.S3Platform.CUSTOM)
        self.assertEqual(response.data["config"]["endpoint"], "s3.internal.example.com")
        self.assertNotIn("endpoint_type", response.data["config"])
        self.assertNotIn("external_endpoint", response.data["config"])
        self.assertNotIn("internal_endpoint", response.data["config"])
        result = self._run_create_task(Repository.objects.get(name="primary-s3"))
        self.assertEqual(result["status"], "success")

    def test_create_s3_repository_rejects_derived_endpoint_fields(self):
        payload = self._s3_payload()
        payload["config"].update(
            {
                "endpoint_type": "external",
                "external_endpoint": "s3.amazonaws.com",
                "internal_endpoint": "s3.amazonaws.com",
            }
        )

        response = self.client.post(
            "/api/v1/storage/repositories/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "config",
            {item["field"] for item in response.data["data"]["errors"]},
        )

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_s3_repository_with_dynamic_catalog_provider(
        self, _initialize, _enqueue
    ):
        provider = copy.deepcopy(load_default_catalog()["providers"][0])
        provider.update({"id": "tencent", "display_name": "Tencent Cloud COS"})
        region = provider["regions"][0]
        region.update(
            {
                "id": "ap-shanghai",
                "external_endpoint": "cos.ap-shanghai.myqcloud.com",
                "internal_endpoint": "cos-internal.ap-shanghai.myqcloud.com",
            }
        )
        provider["regions"] = [region]
        StorageProviderOverride.objects.create(
            provider_id=provider["id"],
            schema_version=1,
            config=provider,
            checksum=provider_checksum(provider),
            updated_by_id=self.user.pk,
        )
        payload = self._s3_payload()
        payload.update({"s3_platform": "tencent", "s3_bucket": "dynamic-provider"})
        payload["config"].update(
            {
                "region": "ap-shanghai",
                "endpoint": "cos.ap-shanghai.myqcloud.com",
            }
        )

        response = self._post_repository(payload)

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["status"], Repository.Status.CREATING)
        self.assertEqual(response.data["s3_platform"], "tencent")
        self.assertNotIn("endpoint_type", response.data["config"])
        self.assertEqual(
            response.data["config"]["endpoint"],
            "cos.ap-shanghai.myqcloud.com",
        )
        self.assertEqual(
            response.data["config"]["external_endpoint"],
            "cos.ap-shanghai.myqcloud.com",
        )
        result = self._run_create_task(Repository.objects.get(name="primary-s3"))
        self.assertEqual(result["status"], "success")

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_s3_repository_persists_encrypted_credential(
        self, initialize, enqueue_usage
    ):
        create = self._post_repository(self._s3_payload())
        self.assertEqual(create.status_code, status.HTTP_202_ACCEPTED, create.content)
        self.assertEqual(create.data["status"], Repository.Status.CREATING)
        self.assertEqual(create.data["initialization_state"], "initializing")
        repo_id = create.data["id"]
        self.assertEqual(create.data["repo_type"], Repository.Type.S3)
        self.assertNotIn("credential_payload", create.data)
        self.assertNotIn("secret_access_key", create.data["config"])
        self.assertNotIn("kopia_password", create.data["config"])
        self.assertTrue(create.data["credential_id"])
        self.assertTrue(create.data["credential_hint"]["configured"])

        repo = Repository.objects.get(id=repo_id)
        self.assertEqual(repo.organization_id, self.org.id)
        self.assertEqual(repo.s3_bucket, "hfl-primary")
        self.assertIsNotNone(repo.credential_id)
        self.assertNotIn("secret_access_key", repo.config)
        self.assertNotIn("kopia_password", repo.config)
        credential = Credential.objects.get(id=repo.credential_id)
        secret_payload = credential.get_secret_payload()
        self.assertEqual(secret_payload["secret_access_key"], "super-secret")
        self.assertTrue(secret_payload["kopia_password"])

        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "success")
        repo.refresh_from_db()
        self.assertEqual(repo.status, Repository.Status.CREATED)
        self.assertEqual(repo.health, Repository.Health.ONLINE)
        claim = RepositoryLocationClaim.objects.get(repository=repo)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        initialize.assert_called_once()
        enqueue_usage.assert_called_once()

        listing = self.client.get(
            "/api/v1/storage/repositories/",
            {"repo_type": "s3"},
            **self._headers(),
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        rows = listing.data["data"]["list"]
        self.assertIn(repo_id, [row["id"] for row in rows])

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_s3_applies_region_connection_settings(self, _initialize, _enqueue):
        expected = {
            Repository.S3Platform.AWS: (
                "virtual_hosted",
                "us-east-1",
                "s3.amazonaws.com",
            ),
            Repository.S3Platform.ALIYUN: (
                "virtual_hosted",
                "cn-hangzhou",
                "oss-cn-hangzhou.aliyuncs.com",
            ),
            Repository.S3Platform.HUAWEICLOUD: (
                "virtual_hosted",
                "cn-north-1",
                "obs.cn-north-1.myhuaweicloud.com",
            ),
            Repository.S3Platform.CUSTOM: (
                "auto",
                "private-region",
                "https://s3.internal.example.com",
            ),
        }
        for index, (platform, (url_style, region, endpoint)) in enumerate(
            expected.items(), start=1
        ):
            payload = self._s3_payload()
            payload["name"] = f"provider-{platform}"
            payload["s3_platform"] = platform
            payload["s3_bucket"] = f"provider-bucket-{index}"
            payload["config"]["region"] = region
            payload["config"]["endpoint"] = endpoint
            payload["config"].pop("s3_url_style")

            response = self._post_repository(payload)

            self.assertEqual(
                response.status_code, status.HTTP_202_ACCEPTED, response.content
            )
            self.assertEqual(response.data["config"]["s3_url_style"], url_style)
            repo = Repository.objects.get(name=f"provider-{platform}")
            result = self._run_create_task(repo)
            self.assertEqual(result["status"], "success")

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_repository_creation_always_snapshots_and_uses_external_endpoint(
        self, initialize, _enqueue
    ):
        payload = self._s3_payload()
        payload["s3_platform"] = Repository.S3Platform.ALIYUN
        payload["config"].update(
            {
                "region": "cn-hangzhou",
                "endpoint": "oss-cn-hangzhou.aliyuncs.com",
            }
        )

        response = self._post_repository(payload)

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        config = response.data["config"]
        self.assertNotIn("endpoint_type", config)
        self.assertEqual(config["endpoint"], "oss-cn-hangzhou.aliyuncs.com")
        self.assertEqual(config["external_endpoint"], "oss-cn-hangzhou.aliyuncs.com")
        self.assertEqual(
            config["internal_endpoint"],
            "oss-cn-hangzhou-internal.aliyuncs.com",
        )
        repository = Repository.objects.get(pk=response.data["id"])
        result = self._run_create_task(repository)
        self.assertEqual(result["status"], "success")
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATED)
        runtime = build_repository_runtime_payload(repository=repository)
        self.assertEqual(runtime["endpoint"], config["endpoint"])
        self.assertIsNotNone(initialize.call_args)
        initialized_repository = initialize.call_args.args[0]
        self.assertEqual(
            initialized_repository.config["endpoint"],
            "oss-cn-hangzhou.aliyuncs.com",
        )

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_equal_catalog_endpoints_force_external_selection(
        self, _initialize, _enqueue
    ):
        payload = self._s3_payload()

        response = self._post_repository(payload)

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        config = response.data["config"]
        self.assertEqual(config["endpoint"], "s3.amazonaws.com")
        self.assertNotIn("endpoint_type", config)
        self.assertNotIn("external_endpoint", config)
        self.assertNotIn("internal_endpoint", config)
        result = self._run_create_task(Repository.objects.get(name="primary-s3"))
        self.assertEqual(result["status"], "success")

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_s3_generates_kopia_password_in_credential(
        self, initialize, _enqueue
    ):
        response = self._post_repository(self._s3_payload())
        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        repo = Repository.objects.get(name="primary-s3")
        self.assertNotIn("kopia_password", repo.config)
        credential = Credential.objects.get(id=repo.credential_id)
        self.assertTrue(credential.get_secret_payload()["kopia_password"])
        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "success")
        initialize.assert_called_once()

    @mock.patch("apps.storage.services.interface.enqueue_repository_usage_refresh")
    def test_create_nas_without_proxy_is_unverified(self, enqueue_usage):
        response = self.client.post(
            "/api/v1/storage/repositories/",
            {
                "name": "direct-nas",
                "repo_type": "nas",
                "nas_protocol": "nfs",
                "config": {
                    "server_address": "10.0.0.15",
                    "share_path": "/volume1/backup",
                },
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(response.data["status"], Repository.Status.CREATED)
        self.assertEqual(response.data["health"], Repository.Health.UNVERIFIED)
        self.assertEqual(response.data["initialization_state"], "not_initialized")
        self.assertEqual(response.data["initialized_target_count"], 0)
        repo = Repository.objects.get(name="direct-nas")
        self.assertNotIn("kopia_password", repo.config)
        self.assertTrue(
            Credential.objects.get(id=repo.credential_id).get_secret_payload()[
                "kopia_password"
            ]
        )
        enqueue_usage.assert_called_once()

    def test_owned_location_is_ready_only_after_ownership_verification(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="legacy-s3-awaiting-verification",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="legacy-bucket",
            config={
                "endpoint": "s3.example.test",
                "prefix": "hfl/",
                "access_key_id": "legacy-account",
            },
        )
        reserve_repository_location(repository)
        mark_repository_location_owned(repository)

        unverified = self.client.get(
            f"/api/v1/storage/repositories/{repository.id}/",
            **self._headers(),
        )

        self.assertEqual(unverified.status_code, status.HTTP_200_OK)
        self.assertEqual(unverified.data["initialization_state"], "unverified")
        self.assertEqual(unverified.data["initialized_target_count"], 0)

        mark_repository_location_ownership_verified(repository)
        verified = self.client.get(
            f"/api/v1/storage/repositories/{repository.id}/",
            **self._headers(),
        )

        self.assertEqual(verified.status_code, status.HTTP_200_OK)
        self.assertEqual(verified.data["initialization_state"], "ready")
        self.assertEqual(verified.data["initialized_target_count"], 1)

    def test_removed_repository_with_retained_location_requires_attention(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="force-cleaned-with-residue",
            repo_type=Repository.Type.S3,
            status=Repository.Status.REMOVED,
            health=Repository.Health.OFFLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="retained-bucket",
            config={
                "endpoint": "s3.example.test",
                "prefix": "hfl/",
                "access_key_id": "retained-account",
            },
        )
        reserve_repository_location(repository)
        mark_repository_location_residual(repository)

        response = self.client.get(
            f"/api/v1/storage/repositories/{repository.id}/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["initialization_state"], "attention_required")

        listing = self.client.get(
            "/api/v1/storage/repositories/",
            {"repo_type": "s3"},
            **self._headers(),
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK, listing.content)
        self.assertIn(
            repository.id,
            [row["id"] for row in listing.data["data"]["list"]],
        )

        invalid_release = self.client.post(
            f"/api/v1/storage/repositories/{repository.id}/release-residual-location/",
            {"confirmation": "RELEASE"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(invalid_release.status_code, status.HTTP_400_BAD_REQUEST)

        released = self.client.post(
            f"/api/v1/storage/repositories/{repository.id}/release-residual-location/",
            {"confirmation": "RELEASE LOCATION"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(released.status_code, status.HTTP_200_OK, released.content)
        self.assertEqual(released.data["initialization_state"], "released")
        claim = RepositoryLocationClaim.objects.get(repository=repository)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RELEASED)

        listing = self.client.get(
            "/api/v1/storage/repositories/",
            {"repo_type": "s3"},
            **self._headers(),
        )
        self.assertNotIn(
            repository.id,
            [row["id"] for row in listing.data["data"]["list"]],
        )

    def test_residual_location_release_rejects_active_repository_operation(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="removed-with-active-operation",
            repo_type=Repository.Type.S3,
            status=Repository.Status.REMOVED,
            health=Repository.Health.OFFLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="retained-bucket",
            config={
                "endpoint": "s3.example.test",
                "prefix": "hfl/",
                "access_key_id": "retained-account",
            },
        )
        claim = reserve_repository_location(repository)
        mark_repository_location_residual(repository)
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name="Active repository operation",
            status=Task.Status.PENDING,
        )
        RepositoryTask.objects.create(
            task=task,
            repository=repository,
            operation_type=RepositoryTask.OperationType.CLEANUP_REPOSITORY,
            owner_type=RepositoryExecutionTarget.OwnerType.CONTROLLER,
            owner_identity="controller",
        )

        response = self.client.post(
            f"/api/v1/storage/repositories/{repository.id}/release-residual-location/",
            {"confirmation": "RELEASE LOCATION"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)

    def test_residual_location_release_rejects_active_agent_operation(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="removed-with-active-agent-operation",
            repo_type=Repository.Type.S3,
            status=Repository.Status.REMOVED,
            health=Repository.Health.OFFLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="retained-agent-bucket",
            config={
                "endpoint": "s3.example.test",
                "prefix": "agent-hfl/",
                "access_key_id": "retained-agent-account",
            },
        )
        claim = reserve_repository_location(repository)
        mark_repository_location_residual(repository)
        node = Node.objects.create(
            organization=self.org,
            name="active-initialization-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=node,
            kind="repo.initialize",
            payload={"repository": {"id": repository.id}},
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )

        response = self.client.post(
            f"/api/v1/storage/repositories/{repository.id}/release-residual-location/",
            {"confirmation": "RELEASE LOCATION"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)

    def test_residual_location_release_rejects_persisted_agent_operation(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="removed-with-persisted-agent-operation",
            repo_type=Repository.Type.S3,
            status=Repository.Status.REMOVED,
            health=Repository.Health.OFFLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="retained-persisted-agent-bucket",
            config={
                "endpoint": "s3.example.test",
                "prefix": "persisted-agent-hfl/",
                "access_key_id": "persisted-agent-account",
            },
        )
        claim = reserve_repository_location(repository)
        mark_repository_location_residual(repository)
        node = Node.objects.create(
            organization=self.org,
            name="persisted-active-operation-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=node,
            kind="repository.operation",
            payload={"repository_id": repository.id},
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )

        response = self.client.post(
            f"/api/v1/storage/repositories/{repository.id}/release-residual-location/",
            {"confirmation": "RELEASE LOCATION"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)

    @mock.patch(
        "apps.storage.services.interface.enqueue_repository_create_task",
        side_effect=ValidationError("queue acceptance failed"),
    )
    def test_create_acceptance_rolls_back_repository_claim_and_credential(
        self,
        _enqueue,
    ):
        response = self.client.post(
            "/api/v1/storage/repositories/",
            self._s3_payload(),
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Repository.objects.filter(name="primary-s3").exists())
        self.assertFalse(RepositoryLocationClaim.objects.exists())
        self.assertFalse(Credential.objects.exists())

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_failed_repository_initialization_retries_on_same_identity(
        self,
        initialize,
        _enqueue_usage,
    ):
        initialize.side_effect = [
            RepositoryInitializationError("temporary endpoint failure"),
            None,
        ]
        create = self._post_repository(self._s3_payload())
        self.assertEqual(create.status_code, status.HTTP_202_ACCEPTED, create.content)
        repository = Repository.objects.get(pk=create.data["id"])
        first_task = RepositoryTask.objects.get(repository=repository)
        first_result = self._run_create_task(repository)
        self.assertEqual(first_result["status"], "failed")
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATE_FAILED)
        self.assertEqual(
            repository.location_claims.get().state,
            RepositoryLocationClaim.State.RESIDUAL,
        )

        with mock.patch("apps.storage.tasks.execute_repository_operation.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                retry = self.client.post(
                    f"/api/v1/storage/repositories/{repository.id}/retry-initialization/",
                    {},
                    format="json",
                    **self._headers(),
                )
        self.assertEqual(retry.status_code, status.HTTP_202_ACCEPTED, retry.content)
        self.assertEqual(retry.data["repository"]["id"], repository.id)
        second_task = (
            RepositoryTask.objects.filter(repository=repository)
            .exclude(pk=first_task.id)
            .get()
        )
        duplicate_retry = self.client.post(
            f"/api/v1/storage/repositories/{repository.id}/retry-initialization/",
            {},
            format="json",
            **self._headers(),
        )
        self.assertEqual(
            duplicate_retry.status_code,
            status.HTTP_202_ACCEPTED,
            duplicate_retry.content,
        )
        self.assertEqual(
            duplicate_retry.data["task"]["task_uuid"],
            str(second_task.task.task_uuid),
        )
        self.assertEqual(
            RepositoryTask.objects.filter(repository=repository).count(),
            2,
        )
        from apps.storage.services.internal.repository_create import (
            run_repository_create_task,
        )

        second_result = run_repository_create_task(repository_task_id=second_task.id)
        self.assertEqual(second_result["status"], "success")
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(
            repository.location_claims.get().state,
            RepositoryLocationClaim.State.OWNED,
        )

    def test_create_smb_nas_rejects_read_only_mount_option(self):
        response = self.client.post(
            "/api/v1/storage/repositories/",
            {
                "name": "read-only-smb",
                "repo_type": "nas",
                "nas_protocol": "smb",
                "config": {
                    "server_address": "10.0.0.15",
                    "share_path": "/backup",
                    "mount_options": "ro,iocharset=utf8",
                    "smb_username": "backup-user",
                    "smb_password": "backup-password",
                },
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            {
                "field": "config.mount_options",
                "code": "VALIDATION.FIELD_INVALID",
                "message": "SMB backup repositories cannot use the read-only (ro) mount option.",
            },
            response.data["data"]["errors"],
        )

    def test_s3_repository_cannot_change_region(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="immutable-s3-location",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="immutable-bucket",
            config={
                "region": "us-east-1",
                "endpoint": "s3.amazonaws.com",
                "prefix": "hfl/repository",
            },
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repository.id}/",
            {"config": {"region": "us-east-1"}},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        repository.refresh_from_db()
        self.assertEqual(repository.config["region"], "us-east-1")

    def test_s3_access_key_change_is_staged_without_changing_live_identity(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="immutable-s3-account",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="immutable-bucket",
            config={
                "region": "us-east-1",
                "endpoint": "s3.amazonaws.com",
                "prefix": "hfl/repository/",
                "access_key_id": "AKIA_ORIGINAL",
                "s3_url_style": "virtual_hosted",
            },
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repository.id}/",
            {
                "credential_payload": {
                    "access_key_id": "AKIA_DIFFERENT_ACCOUNT",
                    "secret_access_key": "replacement-secret",
                }
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        repository.refresh_from_db()
        self.assertEqual(repository.config["access_key_id"], "AKIA_ORIGINAL")
        self.assertTrue(
            RepositoryTask.objects.filter(
                repository=repository,
                operation_type=RepositoryTask.OperationType.CREDENTIAL_ROTATE,
                task__status=Task.Status.PENDING,
            ).exists()
        )

    def test_repository_binding_can_only_change_through_repair_workflow(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="immutable-proxy-binding",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=11,
            config={
                "proxy_node_base_dir": "/data/repositories",
                "proxy_node_dir": "/data/repositories/hfl-repo-1",
                "proxy_fs_layout": "managed_subdir_v1",
            },
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repository.id}/",
            {"bind_node_id": 12},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        repository.refresh_from_db()
        self.assertEqual(repository.bind_node_id, 11)

    def test_repository_cannot_be_updated_during_or_after_removal(self):
        for index, repository_status in enumerate(
            (Repository.Status.REMOVING, Repository.Status.REMOVED),
            start=1,
        ):
            credential = Credential.objects.create(
                organization_id=self.org.id,
                credential_type=Credential.Type.S3,
            )
            credential.set_secret_payload(
                {
                    "secret_access_key": "test-secret",
                    "kopia_password": "test-kopia-password",
                }
            )
            credential.save(update_fields=["secret_cipher", "updated_at"])
            repository = Repository.objects.create(
                organization_id=self.org.id,
                name=f"immutable-removal-{index}",
                repo_type=Repository.Type.S3,
                status=repository_status,
                health=Repository.Health.OFFLINE,
                credential_id=credential.id,
                s3_platform=Repository.S3Platform.CUSTOM,
                s3_bucket=f"immutable-removal-bucket-{index}",
                config={
                    "endpoint": "s3.example.test",
                    "prefix": f"immutable/removal/{index}/",
                    "access_key_id": f"removal-account-{index}",
                    "s3_url_style": "virtual_hosted",
                },
            )
            if repository_status == Repository.Status.REMOVED:
                reserve_repository_location(repository)
                mark_repository_location_residual(repository)

            response = self.client.patch(
                f"/api/v1/storage/repositories/{repository.id}/",
                {"name": "must-not-change"},
                format="json",
                **self._headers(),
            )

            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("during or after removal", str(response.data))
            repository.refresh_from_db()
            self.assertEqual(repository.name, f"immutable-removal-{index}")

    def test_repository_update_rechecks_status_after_lock(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="stale-update",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            nas_protocol=Repository.NasProtocol.NFS,
            config={
                "server_address": "nas.example.test",
                "share_path": "/backup",
            },
        )
        stale_repository = Repository.objects.get(pk=repository.id)
        Repository.objects.filter(pk=repository.id).update(
            status=Repository.Status.REMOVING
        )

        with self.assertRaises(DRFValidationError):
            update_repository(
                repository=stale_repository,
                name="must-not-change",
            )

        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.REMOVING)
        self.assertEqual(repository.name, "stale-update")

    @mock.patch("apps.storage.services.interface.enqueue_repository_usage_refresh")
    def test_s3_quota_update_does_not_revalidate_saved_region_against_catalog(
        self, enqueue_usage
    ):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="legacy-managed-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.HUAWEICLOUD,
            s3_bucket="legacy-bucket",
            config={
                "region": "legacy-region-no-longer-in-catalog",
                "endpoint": "obs.legacy.example.com",
                "prefix": "hfl/repository",
                "s3_url_style": "virtual_hosted",
                "use_tls": True,
                "quota_gb": 5,
            },
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repository.id}/",
            {
                "name": "legacy-managed-s3-renamed",
                "config": {
                    "quota_gb": 10,
                    "quota_unit": "TB",
                    "quota_alert_enabled": True,
                    "quota_alert_threshold": 80,
                },
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        repository.refresh_from_db()
        self.assertEqual(repository.name, "legacy-managed-s3-renamed")
        self.assertEqual(repository.config["quota_gb"], 10)
        self.assertEqual(repository.config["quota_unit"], "TB")
        self.assertEqual(
            repository.config["region"], "legacy-region-no-longer-in-catalog"
        )
        self.assertEqual(repository.config["endpoint"], "obs.legacy.example.com")
        enqueue_usage.assert_called_once()

    def test_repository_update_rejects_invalid_quota_unit(self):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="quota-unit-validation",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"proxy_node_dir": "/data/repository", "quota_gb": 10},
        )

        response = self.client.patch(
            f"/api/v1/storage/repositories/{repository.id}/",
            {"config": {"quota_gb": 10, "quota_unit": "MB"}},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("quota_unit", str(response.data))
        repository.refresh_from_db()
        self.assertNotIn("quota_unit", repository.config)

    def test_associated_sources_lists_direct_nas_agent_health(self):
        agent = Node.objects.create(
            organization=self.org,
            name="source-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.8.10",
            metadata={"inventory": {"hostname": "source-host", "os": "linux"}},
        )
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "192.168.8.82", "share_path": "/nfsshare"},
        )
        config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="agent backup",
            source_type="agent",
            source_ref_id=agent.id,
            repository_id=repo.id,
        )
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repo.id,
            usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
            node_id=agent.id,
            repository_subdir=f"hp-repos/agent-{agent.id}",
            mount_point=f"/mnt/hfl/storage-repositories/repo-{repo.id}-node-{agent.id}",
            source_config_count=1,
            source_config_ids=[config.id],
            status=RepositoryUsageShard.Status.SUCCESS,
            is_active=True,
        )

        response = self.client.get(
            f"/api/v1/storage/repositories/{repo.id}/associated-sources/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["backup_config_id"], config.id)
        self.assertEqual(row["source_name"], "source-agent")
        self.assertEqual(row["source_kind"], "host")
        self.assertEqual(row["node_ip"], "10.0.8.10")
        self.assertEqual(row["availability"], Node.Availability.ONLINE)
        self.assertEqual(row["registered_at"], agent.created_at.isoformat())
        self.assertEqual(row["nas_location"], "nfs://192.168.8.82/nfsshare")
        self.assertEqual(row["repository_subdir"], f"hp-repos/agent-{agent.id}")
        self.assertEqual(
            row["repository_mount_point"],
            f"/mnt/hfl/storage-repositories/repo-{repo.id}-node-{agent.id}",
        )
        self.assertEqual(row["health"], Repository.Health.ONLINE)

    def test_associated_sources_lists_direct_nas_source_health(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="nas-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.8.12",
        )
        source = SourceResource.objects.create(
            organization=self.org,
            name="source-nas",
            resource_type=ResourceType.NAS,
            bound_node=proxy,
            config={"protocol": "nfs", "server": "192.168.10.35", "share": "/"},
        )
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="direct-nas-source",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={"server_address": "192.168.10.35", "share_path": "/"},
        )
        config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="nas backup",
            source_type="nas",
            source_ref_id=source.id,
            repository_id=repo.id,
        )
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=repo.id,
            usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
            node_id=proxy.id,
            repository_subdir=f"hp-repos/agent-{proxy.id}",
            mount_point=f"/mnt/hfl/storage-repositories/repo-{repo.id}-node-{proxy.id}",
            source_config_count=1,
            source_config_ids=[config.id],
            status=RepositoryUsageShard.Status.SUCCESS,
            is_active=True,
        )

        response = self.client.get(
            f"/api/v1/storage/repositories/{repo.id}/associated-sources/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        row = response.data["results"][0]
        self.assertEqual(row["repository_subdir"], f"hp-repos/agent-{proxy.id}")
        self.assertEqual(
            row["repository_mount_point"],
            f"/mnt/hfl/storage-repositories/repo-{repo.id}-node-{proxy.id}",
        )
        self.assertEqual(row["availability"], source.availability)
        self.assertEqual(row["health"], Repository.Health.ONLINE)

    def test_associated_sources_exposes_nas_registration_and_missing_source_fallback(
        self,
    ):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="associated-source-metadata",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
        )
        nas_source = SourceResource.objects.create(
            organization=self.org,
            name="registered-nas-source",
            resource_type=ResourceType.NAS,
        )
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="missing agent backup",
            source_type="agent",
            source_ref_id=999999,
            repository_id=repo.id,
        )
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="nas backup",
            source_type="nas",
            source_ref_id=nas_source.id,
            repository_id=repo.id,
        )

        response = self.client.get(
            f"/api/v1/storage/repositories/{repo.id}/associated-sources/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        rows = {row["source_type"]: row for row in response.data["results"]}
        self.assertEqual(rows["agent"]["source_ref_id"], 999999)
        self.assertIsNone(rows["agent"]["registered_at"])
        self.assertEqual(rows["nas"]["source_ref_id"], nas_source.id)
        self.assertEqual(
            rows["nas"]["registered_at"], nas_source.created_at.isoformat()
        )

    def test_associated_sources_is_paginated_and_supports_non_nas_repositories(self):
        agent_a = Node.objects.create(
            organization=self.org,
            name="source-agent-a",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.8.11",
            metadata={"inventory": {"hostname": "source-a", "os": "linux"}},
        )
        agent_b = Node.objects.create(
            organization=self.org,
            name="source-agent-b",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
            ip_address="10.0.8.12",
            metadata={"inventory": {"hostname": "source-b", "os": "linux"}},
        )
        agent_c = Node.objects.create(
            organization=self.org,
            name="source-agent-c",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.8.13",
            metadata={"inventory": {"hostname": "source-c", "os": "linux"}},
        )
        s3_repo = Repository.objects.create(
            organization_id=self.org.id,
            name="associated-s3",
            repo_type=Repository.Type.S3,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="associated-bucket",
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"endpoint": "https://s3.example.test", "prefix": "kopia"},
        )
        proxy_repo = Repository.objects.create(
            organization_id=self.org.id,
            name="associated-local-disk",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"proxy_node_dir": "/proxy-data/repo"},
        )
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="agent a s3 backup",
            source_type="agent",
            source_ref_id=agent_a.id,
            repository_id=s3_repo.id,
        )
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="agent b s3 backup",
            source_type="agent",
            source_ref_id=agent_b.id,
            repository_id=s3_repo.id,
        )
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="agent c local backup",
            source_type="agent",
            source_ref_id=agent_c.id,
            repository_id=proxy_repo.id,
        )

        page_one = self.client.get(
            f"/api/v1/storage/repositories/{s3_repo.id}/associated-sources/",
            {"page": 1, "page_size": 1},
            **self._headers(),
        )
        page_two = self.client.get(
            f"/api/v1/storage/repositories/{s3_repo.id}/associated-sources/",
            {"page": 2, "page_size": 1},
            **self._headers(),
        )
        local_disk = self.client.get(
            f"/api/v1/storage/repositories/{proxy_repo.id}/associated-sources/",
            {"page": 1, "page_size": 10},
            **self._headers(),
        )

        self.assertEqual(page_one.status_code, status.HTTP_200_OK, page_one.content)
        self.assertEqual(page_two.status_code, status.HTTP_200_OK, page_two.content)
        self.assertEqual(page_one.data["count"], 2)
        self.assertEqual(len(page_one.data["results"]), 1)
        self.assertEqual(len(page_two.data["results"]), 1)
        self.assertEqual(page_one.data["results"][0]["source_name"], "source-agent-a")
        self.assertEqual(
            page_one.data["results"][0]["availability"], Node.Availability.ONLINE
        )
        self.assertEqual(page_one.data["results"][0]["repository_mount_point"], "")
        self.assertEqual(page_two.data["results"][0]["source_name"], "source-agent-b")
        self.assertEqual(
            page_two.data["results"][0]["availability"], Node.Availability.OFFLINE
        )
        self.assertEqual(local_disk.status_code, status.HTTP_200_OK, local_disk.content)
        self.assertEqual(local_disk.data["count"], 1)
        self.assertEqual(local_disk.data["results"][0]["repository_mount_point"], "")

    @mock.patch("apps.storage.tasks.execute_repository_operation.apply_async")
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    def test_create_nas_with_proxy_initializes_repository(
        self, initialize_nas, apply_async
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-node",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.99",
        )

        def _mark_real_mount(repository):
            config = dict(repository.config or {})
            config["proxy_mount_path"] = (
                f"/mnt/hfl/storage-repositories/repo-{repository.id}-node-{proxy.id}"
            )
            repository.config = config
            repository.save(update_fields=["config", "updated_at"])

        initialize_nas.side_effect = _mark_real_mount
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/storage/repositories/",
                {
                    "name": "proxy-nas",
                    "repo_type": "nas",
                    "nas_protocol": "smb",
                    "bind_node_type": "proxy",
                    "bind_node_id": proxy.id,
                    "config": {
                        "server_address": "10.0.0.20",
                        "share_path": "backup",
                        "smb_username": "backup_user",
                        "smb_password": "secret-pass",
                        "proxy_repository_server_host": "Repo-Proxy.Example.Internal.",
                    },
                },
                format="json",
                **self._headers(),
            )
        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["status"], Repository.Status.CREATING)
        self.assertIsNotNone(response.data.get("active_create_task"))
        self.assertEqual(response.data["cross_proxy_access"]["reason"], "ready")
        self.assertEqual(
            response.data["cross_proxy_access"]["host"], "repo-proxy.example.internal"
        )
        repo = Repository.objects.get(name="proxy-nas")
        self.assertEqual(repo.status, Repository.Status.CREATING)
        self.assertEqual(repo.config["share_path"], "/backup")
        apply_async.assert_called_once()

        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "success")
        repo.refresh_from_db()
        self.assertEqual(repo.status, Repository.Status.CREATED)
        self.assertEqual(repo.health, Repository.Health.ONLINE)
        self.assertEqual(
            repo.config["proxy_mount_path"],
            f"/mnt/hfl/storage-repositories/repo-{repo.id}-node-{proxy.id}",
        )
        initialize_nas.assert_called_once()

    def test_create_repository_rejects_repository_server_url(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-invalid-server-host",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        response = self.client.post(
            "/api/v1/storage/repositories/",
            {
                "name": "invalid-server-host",
                "repo_type": "proxy_fs",
                "bind_node_type": "proxy",
                "bind_node_id": proxy.id,
                "config": {
                    "proxy_node_dir": "/data/repository",
                    "proxy_repository_server_host": "https://repo.example.test:51515",
                },
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )

    @mock.patch("apps.storage.tasks.execute_repository_operation.apply_async")
    def test_create_proxy_fs_uses_managed_child_directory(self, apply_async):
        proxy = Node.objects.create(
            organization=self.org,
            name="managed-local-disk-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/storage/repositories/",
                {
                    "name": "managed-local-disk",
                    "repo_type": "proxy_fs",
                    "bind_node_type": "proxy",
                    "bind_node_id": proxy.id,
                    "config": {"proxy_node_base_dir": "/data/backups/"},
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        repository = Repository.objects.get(name="managed-local-disk")
        self.assertEqual(repository.config["proxy_node_base_dir"], "/data/backups")
        self.assertEqual(
            repository.config["proxy_node_dir"],
            f"/data/backups/hfl-repo-{repository.id}",
        )
        self.assertEqual(repository.config["proxy_fs_layout"], "managed_subdir_v1")
        apply_async.assert_called_once()

    def test_create_proxy_fs_rejects_filesystem_root_as_base_directory(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="root-local-disk-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )

        response = self.client.post(
            "/api/v1/storage/repositories/",
            {
                "name": "root-local-disk",
                "repo_type": "proxy_fs",
                "bind_node_type": "proxy",
                "bind_node_id": proxy.id,
                "config": {"proxy_node_base_dir": "/"},
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertFalse(Repository.objects.filter(name="root-local-disk").exists())

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_s3_repository_keeps_row_when_initializer_fails(self, initialize):
        initialize.side_effect = RepositoryInitializationError("S3 init failed")
        response = self._post_repository(self._s3_payload())
        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["status"], Repository.Status.CREATING)
        repo = Repository.objects.get(name="primary-s3")
        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "failed")
        repo.refresh_from_db()
        self.assertEqual(repo.status, Repository.Status.CREATE_FAILED)
        self.assertEqual(repo.health, Repository.Health.OFFLINE)
        claim = RepositoryLocationClaim.objects.get(repository=repo)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        self.assertTrue(Repository.objects.filter(name="primary-s3").exists())

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_s3_rejects_existing_repository_and_cleans_up(self, initialize):
        initialize.side_effect = RepositoryAlreadyExistsError(
            "repository already exists"
        )

        response = self._post_repository(self._s3_payload())

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        repo = Repository.objects.get(name="primary-s3")
        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        self.assertFalse(Repository.objects.filter(name="primary-s3").exists())
        self.assertEqual(
            Credential.objects.filter(organization_id=self.org.id).count(), 0
        )

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_fs_repository"
    )
    def test_create_proxy_fs_rejects_existing_repository_and_cleans_up(
        self, initialize
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-fs-node",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.100",
        )
        initialize.side_effect = RepositoryAlreadyExistsError(
            "repository already exists"
        )

        response = self._post_repository(
            {
                "name": "local-disk",
                "repo_type": "proxy_fs",
                "bind_node_type": "proxy",
                "bind_node_id": proxy.id,
                "config": {"proxy_node_dir": "/data/repository"},
            }
        )

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        repo = Repository.objects.get(name="local-disk")
        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        self.assertFalse(Repository.objects.filter(name="local-disk").exists())
        self.assertEqual(
            Credential.objects.filter(organization_id=self.org.id).count(), 0
        )

    @mock.patch(
        "apps.storage.services.internal.proxy_fs_repository.run_agent_task_sync"
    )
    def test_create_proxy_fs_requires_agent_ownership_capability(
        self, run_agent_task_sync
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-fs-upgrade-required",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.103",
            metadata={"inventory": {"capabilities": []}},
        )
        response = self._post_repository(
            {
                "name": "local-disk-upgrade-required",
                "repo_type": "proxy_fs",
                "bind_node_type": "proxy",
                "bind_node_id": proxy.id,
                "config": {"proxy_node_dir": "/data/repository"},
            }
        )
        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )

        repository = Repository.objects.get(name="local-disk-upgrade-required")
        result = self._run_create_task(repository)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "AGENT_UPGRADE_REQUIRED")
        run_agent_task_sync.assert_not_called()
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATE_FAILED)

    @mock.patch(
        "apps.storage.services.internal.proxy_fs_repository.run_agent_task_sync"
    )
    def test_create_proxy_fs_detects_nested_existing_data_error(
        self, run_agent_task_sync
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-fs-nested-conflict",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.101",
            metadata={"inventory": {"capabilities": ["repository_ownership_v1"]}},
        )
        run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="nested-conflict-task",
                status="failed",
                last_error="exit 1: exit status 1",
            ),
            result={
                "repository_create": {
                    "stderr": (
                        "unable to get repository storage: "
                        "found existing data in storage location"
                    )
                }
            },
            timed_out=False,
            ok=False,
        )

        response = self._post_repository(
            {
                "name": "local-disk-nested-conflict",
                "repo_type": "proxy_fs",
                "bind_node_type": "proxy",
                "bind_node_id": proxy.id,
                "config": {"proxy_node_dir": "/data/existing-repository"},
            }
        )

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        repo = Repository.objects.get(name="local-disk-nested-conflict")
        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        self.assertFalse(
            Repository.objects.filter(name="local-disk-nested-conflict").exists()
        )
        self.assertEqual(
            Credential.objects.filter(organization_id=self.org.id).count(), 0
        )

    @mock.patch(
        "apps.storage.services.internal.proxy_fs_repository.run_agent_task_sync"
    )
    def test_create_proxy_fs_surfaces_nested_failure_reason(self, run_agent_task_sync):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-fs-nested-failure",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.102",
            metadata={"inventory": {"capabilities": ["repository_ownership_v1"]}},
        )
        run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="nested-failure-task",
                status="failed",
                last_error="exit 1: exit status 1",
            ),
            result={"repository_create": {"stderr": "permission denied"}},
            timed_out=False,
            ok=False,
        )

        response = self._post_repository(
            {
                "name": "local-disk-nested-failure",
                "repo_type": "proxy_fs",
                "bind_node_type": "proxy",
                "bind_node_id": proxy.id,
                "config": {"proxy_node_dir": "/data/repository"},
            }
        )

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        repo = Repository.objects.get(name="local-disk-nested-failure")
        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "failed")
        self.assertIn("permission denied", result["error"])
        repository = Repository.objects.get(name="local-disk-nested-failure")
        self.assertEqual(repository.status, Repository.Status.CREATE_FAILED)
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        repository_task = RepositoryTask.objects.get(repository=repository)
        self.assertIn("permission denied", repository_task.task.error_message)

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_failed_repositories_are_listed_by_default(self, initialize):
        initialize.side_effect = RepositoryInitializationError("S3 init failed")
        create = self._post_repository(self._s3_payload())
        self.assertEqual(create.status_code, status.HTTP_202_ACCEPTED, create.content)
        repo = Repository.objects.get(name="primary-s3")
        result = self._run_create_task(repo)
        self.assertEqual(result["status"], "failed")
        repo.refresh_from_db()
        self.assertEqual(repo.status, Repository.Status.CREATE_FAILED)
        Repository.objects.create(
            organization_id=self.org.id,
            name="legacy-failed-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATE_FAILED,
            health=Repository.Health.OFFLINE,
            config={"endpoint": "https://s3.example.com"},
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="legacy-bucket",
        )
        listing = self.client.get(
            "/api/v1/storage/repositories/",
            {"repo_type": "s3"},
            **self._headers(),
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        names = [row["name"] for row in listing.data["data"]["list"]]
        self.assertIn("primary-s3", names)
        self.assertIn("legacy-failed-s3", names)

    @mock.patch("apps.storage.tasks.reconcile_storage_repositories.apply_async")
    def test_sync_usage_all_queues_async_refresh(self, apply_async):
        apply_async.return_value = SimpleNamespace(id="usage-refresh-task")
        response = self.client.post(
            "/api/v1/storage/repositories/sync-usage/",
            {"repo_type": "nas", "limit": 200, "stale_after_seconds": 900},
            format="json",
            **self._headers(),
        )
        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["queued"], True)
        self.assertEqual(response.data["task_id"], "usage-refresh-task")
        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs["kwargs"]
        self.assertEqual(kwargs["organization_id"], self.org.id)
        self.assertEqual(kwargs["repo_type"], Repository.Type.NAS)
        self.assertEqual(kwargs["limit"], 200)
        self.assertEqual(kwargs["stale_after_seconds"], 900)

    @mock.patch("apps.storage.tasks.reconcile_storage_repositories.apply_async")
    def test_sync_usage_detail_queues_single_repository_refresh(self, apply_async):
        apply_async.return_value = SimpleNamespace(id="usage-refresh-repo")
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="nas-refresh",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "10.0.0.20", "share_path": "/backup"},
        )
        response = self.client.post(
            f"/api/v1/storage/repositories/{repo.id}/sync-usage/",
            {},
            format="json",
            **self._headers(),
        )
        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["queued"], True)
        kwargs = apply_async.call_args.kwargs["kwargs"]
        self.assertEqual(kwargs["organization_id"], self.org.id)
        self.assertEqual(kwargs["repository_ids"], [repo.id])
        self.assertEqual(kwargs["force"], True)

    @mock.patch("apps.storage.repositories.views.validate_s3_connection")
    def test_validate_s3_connection_returns_buckets(self, validate_s3_connection):
        validate_s3_connection.return_value = [
            "bucket-a",
            "bucket-b",
            "bucket-c",
            "bucket-d",
        ]
        payload = {
            "endpoint": "https://s3.amazonaws.com",
            "region": "us-east-1",
            "access_key_id": "AKIA_TEST",
            "secret_access_key": "super-secret",
            "s3_url_style": "virtual_hosted",
            "use_tls": True,
            "count": 3,
        }
        response = self.client.post(
            "/api/v1/storage/repositories/validate/s3/",
            payload,
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["buckets"], ["bucket-a", "bucket-b", "bucket-c"])
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(response.data["total_count"], 4)
        validate_s3_connection.assert_called_once_with(
            platform="custom",
            endpoint="s3.amazonaws.com",
            region="us-east-1",
            access_key_id="AKIA_TEST",
            secret_access_key="super-secret",
            s3_url_style="virtual_hosted",
            use_tls=True,
        )

        no_slash_response = self.client.post(
            "/api/v1/storage/repositories/validate/s3",
            payload,
            format="json",
            **self._headers(),
        )
        self.assertEqual(no_slash_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            no_slash_response.data["buckets"],
            ["bucket-a", "bucket-b", "bucket-c"],
        )

    @mock.patch("apps.storage.repositories.views.validate_s3_connection")
    def test_managed_connection_validation_always_uses_external_endpoint(
        self, validate_s3_connection
    ):
        validate_s3_connection.return_value = []

        response = self.client.post(
            "/api/v1/storage/repositories/validate/s3/",
            {
                "platform": "aliyun",
                "region": "cn-hangzhou",
                "endpoint": "oss-cn-hangzhou-internal.aliyuncs.com",
                "access_key_id": "AKIA_TEST",
                "secret_access_key": "super-secret",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        validate_s3_connection.assert_called_once_with(
            platform="aliyun",
            endpoint="oss-cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            access_key_id="AKIA_TEST",
            secret_access_key="super-secret",
            s3_url_style="virtual_hosted",
            use_tls=True,
        )

    @mock.patch("apps.storage.repositories.views.validate_s3_connection")
    def test_validate_s3_failure_returns_stable_safe_error(
        self, validate_s3_connection
    ):
        validate_s3_connection.side_effect = RepositoryInitializationError(
            "InvalidAccessKeyId: secret-token-value"
        )

        response = self.client.post(
            "/api/v1/storage/repositories/validate/s3/",
            {
                "endpoint": "s3.example.com",
                "access_key_id": "AKIA_SECRET",
                "secret_access_key": "super-secret",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        problem = response.data["data"]
        self.assertEqual(problem["code"], "STORAGE.S3_VALIDATION_FAILED")
        self.assertNotIn("diagnostic", problem["meta"])
        self.assertNotIn("AKIA_SECRET", str(response.data))
        self.assertNotIn("super-secret", str(response.data))
        self.assertNotIn("secret-token-value", str(response.data))

    @mock.patch("apps.storage.repositories.views.validate_s3_connection")
    def test_validate_s3_sdk_type_error_returns_configuration_error(
        self, validate_s3_connection
    ):
        try:
            raise TypeError("expected string or bytes-like object, got 'NoneType'")
        except TypeError as upstream:
            validate_s3_connection.side_effect = RepositoryInitializationError(
                "Unable to list S3 buckets"
            )
            validate_s3_connection.side_effect.__cause__ = upstream

        response = self.client.post(
            "/api/v1/storage/repositories/validate/s3/",
            {
                "platform": "other",
                "endpoint": "192.0.2.81:9443",
                "region": "",
                "access_key_id": "002",
                "secret_access_key": "not-a-real-secret",
                "s3_url_style": "auto",
                "use_tls": False,
                "count": 1000,
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        problem = response.data["data"]
        self.assertEqual(problem["code"], "STORAGE.S3_CONFIGURATION_INVALID")
        self.assertNotIn("NoneType", str(response.data))
        self.assertNotIn("not-a-real-secret", str(response.data))

    def test_nas_repository_payload_converts_normalized_smb_share_for_mount(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="payload-smb-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.SMB,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/backup",
                "smb_username": "backup_user",
                "smb_password": "secret-pass",
            },
        )

        payload = nas_repository_payload(
            repository=repo,
            subdir=nas_proxy_repository_subdir(repo),
            node_id=99,
        )

        self.assertEqual(payload["nas"]["share"], "backup")

    def test_proxy_nas_repository_subdir_includes_storage_id(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="payload-proxy-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/volume1/backup",
            },
        )

        self.assertEqual(
            nas_proxy_repository_subdir(repo), f"hp-repos/storage-{repo.id}"
        )

    def test_proxy_bound_nas_repository_payload_requires_bound_proxy(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="repo-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        agent = Node.objects.create(
            organization=self.org,
            name="source-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="payload-bound-proxy-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/volume1/backup",
                "kopia_password": "repo-pass",
            },
        )

        payload = repository_payload_for_node(
            repository=repo,
            node=proxy,
            source_type="proxy",
            source_ref_id=proxy.id,
        )

        self.assertEqual(payload["type"], Repository.Type.NAS)
        self.assertEqual(payload["subdir"], f"hp-repos/storage-{repo.id}")
        self.assertEqual(
            payload["nas"]["mount_point"],
            repository_mount_point(repo.id, node_id=proxy.id),
        )
        with self.assertRaises(ValidationError):
            repository_payload_for_node(
                repository=repo,
                node=agent,
                source_type="agent",
                source_ref_id=agent.id,
            )

    def test_repository_list_is_tenant_scoped(self):
        other_user = get_user_model().objects.create_user(
            username="storage-other@test.local",
            password="test-pass",
        )
        other_org = Organization.objects.create(
            key="storage-other-org", name="Storage Other Org"
        )
        Membership.objects.create(
            user=other_user,
            organization=other_org,
            role=Membership.Role.ADMIN,
        )
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="org1-repo",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            config={"proxy_node_dir": "/data/repo"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=1,
        )

        self.client.force_authenticate(user=other_user)
        response = self.client.get(
            "/api/v1/storage/repositories/",
            **self._headers(other_org),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["list"]
        self.assertNotIn(repo.id, [row["id"] for row in rows])

    def test_delete_repository_with_backup_configs_is_rejected(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="repo-with-snapshot",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            config={"access_key_id": "AKIA_TEST"},
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="bucket",
        )
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="repo-bound-config",
            source_type="agent",
            source_ref_id=11,
            repository_id=repo.id,
            compression_level="balanced",
        )

        response = self.client.delete(
            f"/api/v1/storage/repositories/{repo.id}/",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Repository.objects.filter(id=repo.id).exists())

    def test_delete_repository_preserves_completed_maintenance_tasks_and_creates_cleanup(
        self,
    ):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="repo-with-completed-maintenance",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="completed-maintenance-bucket",
            config={"prefix": "hfl", "access_key_id": "completed-maintenance"},
        )
        reserve_repository_location(repo)
        mark_repository_location_owned(repo)
        mark_repository_location_ownership_verified(repo)
        discover_repository_execution_targets()
        target = RepositoryExecutionTarget.objects.get(repository=repo)
        repository_task = create_repository_operation_task(
            target_id=target.id,
            operation_type=RepositoryTask.OperationType.MAINTENANCE_QUICK,
        )
        repository_task.task.status = Task.Status.SUCCESS
        repository_task.task.save(update_fields=["status", "updated_at"])
        task_id = repository_task.task_id

        response = self.client.delete(
            f"/api/v1/storage/repositories/{repo.id}/",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        repo.refresh_from_db()
        self.assertEqual(repo.status, Repository.Status.REMOVING)
        self.assertTrue(RepositoryExecutionTarget.objects.filter(id=target.id).exists())
        self.assertTrue(RepositoryTask.objects.filter(id=repository_task.id).exists())
        self.assertTrue(Task.objects.filter(id=task_id).exists())
        cleanup_task = RepositoryTask.objects.get(
            repository=repo,
            operation_type=RepositoryTask.OperationType.CLEANUP_REPOSITORY,
        )
        self.assertEqual(cleanup_task.task.status, Task.Status.PENDING)
        self.assertIsNone(cleanup_task.triggered_by_task_id)
        self.assertEqual(response.data["task_uuid"], str(cleanup_task.task.task_uuid))

    def test_delete_repository_with_active_maintenance_task_is_rejected(self):
        repo = Repository.objects.create(
            organization_id=self.org.id,
            name="repo-with-active-maintenance",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="active-maintenance-bucket",
            config={"prefix": "hfl", "access_key_id": "active-maintenance"},
        )
        reserve_repository_location(repo)
        mark_repository_location_owned(repo)
        mark_repository_location_ownership_verified(repo)
        discover_repository_execution_targets()
        target = RepositoryExecutionTarget.objects.get(repository=repo)
        create_repository_operation_task(
            target_id=target.id,
            operation_type=RepositoryTask.OperationType.MAINTENANCE_QUICK,
        )

        response = self.client.delete(
            f"/api/v1/storage/repositories/{repo.id}/",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_409_CONFLICT, response.content
        )
        self.assertTrue(Repository.objects.filter(id=repo.id).exists())

    @mock.patch("apps.storage.tasks.execute_repository_operation.apply_async")
    def test_manual_repository_check_returns_a_worker_task(self, dispatch):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="manual-check",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="manual-check-bucket",
            config={
                "endpoint": "s3.example.test",
                "prefix": "hfl/",
                "access_key_id": "manual-check-key",
            },
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/api/v1/storage/repositories/{repository.id}/check/",
                {},
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        repository_task = RepositoryTask.objects.get(
            repository=repository,
            operation_type=RepositoryTask.OperationType.CHECK,
        )
        self.assertEqual(
            response.data["task"]["task_uuid"],
            str(repository_task.task.task_uuid),
        )
        dispatch.assert_called_once_with(
            kwargs={"repository_task_id": repository_task.id}
        )

    @mock.patch("apps.storage.tasks.execute_repository_operation.apply_async")
    def test_s3_credential_update_stages_then_activates_new_secret(
        self,
        dispatch,
    ):
        credential = Credential(
            organization_id=self.org.id,
            credential_type=Credential.Type.S3,
        )
        credential.set_secret_payload(
            {
                "secret_access_key": "old-secret",
                "kopia_password": "repository-password",
            }
        )
        credential.save()
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="credential-update",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            credential_id=credential.id,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="credential-update-bucket",
            config={
                "endpoint": "s3.example.test",
                "region": "us-east-1",
                "prefix": "hfl/",
                "access_key_id": "old-access-key",
                "s3_url_style": "path",
                "use_tls": True,
            },
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                f"/api/v1/storage/repositories/{repository.id}/",
                {
                    "config": {
                        "access_key_id": "new-access-key",
                        "secret_access_key": "new-secret",
                    }
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        repository.refresh_from_db()
        self.assertEqual(repository.credential_id, credential.id)
        self.assertEqual(repository.config["access_key_id"], "old-access-key")
        repository_task = RepositoryTask.objects.get(
            repository=repository,
            operation_type=RepositoryTask.OperationType.CREDENTIAL_ROTATE,
        )
        self.assertEqual(
            response.data["task"]["task_uuid"],
            str(repository_task.task.task_uuid),
        )
        self.assertNotIn("new-secret", str(response.data))
        self.assertNotIn("new-secret", str(repository_task.task.request_payload))
        dispatch.assert_called_once_with(
            kwargs={"repository_task_id": repository_task.id}
        )

        with mock.patch(
            "apps.storage.services.internal.repository_credential_rotation.check_s3_repository"
        ), mock.patch(
            "apps.storage.services.internal.repository_credential_rotation.enqueue_repository_usage_refresh"
        ):
            result = run_repository_credential_rotation_task(
                repository_task_id=repository_task.id
            )

        self.assertEqual(result["status"], "success")
        repository.refresh_from_db()
        self.assertNotEqual(repository.credential_id, credential.id)
        active_secret = Credential.objects.get(
            id=repository.credential_id
        ).get_secret_payload()
        self.assertEqual(active_secret["secret_access_key"], "new-secret")
        self.assertNotIn("secret_access_key", repository.config)
