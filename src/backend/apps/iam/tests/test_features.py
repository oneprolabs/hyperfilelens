from django.test import SimpleTestCase

from apps.iam.features import FEATURE_DEFAULT_PATHS


class FeatureDefaultPathTests(SimpleTestCase):
    def test_operations_features_use_current_routes(self):
        self.assertEqual(FEATURE_DEFAULT_PATHS["task"], "/ops/tasks")
        self.assertEqual(FEATURE_DEFAULT_PATHS["alerts"], "/ops/alerts")
        self.assertEqual(FEATURE_DEFAULT_PATHS["audit"], "/ops/audit-logs")
