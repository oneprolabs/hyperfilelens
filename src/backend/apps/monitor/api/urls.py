from django.urls import path

from apps.monitor.api.views import AttentionView, EventView, SystemMonitorView
from apps.monitor.api.views.platform_monitor import PlatformMonitorView
from apps.monitor.api.views.resource_monitor import ResourceMonitorView
from common.extension_loader import extensions_enabled

urlpatterns = [
    path("system/", SystemMonitorView.as_view(), name="monitor-system"),
    path("events/", EventView.as_view(), name="monitor-events"),
    path("attention/", AttentionView.as_view(), name="monitor-attention"),
    path("resources/", ResourceMonitorView.as_view(), name="monitor-resources"),
    path("platform/", PlatformMonitorView.as_view(), name="monitor-platform"),
]

# Tenant node monitor read APIs are an EE product surface (Operations → Monitor).
# Host keeps metrics ingest; community builds do not expose these routes.
if extensions_enabled():
    from apps.platform_ops.api.tenant_node_monitor_urls import urlpatterns as _node_monitor_urls

    urlpatterns += _node_monitor_urls
