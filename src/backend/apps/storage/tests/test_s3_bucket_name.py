from django.test import SimpleTestCase

from apps.storage.services.internal.s3_bucket_name import s3_bucket_name_error


class S3BucketNameTests(SimpleTestCase):
    def test_aws_and_huawei_use_dns_style_rules(self):
        self.assertEqual(s3_bucket_name_error(platform="aws", bucket="team.backup-1"), "")
        self.assertTrue(
            s3_bucket_name_error(platform="huaweicloud", bucket="team.-backup")
        )
        self.assertTrue(
            s3_bucket_name_error(platform="aws", bucket="192.168.1.1")
        )

    def test_aliyun_rejects_periods(self):
        self.assertTrue(s3_bucket_name_error(platform="aliyun", bucket="team.backup"))
        self.assertEqual(
            s3_bucket_name_error(platform="aliyun", bucket="team-backup"),
            "",
        )

    def test_custom_provider_remains_provider_authoritative(self):
        self.assertEqual(
            s3_bucket_name_error(platform="custom", bucket="Legacy_Bucket"),
            "",
        )

    def test_aws_reserved_names(self):
        for name in ("xn--bucket", "sthree-bucket", "amzn-s3-demo-bucket", "bucket-s3alias",
                     "bucket--ol-s3", "bucket.mrap", "bucket--x-s3", "bucket--table-s3"):
            with self.subTest(name=name):
                self.assertIn("reserved", s3_bucket_name_error(platform="aws", bucket=name))
                self.assertEqual(s3_bucket_name_error(platform="custom", bucket=name), "")

    def test_generated_name_is_valid_for_managed_providers(self):
        for platform in ("aws", "aliyun", "huaweicloud"):
            self.assertEqual(s3_bucket_name_error(platform=platform, bucket="hfl-20260909080305"), "")
