"""HTTP routes for instance-level settings (OSS).

Canonical: ``/api/v1/instance-settings/``
Legacy aliases remain under ``/api/v1/platform-ops/platform/settings/*``.
"""

from django.urls import path

from apps.instance_settings.api.views.settings import (
    PlatformOpsSettingsAiTestView,
    PlatformOpsSettingsAiView,
    PlatformOpsSettingsDefaultsView,
    PlatformOpsSettingsEmailTestView,
    PlatformOpsSettingsEmailView,
    PlatformOpsSettingsEnvironmentView,
    PlatformOpsSettingsExternalAccessView,
    PlatformOpsSettingsIdentityView,
)

urlpatterns = [
    path("email", PlatformOpsSettingsEmailView.as_view(), name="instance-settings-email"),
    path(
        "email/test",
        PlatformOpsSettingsEmailTestView.as_view(),
        name="instance-settings-email-test",
    ),
    path(
        "identity",
        PlatformOpsSettingsIdentityView.as_view(),
        name="instance-settings-identity",
    ),
    path("ai", PlatformOpsSettingsAiView.as_view(), name="instance-settings-ai"),
    path(
        "ai/test",
        PlatformOpsSettingsAiTestView.as_view(),
        name="instance-settings-ai-test",
    ),
    path(
        "external-access",
        PlatformOpsSettingsExternalAccessView.as_view(),
        name="instance-settings-external-access",
    ),
    path(
        "environment",
        PlatformOpsSettingsEnvironmentView.as_view(),
        name="instance-settings-environment",
    ),
    path(
        "defaults",
        PlatformOpsSettingsDefaultsView.as_view(),
        name="instance-settings-defaults",
    ),
]
