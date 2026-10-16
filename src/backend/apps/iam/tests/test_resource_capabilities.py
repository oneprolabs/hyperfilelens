"""Community capability tests must not import Enterprise packages."""

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.storage.repositories.models import Repository, RepositoryLocationClaim
from apps.storage.services.internal.repository_location import reserve_repository_location
from common.extension_spi import clear_providers_for_tests, restore_providers_for_tests


class CommunityResourceCapabilitiesTests(APITestCase):
    def setUp(self):
        super().setUp()
        previous = clear_providers_for_tests()
        self.addCleanup(restore_providers_for_tests, previous)
        self.org = Organization.objects.create(key="community-capabilities", name="Community")
        self.user = get_user_model().objects.create_user(username="community-capabilities")
        Membership.objects.create(user=self.user, organization=self.org)
        self.node = Node.objects.create(organization=self.org, name="Community Host", role="agent")
        self.client.force_authenticate(user=self.user)
        self.headers = {"HTTP_X_ORG_KEY": self.org.key}

    def test_community_capabilities_keep_existing_org_authority_without_ee_imports(self):
        response = self.client.post("/api/v1/iam/resource-capabilities/", {
            "resources": [{"resource_type": "node", "resource_id": self.node.id}],
        }, format="json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["resources"][0]["view"])
        self.assertTrue(response.data["resources"][0]["manage"])
        self.assertTrue(response.data["create"]["backup_config"])

    def test_foreign_and_invalid_ids_are_not_reported_as_accessible(self):
        other = Organization.objects.create(key="community-capabilities-other", name="Other")
        foreign = Node.objects.create(organization=other, name="Foreign", role="agent")
        response = self.client.post("/api/v1/iam/resource-capabilities/", {
            "resources": [{"resource_type": "node", "resource_id": foreign.id}],
        }, format="json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["resources"][0]["view"])
        response = self.client.post("/api/v1/iam/resource-capabilities/", {
            "resources": [{"resource_type": "node", "resource_id": str(2**63)}],
        }, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_only_local_residual_tombstones_keep_repository_capabilities(self):
        foreign_org = Organization.objects.create(key="foreign-residue", name="Foreign")
        repositories = []
        for organization, prefix, state in (
            (self.org, "residual", RepositoryLocationClaim.State.RESIDUAL),
            (self.org, "released", RepositoryLocationClaim.State.RELEASED),
            (foreign_org, "foreign", RepositoryLocationClaim.State.RESIDUAL),
        ):
            repository = Repository.objects.create(
                organization_id=organization.id,
                name=prefix,
                repo_type=Repository.Type.S3,
                status=Repository.Status.REMOVED,
                s3_bucket="capabilities-bucket",
                config={"endpoint": "s3.amazonaws.com", "prefix": prefix},
            )
            claim = reserve_repository_location(repository)
            claim.state = state
            claim.save()
            repositories.append(repository)

        response = self.client.post("/api/v1/iam/resource-capabilities/", {
            "resources": [
                {"resource_type": "repository", "resource_id": row.id}
                for row in repositories
            ],
        }, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        for index, row in enumerate(response.data["resources"]):
            self.assertEqual(row["view"], index == 0)
            self.assertEqual(row["delete"], index == 0)
            self.assertEqual(row["delete_data"], index == 0)
