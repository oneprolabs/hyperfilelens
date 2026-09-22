"""Tests for PlatformRuntimeSetting R > D > Def resolve helpers."""

from __future__ import annotations

import os
from unittest.mock import patch

from django.test import TestCase

from apps.configuration.models import PlatformRuntimeSetting
from apps.configuration.services import runtime_settings as rs
from apps.insight import config as insight_config
from apps.insight import conf as insight_conf


class RuntimeSettingsResolveTests(TestCase):
    def tearDown(self):
        PlatformRuntimeSetting.objects.all().delete()

    def test_get_source_empty_env_is_not_deployment(self):
        with patch.dict(os.environ, {"EMAIL_HOST": ""}, clear=False):
            self.assertEqual(
                rs.get_source(rs.KEY_EMAIL_HOST, env_name="EMAIL_HOST"),
                "default",
            )

    def test_get_source_nonempty_env_is_deployment(self):
        with patch.dict(os.environ, {"EMAIL_HOST": "smtp.example.com"}, clear=False):
            self.assertEqual(
                rs.get_source(rs.KEY_EMAIL_HOST, env_name="EMAIL_HOST"),
                "deployment",
            )

    def test_langfuse_enabled_runtime_false_beats_deployment_env(self):
        PlatformRuntimeSetting.objects.create(
            key=rs.KEY_AI_LANGFUSE_ENABLED,
            value_text="false",
        )
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "true"}, clear=False):
            self.assertFalse(rs.langfuse_enabled())

    def test_langfuse_enabled_falls_to_deployment_when_unset(self):
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "true"}, clear=False):
            self.assertTrue(rs.langfuse_enabled())

    def test_insight_langfuse_enabled_uses_prs_not_gc_deployment_env(self):
        """Console AI writes PRS; LANGFUSE_ENABLED must not win via GC Deployment."""
        PlatformRuntimeSetting.objects.create(
            key=rs.KEY_AI_LANGFUSE_ENABLED,
            value_text="false",
        )
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "true"}, clear=False):
            settings = insight_config.get_langfuse_settings()
            self.assertFalse(settings["enabled"])

    def test_insight_langfuse_gc_runtime_override_beats_prs(self):
        from apps.configuration.models import GlobalConfig

        PlatformRuntimeSetting.objects.create(
            key=rs.KEY_AI_LANGFUSE_ENABLED,
            value_text="false",
        )
        GlobalConfig.objects.create(
            key=insight_conf.CONFIG_KEY_LANGFUSE_ENABLED,
            scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="",
            value_type=GlobalConfig.ValueType.BOOLEAN,
            value=True,
            is_active=True,
        )
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "false"}, clear=False):
            settings = insight_config.get_langfuse_settings()
            self.assertTrue(settings["enabled"])
