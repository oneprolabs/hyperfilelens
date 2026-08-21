from __future__ import annotations

import copy
import json
from datetime import timedelta
from unittest.mock import Mock, call, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.storage.provider_catalog import cloud_validation
from apps.storage.provider_catalog.catalog import load_default_catalog
from apps.storage.provider_catalog.credentials import (
    CREDENTIAL_CACHE_PREFIX,
    ProviderCredentialUnavailable,
    ProviderCredentials,
    delete_validation_credentials,
    load_validation_credentials,
)
from apps.storage.provider_catalog.errors import (
    ProviderCatalogConflictError,
    ProviderCatalogValidationError,
    ProviderEndpointPolicyError,
)
from apps.storage.provider_catalog.models import (
    StorageProviderRegionValidation,
    StorageProviderValidationRun,
)
from apps.storage.provider_catalog.schema import provider_checksum
from apps.storage.provider_catalog.security import validate_managed_endpoint_network
from apps.storage.provider_catalog.validation import (
    cancel_validation_run,
    cleanup_expired_validation_runs,
    cleanup_validation_run,
    create_validation_run,
    execute_validation_run,
    import_validation_evidence,
    serialize_validation_run,
)
from apps.task.models import Task


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "provider-catalog-validation",
    }
}


def _aliyun_candidate(*, suffix: str = "") -> dict:
    provider = next(
        copy.deepcopy(item)
        for item in load_default_catalog()["providers"]
        if item["id"] == "aliyun"
    )
    provider["display_name"] += suffix
    return provider


