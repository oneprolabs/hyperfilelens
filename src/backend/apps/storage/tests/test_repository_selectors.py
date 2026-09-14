from django.test import TestCase

from apps.iam.models import Organization
from apps.storage.repositories.models import Repository
from apps.storage.selectors.interface import list_repositories


class RepositorySelectorTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="selector-org", name="Selector Org")

    def test_default_list_includes_all_repository_statuses(self):
        Repository.objects.create(
            organization_id=self.org.id,
            name="created-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={},
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="ok",
        )
        Repository.objects.create(
            organization_id=self.org.id,
            name="failed-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATE_FAILED,
            health=Repository.Health.OFFLINE,
            config={},
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="bad",
        )
        Repository.objects.create(
            organization_id=self.org.id,
            name="removed-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.REMOVED,
            health=Repository.Health.OFFLINE,
            config={},
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="gone",
        )

        names = list(
            list_repositories(organization_id=self.org.id, repo_type=Repository.Type.S3).values_list(
                "name",
                flat=True,
            )
        )
        self.assertEqual(names, ["removed-repo", "failed-repo", "created-repo"])

    def test_status_filter_can_narrow_results(self):
        Repository.objects.create(
            organization_id=self.org.id,
            name="failed-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATE_FAILED,
            health=Repository.Health.OFFLINE,
            config={},
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="bad",
        )

        names = list(
            list_repositories(
                organization_id=self.org.id,
                repo_type=Repository.Type.S3,
                status=Repository.Status.CREATE_FAILED,
            ).values_list("name", flat=True)
        )
        self.assertEqual(names, ["failed-repo"])

    def test_newest_first_with_stable_tie_breaker_before_pagination(self):
        from datetime import timedelta
        from django.utils import timezone
        oldest = Repository.objects.create(organization_id=self.org.id, name="A", repo_type="s3")
        second = Repository.objects.create(organization_id=self.org.id, name="Z", repo_type="nas")
        newest = Repository.objects.create(organization_id=self.org.id, name="B", repo_type="s3")
        now = timezone.now()
        Repository.objects.filter(pk=oldest.pk).update(created_at=now - timedelta(days=1))
        Repository.objects.filter(pk__in=[second.pk, newest.pk]).update(created_at=now)
        rows = list_repositories(organization_id=self.org.id)
        self.assertEqual(list(rows.values_list("id", flat=True)[:2]), [newest.id, second.id])
        self.assertEqual(list(rows.values_list("id", flat=True)[2:]), [oldest.id])
        self.assertEqual(list(list_repositories(organization_id=self.org.id, repo_type="s3").values_list("id", flat=True)), [newest.id, oldest.id])
