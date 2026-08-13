"""Health endpoint product identity tests."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from apps.monitor.services.internal.deployment_host import current_host_identity
from common.ops.health import CheckResult, readiness


class ReadinessVersionTests(SimpleTestCase):
    def test_readiness_prefers_product_version_over_image_version(self):
        with (
            patch.dict(
                os.environ,
                {"HFL_PRODUCT_VERSION": "1.2.3", "APP_VERSION": "1.2.3-ee"},
                clear=False,
            ),
            patch("common.ops.health._check_db", return_value=CheckResult("database", True)),
            patch("common.ops.health._check_cache", return_value=CheckResult("cache", True)),
        ):
            response = readiness(RequestFactory().get("/health/ready"))

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["version"], "1.2.3")

    def test_host_identity_prefers_product_version_over_image_version(self):
        with patch.dict(
            os.environ,
            {"HFL_PRODUCT_VERSION": "1.2.3", "APP_VERSION": "1.2.3-ee"},
            clear=False,
        ), patch(
            "apps.monitor.services.internal.deployment_host._primary_ip",
            return_value=None,
        ):
            identity = current_host_identity()

        self.assertEqual(identity["app_version"], "1.2.3")