@override_settings(CACHES=TEST_CACHES)
class ProviderCatalogValidationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="provider-validator@example.com",
            password="Pass1234",
            is_staff=True,
        )

    def tearDown(self):
        cache.clear()

    def _create(self, *, candidate=None, region_ids=None):
        candidate = candidate or _aliyun_candidate()
        with patch(
            "apps.storage.provider_catalog.validation.current_app.send_task"
        ) as send_task:
            run = create_validation_run(
                provider_id=candidate["id"],
                region_ids=(
                    region_ids
                    if region_ids is not None
                    else [region["id"] for region in candidate["regions"][:2]]
                ),
                access_key_id="test-access-key",
                secret_access_key="test-secret-key",
                requested_by_id=self.user.pk,
                candidate_config=candidate,
            )
        return run, send_task

    def test_credentials_are_encrypted_ttl_bound_and_never_serialized(self):
        run, send_task = self._create()

        raw = cache.get(f"{CREDENTIAL_CACHE_PREFIX}:{run.id}")
        self.assertIsInstance(raw, str)
        self.assertNotIn("test-access-key", raw)
        self.assertNotIn("test-secret-key", raw)
        self.assertEqual(
            load_validation_credentials(run.id).access_key_id, "test-access-key"
        )

        task = Task.objects.get(pk=run.task_id)
        persisted = json.dumps(
            {
                "run": serialize_validation_run(run),
                "task_request": task.request_payload,
                "task_result": task.result_payload,
            },
            default=str,
        )
        self.assertNotIn("test-access-key", persisted)
        self.assertNotIn("test-secret-key", persisted)
        send_task.assert_called_once()
        self.assertEqual(send_task.call_args.kwargs["args"], [str(run.id)])
        self.assertNotIn("test-access-key", json.dumps(send_task.call_args.kwargs))

    def test_credential_store_failure_preserves_replaceable_run(self):
        old, _send_task = self._create()
        old.status = StorageProviderValidationRun.Status.VALIDATION_FAILED
        old.save(update_fields=["status", "updated_at"])

        with patch(
            "apps.storage.provider_catalog.validation.store_validation_credentials",
            side_effect=RuntimeError("redis unavailable"),
        ):
            with self.assertRaises(ProviderCatalogValidationError):
                create_validation_run(
                    provider_id="aliyun",
                    region_ids=[_aliyun_candidate()["regions"][0]["id"]],
                    access_key_id="replacement-access",
                    secret_access_key="replacement-secret",
                    requested_by_id=self.user.pk,
                    candidate_config=_aliyun_candidate(suffix=" Replacement"),
                )

        self.assertTrue(StorageProviderValidationRun.objects.filter(pk=old.pk).exists())

    def test_active_and_cleanup_required_runs_cannot_be_replaced(self):
        run, _send_task = self._create()
        for status in (
            StorageProviderValidationRun.Status.PENDING,
            StorageProviderValidationRun.Status.CLEANUP_REQUIRED,
        ):
            run.status = status
            run.save(update_fields=["status", "updated_at"])
            with self.assertRaises(ProviderCatalogConflictError):
                with patch(
                    "apps.storage.provider_catalog.validation.current_app.send_task"
                ):
                    create_validation_run(
                        provider_id="aliyun",
                        region_ids=[_aliyun_candidate()["regions"][0]["id"]],
                        access_key_id="other-access",
                        secret_access_key="other-secret",
                        requested_by_id=self.user.pk,
                        candidate_config=_aliyun_candidate(suffix=" Other"),
                    )
            self.assertTrue(
                StorageProviderValidationRun.objects.filter(pk=run.pk).exists()
            )

    def test_active_worker_owns_cancellation_cleanup_dispatch(self):
        run, _send_task = self._create()
        run.status = StorageProviderValidationRun.Status.VALIDATING
        run.save(update_fields=["status", "updated_at"])

        with patch(
            "apps.storage.provider_catalog.validation.current_app.send_task"
        ) as send_task:
            cancel_validation_run(
                run_id=run.id,
                requested_by_id=self.user.pk,
            )

        run.refresh_from_db()
        self.assertEqual(run.status, StorageProviderValidationRun.Status.CANCELLING)
        send_task.assert_not_called()

    def test_cancel_without_cloud_resources_does_not_require_expired_credentials(self):
        run, _send_task = self._create()
        region = run.region_validations.order_by("id").first()
        assert region is not None
        region.status = StorageProviderRegionValidation.Status.RUNNING
        region.current_step = StorageProviderRegionValidation.Step.CREATE_BUCKET
        region.save(update_fields=["status", "current_step", "updated_at"])
        run.status = StorageProviderValidationRun.Status.VALIDATION_FAILED
        run.save(update_fields=["status", "updated_at"])
        delete_validation_credentials(run.id)

        with patch("apps.storage.provider_catalog.validation.current_app.send_task"):
            cancel_validation_run(
                run_id=run.id,
                requested_by_id=self.user.pk,
            )
        cleanup_validation_run(run.id)

        run.refresh_from_db()
        self.assertEqual(run.status, StorageProviderValidationRun.Status.CANCELLED)
        self.assertIsNone(run.candidate_config)
        self.assertIsNone(run.candidate_checksum)
        region.refresh_from_db()
        self.assertEqual(region.status, StorageProviderRegionValidation.Status.CANCELLED)
        self.assertIsNone(region.current_step)
        self.assertFalse(
            run.region_validations.exclude(
                status=StorageProviderRegionValidation.Status.CANCELLED
            ).exists()
        )
        with self.assertRaises(ProviderCredentialUnavailable):
            load_validation_credentials(run.id)

        with patch("apps.storage.provider_catalog.validation.current_app.send_task"):
            replacement = create_validation_run(
                provider_id="aliyun",
                region_ids=[_aliyun_candidate()["regions"][0]["id"]],
                access_key_id="replacement-access-key",
                secret_access_key="replacement-secret-key",
                requested_by_id=self.user.pk,
                candidate_config=_aliyun_candidate(suffix=" Replacement"),
            )
        self.assertNotEqual(replacement.id, run.id)

    def test_worker_success_creates_current_validation_evidence(self):
        candidate = _aliyun_candidate(suffix=" Validated")
        run, _send_task = self._create(candidate=candidate)
        with patch("apps.storage.provider_catalog.validation.validate_region"):
            execute_validation_run(run.id)

        run.refresh_from_db()
        self.assertEqual(
            run.status,
            StorageProviderValidationRun.Status.PASSED,
        )
        self.assertFalse(
            run.region_validations.exclude(
                status=StorageProviderRegionValidation.Status.SUCCESS
            ).exists()
        )
        evidence = import_validation_evidence(
            provider_id="aliyun",
            candidate_checksum=provider_checksum(candidate),
            requested_by_id=self.user.pk,
        )
        self.assertEqual(evidence["status"], "passed_partial")

    def test_dynamic_s3_provider_validates_without_static_adapter(self):
        candidate = _aliyun_candidate()
        candidate.update(
            {
                "id": "dynamiccloud",
                "display_name": "Dynamic Cloud Object Storage",
            }
        )
        candidate["regions"] = [candidate["regions"][0]]
        candidate["regions"][0].update(
            {
                "id": "region-one",
                "external_endpoint": "s3.region-one.dynamiccloud.example",
                "internal_endpoint": "s3-internal.region-one.dynamiccloud.example",
            }
        )

        run, _send_task = self._create(candidate=candidate)
        with patch("apps.storage.provider_catalog.validation.validate_region"):
            execute_validation_run(run.id)

        evidence = import_validation_evidence(
            provider_id=candidate["id"],
            candidate_checksum=provider_checksum(candidate),
            requested_by_id=self.user.pk,
        )
        self.assertEqual(evidence["status"], "passed_complete")

    def test_create_requires_one_to_ten_known_unique_regions(self):
        candidate = _aliyun_candidate()
        invalid_region_sets = [
            [],
            [candidate["regions"][0]["id"]] * 2,
            ["unknown-region"],
            [region["id"] for region in candidate["regions"][:11]],
        ]
        for region_ids in invalid_region_sets:
            with self.subTest(region_ids=region_ids):
                with self.assertRaises(ProviderCatalogValidationError):
                    self._create(candidate=candidate, region_ids=region_ids)

    def test_worker_failure_is_sanitized_and_cleanup_failure_is_retained(self):
        run, _send_task = self._create()
        with patch(
            "apps.storage.provider_catalog.validation.validate_region",
            side_effect=cloud_validation.ProviderRegionValidationError(
                "BUCKET_CLEANUP_FAILED",
                "secret_access_key=test-secret-key cleanup failed",
                cleanup_required=True,
            ),
        ):
            execute_validation_run(run.id)

        run.refresh_from_db()
        self.assertEqual(
            run.status,
            StorageProviderValidationRun.Status.CLEANUP_REQUIRED,
        )
        self.assertNotIn("test-secret-key", run.error_message or "")
        region = run.region_validations.get(status="cleanup_failed")
        self.assertNotIn("test-secret-key", region.error_message or "")

        run.finished_at = timezone.now() - timedelta(days=3)
        run.save(update_fields=["finished_at", "updated_at"])
        cleanup_expired_validation_runs()
        self.assertTrue(StorageProviderValidationRun.objects.filter(pk=run.pk).exists())

    def test_endpoint_policy_failure_marks_region_failed_and_allows_replacement(self):
        candidate = _aliyun_candidate()
        run, _send_task = self._create(
            candidate=candidate,
            region_ids=[candidate["regions"][0]["id"]],
        )
        with patch(
            "apps.storage.provider_catalog.validation.validate_region",
            side_effect=ProviderEndpointPolicyError(
                "ENDPOINT_PRIVATE_ADDRESS",
                "Endpoint resolves to an unauthorized network address.",
            ),
        ):
            execute_validation_run(run.id)

        run.refresh_from_db()
        region = run.region_validations.get()
        self.assertEqual(run.status, StorageProviderValidationRun.Status.VALIDATION_FAILED)
        self.assertEqual(run.error_code, "ENDPOINT_PRIVATE_ADDRESS")
        self.assertEqual(region.status, StorageProviderRegionValidation.Status.FAILED)
        self.assertEqual(region.error_code, "ENDPOINT_PRIVATE_ADDRESS")

        with patch("apps.storage.provider_catalog.validation.current_app.send_task"):
            replacement = create_validation_run(
                provider_id="aliyun",
                region_ids=[candidate["regions"][0]["id"]],
                access_key_id="replacement-access-key",
                secret_access_key="replacement-secret-key",
                requested_by_id=self.user.pk,
                candidate_config=candidate,
            )
        self.assertNotEqual(replacement.id, run.id)

    def test_model_constraints_reject_duplicate_region_and_invalid_status(self):
        run, _send_task = self._create()
        region = run.region_validations.first()
        assert region is not None
        with self.assertRaises(IntegrityError), transaction.atomic():
            StorageProviderRegionValidation.objects.create(
                run=run,
                region_id=region.region_id,
                region_group=region.region_group,
                region_group_en=region.region_group_en,
                external_endpoint=region.external_endpoint,
                internal_endpoint=region.internal_endpoint,
                driver=region.driver,
                s3_url_style=region.s3_url_style,
                use_tls=region.use_tls,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            StorageProviderValidationRun.objects.filter(pk=run.pk).update(
                status="success"
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            StorageProviderValidationRun.objects.filter(pk=run.pk).update(
                candidate_config=None,
                candidate_checksum=None,
            )


class ProviderBucketOwnershipTests(SimpleTestCase):
    def test_validation_bucket_creation_targets_the_selected_region(self):
        cases = [
            (
                "huaweicloud",
                "cn-north-4",
                {"LocationConstraint": "cn-north-4"},
            ),
            ("aws", "us-east-1", None),
        ]
        for provider_id, region_id, expected_configuration in cases:
            with self.subTest(provider_id=provider_id, region_id=region_id):
                context = cloud_validation.RegionValidationContext(
                    run_id=StorageProviderValidationRun._meta.get_field("id").default(),
                    provider_id=provider_id,
                    region={"id": region_id},
                    credentials=ProviderCredentials("access", "secret"),
                )

                args = cloud_validation._create_bucket_args(context, "validation-bucket")

                self.assertEqual(args["Bucket"], "validation-bucket")
                self.assertEqual(
                    args.get("CreateBucketConfiguration"),
                    expected_configuration,
                )

    def test_cloud_client_error_retains_safe_provider_diagnostics(self):
        error = cloud_validation.ClientError(
            {
                "Error": {
                    "Code": "InvalidLocationConstraint",
                    "Message": "The specified location does not match the endpoint.",
                },
                "ResponseMetadata": {"HTTPStatusCode": 400},
            },
            "CreateBucket",
        )

        message = cloud_validation._cloud_operation_message(error)

        self.assertEqual(
            message,
            "Cloud storage validation failed: InvalidLocationConstraint: "
            "The specified location does not match the endpoint.",
        )

    @patch("apps.storage.provider_catalog.security.socket.getaddrinfo")
    def test_managed_network_check_accepts_any_public_https_hostname(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("8.8.8.8", 443)),
        ]

        validate_managed_endpoint_network("https://objects.example.net")

    @patch("apps.storage.provider_catalog.security.socket.getaddrinfo")
    def test_managed_network_check_still_rejects_private_addresses(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]

        with self.assertRaises(ProviderEndpointPolicyError) as caught:
            validate_managed_endpoint_network("https://objects.example.net")

        self.assertEqual(caught.exception.code, "ENDPOINT_PRIVATE_ADDRESS")

    @patch(
        "apps.storage.provider_catalog.security.provider_validation_allow_proxy_fake_ip",
        return_value=True,
    )
    @patch("apps.storage.provider_catalog.security.socket.getaddrinfo")
    def test_managed_network_check_accepts_proxy_fake_ip_when_enabled(
        self,
        getaddrinfo,
        _allow_proxy_fake_ip,
    ):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("198.18.2.161", 443)),
            (10, 1, 6, "", ("fdfe:dcba:9876::152", 443, 0, 0)),
        ]

        validate_managed_endpoint_network("https://objects.example.net")

    @patch(
        "apps.storage.provider_catalog.security.provider_validation_allow_proxy_fake_ip",
        return_value=True,
    )
    @patch("apps.storage.provider_catalog.security.socket.getaddrinfo")
    def test_proxy_fake_ip_setting_does_not_allow_other_private_addresses(
        self,
        getaddrinfo,
        _allow_proxy_fake_ip,
    ):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]

        with self.assertRaises(ProviderEndpointPolicyError):
            validate_managed_endpoint_network("https://objects.example.net")

    def test_validation_connection_uses_external_endpoint_only(self):
        provider = _aliyun_candidate()
        region = copy.deepcopy(provider["regions"][0])
        context = cloud_validation.RegionValidationContext(
            run_id=StorageProviderValidationRun._meta.get_field("id").default(),
            provider_id="aliyun",
            region=region,
            credentials=ProviderCredentials("access", "secret"),
        )
        built_client = Mock()
        with (
            patch(
                "apps.storage.provider_catalog.cloud_validation.boto3.client",
                return_value=built_client,
            ) as client_factory,
            patch(
                "apps.storage.provider_catalog.cloud_validation."
                "register_s3_delete_objects_compatibility"
            ) as register_compatibility,
        ):
            result = cloud_validation._s3_client(context)

        self.assertIs(result, built_client)
        self.assertEqual(
            client_factory.call_args.kwargs["endpoint_url"],
            f"https://{region['external_endpoint']}",
        )
        register_compatibility.assert_called_once_with(built_client)

    def test_validation_cleanup_falls_back_to_exact_object_deletion(self):
        client = Mock()
        client.delete_objects.side_effect = cloud_validation.ClientError(
            {
                "Error": {
                    "Code": "MissingArgument",
                    "Message": "Missing Some Required Arguments.",
                }
            },
            "DeleteObjects",
        )
        entries = [
            {"Key": "validation/object"},
            {"Key": "validation/versioned", "VersionId": "version-1"},
        ]

        cloud_validation._delete_entries(client, "validation-bucket", entries)

        self.assertEqual(
            client.delete_object.call_args_list,
            [
                call(Bucket="validation-bucket", Key="validation/object"),
                call(
                    Bucket="validation-bucket",
                    Key="validation/versioned",
                    VersionId="version-1",
                ),
            ],
        )

    def test_bucket_is_not_deleted_when_cryptographic_proof_does_not_match(self):
        context = cloud_validation.RegionValidationContext(
            run_id=StorageProviderValidationRun._meta.get_field("id").default(),
            provider_id="aliyun",
            region={
                "id": "cn-hangzhou",
                "external_endpoint": "oss-cn-hangzhou.aliyuncs.com",
                "s3_url_style": "virtual_hosted",
                "use_tls": True,
            },
            credentials=ProviderCredentials("access", "secret"),
        )
        body = Mock()
        body.read.return_value = b'{"proof":"belongs-to-another-run"}'
        client = Mock()
        client.get_object.return_value = {"Body": body}

        with self.assertRaises(
            cloud_validation.ProviderRegionValidationError
        ) as caught:
            cloud_validation._delete_owned_bucket(
                context,
                client=client,
                bucket_name="hfl-val-untrusted",
            )

        self.assertTrue(caught.exception.cleanup_required)
        self.assertEqual(caught.exception.code, "BUCKET_OWNERSHIP_UNPROVEN")
        client.delete_bucket.assert_not_called()
