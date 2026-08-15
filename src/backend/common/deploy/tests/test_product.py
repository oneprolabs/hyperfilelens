"""Public product identity tests."""

from __future__ import annotations

import os
from unittest.mock import patch

from django.test import SimpleTestCase

from common.deploy.product import product_edition, product_version


class ProductIdentityTests(SimpleTestCase):
    def test_product_version_is_preferred_over_enterprise_image_version(self):
        with patch.dict(
            os.environ,
            {"HFL_PRODUCT_VERSION": "0.2.1", "APP_VERSION": "0.2.1-ee"},
            clear=True,
        ):
            self.assertEqual(product_version(), "0.2.1")

    def test_legacy_enterprise_image_suffix_is_not_public_product_identity(self):
        with patch.dict(os.environ, {"APP_VERSION": "0.2.1-ee"}, clear=True):
            self.assertEqual(product_version(), "0.2.1")

    def test_non_release_build_has_no_public_version(self):
        with patch.dict(os.environ, {"APP_VERSION": "main-abc1234"}, clear=True):
            self.assertIsNone(product_version())

    def test_configured_edition_is_normalized(self):
        with patch.dict(os.environ, {"HFL_EDITION": " Enterprise "}, clear=True):
            self.assertEqual(product_edition(), "enterprise")

    @patch("common.extension_loader.extensions_enabled", return_value=True)
    def test_source_build_infers_enterprise_from_loaded_extension(self, _extensions):
        with patch.dict(os.environ, {"HFL_EDITION": "community"}, clear=True):
            self.assertEqual(product_edition(), "enterprise")
