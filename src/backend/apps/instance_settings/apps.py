"""OSS instance-level settings and Admin Console essentials.

These stay in Community builds. Full Platform Ops console lives in EE.
API surface is stable under ``/api/v1/instance-settings/``; implementations
currently reuse platform_ops settings views until the view module is moved.
"""

from django.apps import AppConfig


class InstanceSettingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.instance_settings"
    label = "instance_settings"
    verbose_name = "Instance Settings"
