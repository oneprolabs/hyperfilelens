"""Tests for configuration resolution and cache invalidation."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from apps.configuration.constants import CACHE_VERSION_KEY
from apps.configuration.models import GlobalConfig
from apps.configuration.selectors.interface import (
    get_config,
    get_config_source,
    has_runtime_config,
)
from apps.configuration.selectors.internal.cache import bump_cache_version
from apps.iam import conf as iam_conf
from apps.instance_settings.conf import CONFIG_KEY_EXTERNAL_ACCESS_URL


class GetConfigCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        cache.set(CACHE_VERSION_KEY, 1, timeout=None)

    def test_tenant_miss_uses_global_without_tenant_cache_pollution(self):
        GlobalConfig.objects.create(
            key="test.feature",
            scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="",
            value_type=GlobalConfig.ValueType.NUMBER,
            value=7,
        )

        first = get_config("test.feature", tenant_key="acme", use_cache=True)
        self.assertEqual(first, 7)

        GlobalConfig.objects.filter(
            key="test.feature",
            scope=GlobalConfig.Scope.GLOBAL,
        ).update(
            value=14,
        )
        bump_cache_version()

        second = get_config("test.feature", tenant_key="acme", use_cache=True)
        self.assertEqual(second, 14)

    def test_defaults_are_not_cached(self):
        GlobalConfig.objects.filter(key="test.missing").delete()
        value = get_config("test.missing", default=99, use_cache=True)
        self.assertEqual(value, 99)

        GlobalConfig.objects.create(
            key="test.missing",
            scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="",
            value_type=GlobalConfig.ValueType.NUMBER,
            value=1,
        )
        bump_cache_version()

        updated = get_config("test.missing", default=99, use_cache=True)
        self.assertEqual(updated, 1)


class GetConfigLayerTests(TestCase):
    def setUp(self):
        cache.clear()
        GlobalConfig.objects.filter(
            key__in=(
                iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES,
                CONFIG_KEY_EXTERNAL_ACCESS_URL,
            )
        ).delete()

    def test_deployment_beats_default_for_registered_key(self):
        with patch.dict(
            "os.environ",
            {"HFL_IAM_LOGIN_CODE_MINUTES": "42"},
            clear=False,
        ):
            value = get_config(
                iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES,
                default=iam_conf.DEFAULT_LOGIN_VERIFICATION_CODE_MINUTES,
                use_cache=False,
            )
            self.assertEqual(value, 42)
            self.assertEqual(
                get_config_source(iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES),
                "deployment",
            )

    def test_runtime_beats_deployment_including_explicit_empty(self):
        with patch.dict(
            "os.environ",
            {"HFL_EXTERNAL_ACCESS_URL": "https://deploy.example.com"},
            clear=False,
        ):
            self.assertEqual(
                get_config(CONFIG_KEY_EXTERNAL_ACCESS_URL, default="", use_cache=False),
                "https://deploy.example.com",
            )

            GlobalConfig.objects.create(
                key=CONFIG_KEY_EXTERNAL_ACCESS_URL,
                scope=GlobalConfig.Scope.GLOBAL,
                tenant_key="",
                value="",
                value_type=GlobalConfig.ValueType.STRING,
                is_active=True,
            )
            self.assertTrue(has_runtime_config(CONFIG_KEY_EXTERNAL_ACCESS_URL))
            self.assertEqual(
                get_config(CONFIG_KEY_EXTERNAL_ACCESS_URL, default="x", use_cache=False),
                "",
            )
            self.assertEqual(
                get_config_source(CONFIG_KEY_EXTERNAL_ACCESS_URL),
                "runtime",
            )

    def test_inactive_placeholder_is_not_runtime(self):
        GlobalConfig.objects.create(
            key=iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES,
            scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="",
            value=0,
            value_type=GlobalConfig.ValueType.NUMBER,
            is_active=False,
        )
        self.assertFalse(has_runtime_config(iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES))
        self.assertEqual(
            get_config(
                iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES,
                default=iam_conf.DEFAULT_LOGIN_VERIFICATION_CODE_MINUTES,
                use_cache=False,
            ),
            iam_conf.DEFAULT_LOGIN_VERIFICATION_CODE_MINUTES,
        )
        self.assertEqual(
            get_config_source(iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES),
            "default",
        )
