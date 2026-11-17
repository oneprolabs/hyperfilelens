"""The organization-user usage service is not an OSS tenant API."""

from unittest import skipIf

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

from common.extension_loader import extensions_enabled


@skipIf(extensions_enabled(), "Enterprise installs intentionally expose user usage.")
class CommunityUserUsageBoundaryTests(SimpleTestCase):
    def test_enterprise_usage_endpoints_are_not_registered(self):
        for path in (
            "/api/v1/subscription/my-usage/",
            "/api/v1/subscription/member-usage/1/",
        ):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
