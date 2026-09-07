"""Host-only tests for tenant node-monitor URL gating.

Both branches run without a real platform extension: the enabled path injects a
stub URL fragment so OSS CI does not depend on hyperfilelens-ee.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

_FRAGMENT = "apps.platform_ops.api.tenant_node_monitor_urls"
_API_PKG = "apps.platform_ops.api"


def _route_text(urlpatterns) -> str:
    return " ".join(str(pattern.pattern) for pattern in urlpatterns)


class MonitorUrlGatingTests(SimpleTestCase):
    def tearDown(self):
        sys.modules.pop(_FRAGMENT, None)
        # Drop injected api package only if we created a stub without a real path.
        api_mod = sys.modules.get(_API_PKG)
        if api_mod is not None and getattr(api_mod, "__file__", None) is None:
            sys.modules.pop(_API_PKG, None)
        import apps.monitor.api.urls as monitor_urls

        importlib.reload(monitor_urls)

    def test_nodes_routes_absent_when_extensions_disabled(self):
        with patch("common.extension_loader.extensions_enabled", return_value=False):
            import apps.monitor.api.urls as monitor_urls

            importlib.reload(monitor_urls)
            text = _route_text(monitor_urls.urlpatterns)
            self.assertNotIn("nodes/", text)
            self.assertIn("system/", text)
            self.assertIn("events/", text)

    def test_nodes_routes_present_when_extensions_enabled(self):
        class _StubView(APIView):
            def get(self, request):
                return Response({})

        if _API_PKG not in sys.modules:
            api_pkg = ModuleType(_API_PKG)
            api_pkg.__path__ = []  # type: ignore[attr-defined]
            sys.modules[_API_PKG] = api_pkg

        fragment = ModuleType(_FRAGMENT)
        fragment.urlpatterns = [
            path("nodes/", _StubView.as_view(), name="monitor-nodes"),
            path("nodes/<int:node_id>/", _StubView.as_view(), name="monitor-node-detail"),
        ]
        sys.modules[_FRAGMENT] = fragment

        with patch("common.extension_loader.extensions_enabled", return_value=True):
            import apps.monitor.api.urls as monitor_urls

            importlib.reload(monitor_urls)
            text = _route_text(monitor_urls.urlpatterns)
            self.assertIn("nodes/", text)
            self.assertIn("nodes/<int:node_id>/", text)
