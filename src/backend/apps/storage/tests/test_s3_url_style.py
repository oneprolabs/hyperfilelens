from django.test import SimpleTestCase

from apps.storage.repositories.models import Repository
from apps.storage.services.internal.s3_url_style import (
    boto3_s3_addressing_style,
    default_s3_url_style,
    kopia_s3_url_style,
    normalize_s3_url_style,
)


class S3URLStyleTests(SimpleTestCase):
    def test_provider_defaults(self):
        self.assertEqual(default_s3_url_style(Repository.S3Platform.AWS), "auto")
        self.assertEqual(default_s3_url_style(Repository.S3Platform.ALIYUN), "auto")
        self.assertEqual(
            default_s3_url_style(Repository.S3Platform.HUAWEICLOUD),
            "virtual_hosted",
        )
        self.assertEqual(default_s3_url_style(Repository.S3Platform.MINIO), "path")
        self.assertEqual(default_s3_url_style(Repository.S3Platform.CUSTOM), "auto")

    def test_backend_and_kopia_mappings(self):
        self.assertEqual(boto3_s3_addressing_style("auto"), "auto")
        self.assertEqual(boto3_s3_addressing_style("virtual_hosted"), "virtual")
        self.assertEqual(boto3_s3_addressing_style("path"), "path")
        self.assertEqual(kopia_s3_url_style("auto"), "auto")
        self.assertEqual(kopia_s3_url_style("virtual_hosted"), "virtual-hosted")
        self.assertEqual(kopia_s3_url_style("path"), "path")

    def test_rejects_unknown_style(self):
        with self.assertRaisesMessage(ValueError, "must be one of"):
            normalize_s3_url_style("dns")
