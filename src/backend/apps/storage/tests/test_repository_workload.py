from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.storage.repositories.models import (
    Repository,
    RepositoryLocationClaim,
    RepositoryLocationNamespace,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_ownership_verified,
)
from apps.storage.services.internal.repository_workload import (
    RepositoryWorkload,
    lock_repositories_for_workload,
)


class RepositoryLegacyWorkloadTests(TestCase):
    def setUp(self):
        self.repository = Repository.objects.create(
            organization_id=1,
            name="Migrated repository",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="migrated-bucket",
            config={"endpoint": "s3.example.test", "prefix": "hfl"},
        )
        namespace = RepositoryLocationNamespace.objects.create(
            namespace_key="legacy-workload-namespace",
            kind=RepositoryLocationNamespace.Kind.S3,
            display_hint="s3.example.test/migrated-bucket",
        )
        self.claim = RepositoryLocationClaim.objects.create(
            organization_id=1,
            repository=self.repository,
            namespace=namespace,
            root_path="hfl",
            state=RepositoryLocationClaim.State.OWNED,
            legacy_adoption_required=True,
        )

    def test_legacy_repository_allows_non_destructive_workloads(self):
        for workload in (
            RepositoryWorkload.BACKUP_WRITE,
            RepositoryWorkload.RESTORE_READ,
        ):
            with self.subTest(workload=workload):
                locked = lock_repositories_for_workload(
                    organization_id=1,
                    repository_ids=[self.repository.id],
                    workload=workload,
                )
                self.assertEqual([self.repository.id], [item.id for item in locked])

    def test_legacy_repository_blocks_snapshot_delete_until_verified(self):
        with self.assertRaises(ValidationError):
            lock_repositories_for_workload(
                organization_id=1,
                repository_ids=[self.repository.id],
                workload=RepositoryWorkload.SNAPSHOT_DELETE,
            )

        mark_repository_location_ownership_verified(self.repository)
        locked = lock_repositories_for_workload(
            organization_id=1,
            repository_ids=[self.repository.id],
            workload=RepositoryWorkload.SNAPSHOT_DELETE,
        )
        self.assertEqual([self.repository.id], [item.id for item in locked])
