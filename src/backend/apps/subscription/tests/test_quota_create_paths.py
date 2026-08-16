"""Create-path integration tests for Extension-owned quota decisions."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.alert.constants import AlertSeverity, AlertType
from apps.alert.models import AlertPolicy
from apps.iam.models import Membership, Organization
from apps.iam.services.membership_service import (
    create_org_membership,
    update_org_membership,
)
from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.source.services.interface import create_source_resource, update_source_resource
from apps.storage.repositories.models import Credential, Repository
from apps.storage.services.interface import create_repository
from common.errors import AppError
from common.extension_spi import (
    clear_providers_for_tests,
    register_quota_provider,
    restore_providers_for_tests,
)


class _RecordingBlockingProvider:
    """QuotaProvider stub that records and rejects every consumption request."""

    def __init__(self) -> None:
        self.calls: list[tuple[Organization, str, int | float]] = []

    def check_quota(
        self,
        organization: Organization,
        resource_type: str,
        additional: int | float = 1,
    ) -> None:
        self.calls.append((organization, resource_type, additional))
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title="blocked",
            diagnostic="blocked",
            meta={"quota_type": resource_type},
        )

    def get_limits(self, organization: Organization) -> dict[str, int]:
        return {}

    def validate_quota(
        self,
        organization: Organization,
        quota_type: str,
        amount: int = 1,
    ) -> dict:
        return {"is_valid": False, "quota_type": quota_type}

    def on_license_activated(self, organization: Organization, license_obj) -> None:
        return None


class _BlockUsersProvider(_RecordingBlockingProvider):
    """Allow organization count checks and reject the automatic Owner only."""

    def check_quota(
        self,
        organization: Organization | None,
        resource_type: str,
        additional: int | float = 1,
    ) -> None:
        self.calls.append((organization, resource_type, additional))
        if resource_type == "max_users":
            raise AppError(
                code="SUBSCRIPTION.QUOTA_EXCEEDED",
                status=403,
                title="blocked",
                diagnostic="blocked",
                meta={"quota_type": resource_type},
            )


class QuotaCreatePathTests(TestCase):
    """Host business entry points must consult the registered provider first."""

    def setUp(self) -> None:
        self._spi_previous = clear_providers_for_tests()
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="quota-owner@test.local",
            email="quota-owner@test.local",
            password="test-pass",
        )
        self.organization = Organization.objects.create(
            key="quota-create-paths",
            name="Quota Create Paths",
        )
        Membership.objects.create(
            user=self.owner,
            organization=self.organization,
            role=Membership.Role.OWNER,
            is_active=True,
        )
        self.provider = _RecordingBlockingProvider()
        register_quota_provider(self.provider)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def tearDown(self) -> None:
        restore_providers_for_tests(self._spi_previous)

    def assert_last_check(
        self, resource_type: str, additional: int | float = 1
    ) -> None:
        self.assertEqual(len(self.provider.calls), 1)
        organization, actual_resource, actual_additional = self.provider.calls[-1]
        self.assertEqual(organization.pk, self.organization.pk)
        self.assertEqual(actual_resource, resource_type)
        self.assertEqual(actual_additional, additional)

    def test_node_rest_create_is_closed_in_favor_of_enrollment(self) -> None:
        response = self.client.post(
            "/api/v1/node/nodes/",
            {
                "organization": self.organization.id,
                "name": "manual-agent",
                "role": NodeRole.AGENT,
            },
            format="json",
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertFalse(Node.objects.filter(name="manual-agent").exists())
        self.assertEqual(self.provider.calls, [])

    def test_enrolled_node_cannot_change_role_or_organization(self) -> None:
        node = Node.objects.create(
            organization=self.organization,
            name="enrolled-agent",
            role=NodeRole.AGENT,
        )
        other = Organization.objects.create(
            key="quota-node-other",
            name="Quota Node Other",
        )

        role_response = self.client.patch(
            f"/api/v1/node/nodes/{node.id}/",
            {"role": NodeRole.PROXY},
            format="json",
            HTTP_X_ORG_KEY=self.organization.key,
        )
        organization_response = self.client.patch(
            f"/api/v1/node/nodes/{node.id}/",
            {"organization": other.id},
            format="json",
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(role_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            organization_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        node.refresh_from_db()
        self.assertEqual(node.role, NodeRole.AGENT)
        self.assertEqual(node.organization_id, self.organization.id)

    def test_membership_create_is_blocked_before_persisting(self) -> None:
        user = get_user_model().objects.create_user(
            username="quota-new-member@test.local",
            email="quota-new-member@test.local",
            password="test-pass",
        )

        with self.assertRaises(AppError):
            create_org_membership(
                organization=self.organization,
                user=user,
                role=Membership.Role.ADMIN,
            )

        self.assertFalse(
            Membership.objects.filter(
                organization=self.organization,
                user=user,
            ).exists()
        )
        self.assert_last_check("max_users")

    def test_tenant_provision_counts_owner_and_rolls_back_organization(self) -> None:
        clear_providers_for_tests()
        provider = _BlockUsersProvider()
        register_quota_provider(provider)
        user = get_user_model().objects.create_user(
            username="quota-new-tenant@test.local",
            email="quota-new-tenant@test.local",
            password="test-pass",
        )

        with self.assertRaises(AppError):
            provision_registered_user_tenant(user)

        self.assertFalse(Membership.objects.filter(user=user).exists())
        self.assertFalse(
            Organization.objects.filter(key__startswith="quota-new-tenant").exists()
        )
        self.assertEqual(
            [resource for _, resource, _ in provider.calls],
            ["max_organizations", "max_users"],
        )

    def test_membership_reactivation_is_blocked_before_state_change(self) -> None:
        user = get_user_model().objects.create_user(
            username="quota-inactive-member@test.local",
            email="quota-inactive-member@test.local",
            password="test-pass",
        )
        membership = Membership.objects.create(
            user=user,
            organization=self.organization,
            role=Membership.Role.ADMIN,
            is_active=False,
        )

        with self.assertRaises(AppError):
            update_org_membership(membership, is_active=True)

        membership.refresh_from_db()
        self.assertFalse(membership.is_active)
        self.assert_last_check("max_users")

    def test_nas_source_create_is_blocked_before_persisting(self) -> None:
        with self.assertRaises(AppError):
            create_source_resource(
                organization=self.organization,
                user=self.owner,
                name="quota-nas",
                resource_type=ResourceType.NAS,
                config={
                    "protocol": "nfs",
                    "server": "192.0.2.10",
                    "export_path": "/source",
                },
            )

        self.assertFalse(
            SourceResource.objects.filter(
                organization=self.organization,
                name="quota-nas",
            ).exists()
        )
        self.assert_last_check("max_source_nas")

    def test_source_type_change_to_nas_is_blocked_before_persisting(self) -> None:
        resource = SourceResource.objects.create(
            organization=self.organization,
            name="quota-local-source",
            resource_type=ResourceType.LOCAL,
            config={},
        )

        with self.assertRaises(AppError):
            update_source_resource(
                resource=resource,
                user=self.owner,
                resource_type=ResourceType.NAS,
            )

        resource.refresh_from_db()
        self.assertEqual(resource.resource_type, ResourceType.LOCAL)
        self.assert_last_check("max_source_nas")

    def test_s3_repository_create_is_blocked_before_credentials_or_repository(
        self,
    ) -> None:
        with self.assertRaises(AppError):
            create_repository(
                organization_id=self.organization.id,
                name="quota-s3",
                repo_type=Repository.Type.S3,
                s3_platform=Repository.S3Platform.AWS,
                s3_bucket="quota-bucket",
                s3_bucket_mode=Repository.S3BucketMode.EXISTING,
                config={
                    "region": "us-east-1",
                    "endpoint": "https://s3.amazonaws.com",
                    "prefix": "kopia",
                    "access_key_id": "AKIA_TEST",
                },
                credential_payload={"secret_access_key": "secret"},
                requested_by=self.owner,
            )

        self.assertFalse(
            Repository.objects.filter(
                organization_id=self.organization.id,
                name="quota-s3",
            ).exists()
        )
        self.assertEqual(
            Credential.objects.filter(organization_id=self.organization.id).count(),
            0,
        )
        self.assert_last_check("max_object_storage")

    def test_alert_policy_duplicate_is_blocked_before_persisting(self) -> None:
        policy = AlertPolicy.objects.create(
            organization=self.organization,
            name="Quota alert",
            type=AlertType.METRIC,
            severity=AlertSeverity.WARNING,
            enabled=True,
            resource_type="system",
            scope="all",
            trigger_rule={"metric": "cpu_usage"},
        )

        response = self.client.post(
            f"/api/v1/alerts/policies/{policy.id}/duplicate/",
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            AlertPolicy.objects.filter(organization=self.organization).count(),
            1,
        )
        self.assert_last_check("max_alert_policies")
