"""HTTP URL configuration (api process).

Composition-layer module: allowed to include business apps.
"""

from django.contrib import admin
from django.urls import include, path

from common.extension_loader import extensions_enabled
from common.http.schema import redoc_view, schema_view, swagger_view
from common.ops.health import liveness, readiness
from common.ops.metrics import metrics

from apps.iam.auth.views.oauth import GoogleOAuthCallbackView

urlpatterns = [
    # Health & metrics
    path("health/live", liveness, name="health-live"),
    path("health/ready", readiness, name="health-ready"),
    path("health", liveness, name="health"),
    path("metrics", metrics, name="metrics"),
    # API documentation
    path("api/schema", schema_view, name="schema"),
    path("swagger", swagger_view, name="swagger-ui"),
    path("redoc", redoc_view, name="redoc"),
    # Admin
    path("admin/", admin.site.urls),
    # Auth (IAM + allauth)
    path("", include("apps.iam.auth.urls")),
    path("accounts/oauth/callback/", GoogleOAuthCallbackView.as_view(), name="oauth_callback"),
    path("accounts/", include("allauth.urls")),
    path("_allauth/", include("allauth.headless.urls")),
    # REST API v1
    path("api/v1/iam/", include("apps.iam.urls")),
    path("api/v1/node/", include("apps.node.urls")),
    path("api/v1/storage/", include("apps.storage.urls")),
    path("api/v1/protection/", include("apps.protection.urls")),
    path("api/v1/restore/", include("apps.restore.urls")),
    path("api/v1/tasks/", include("apps.task.urls")),
    path("api/v1/alerts/", include("apps.alert.urls")),
    path("api/v1/audits/", include("apps.audit.urls")),
    path("api/v1/monitors/", include("apps.monitor.urls")),
    path("api/v1/notifications/", include("apps.notification.urls")),
    path("api/v1/insight/", include("apps.insight.urls")),
    path("api/v1/lens/", include("apps.lens_bridge.urls")),
    path("api/v1/subscription/", include("apps.subscription.urls")),
    path("api/v1/source/", include("apps.source.urls")),
    path("api/v1/configuration/", include("apps.configuration.urls")),
    # Deprecated alias for existing console paths (/api/v1/config/)
    path("api/v1/config/", include("apps.configuration.api.legacy_urls")),
    # Instance essential settings (OSS) — canonical + legacy Admin Console paths
    path("api/v1/instance-settings/", include("apps.instance_settings.api.urls")),
    path(
        "api/v1/platform-ops/platform/settings/",
        include("apps.instance_settings.api.urls"),
    ),
    path("api/v1/meta/", include("common.meta.urls")),
]

# Full Platform Ops console APIs — commercial / platform extension only
if extensions_enabled():
    urlpatterns.append(
        path("api/v1/platform-ops/", include("apps.platform_ops.api.urls")),
    )
else:
    # Community empty socket: Host still serves platform AI Models / settings.
    # When EE is loaded it mounts these same Host views (avoid a greedy lens/
    # include that would steal EE lens/gateways routes).
    urlpatterns.append(
        path(
            "api/v1/platform-ops/lens/",
            include("apps.instance_settings.api.lens_urls"),
        ),
    )
