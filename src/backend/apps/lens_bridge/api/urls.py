from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.lens_bridge.api.views import (
    LensAssistantViewSet,
    LensCopilotAssistantView,
    LensCopilotAdmissionPreviewView,
    LensCopilotBindingView,
    LensCopilotGatewayOptionsView,
    LensCopilotKnowledgeSourceView,
    LensCopilotReadinessView,
    LensCopilotRunStreamView,
    LensCopilotSharedQAFileView,
    LensCopilotSharedQAPdfView,
    LensCopilotSharedQAView,
    LensCopilotSessionViewSet,
    LensCopilotSnapshotBrowseTaskView,
    LensCopilotSnapshotBrowseView,
    LensCopilotScopePreviewTaskView,
    LensCopilotScopePreviewView,
    LensCopilotUsageView,
    LensGatewayViewSet,
    LensKnowledgeSourceViewSet,
    LensMcpServerViewSet,
    LensModelProxyView,
    LensOrgSettingsView,
    LensSkillViewSet,
    health,
)

router = DefaultRouter()
router.register(r"knowledge-sources", LensKnowledgeSourceViewSet, basename="lens-knowledge-source")
router.register(r"gateways", LensGatewayViewSet, basename="lens-gateway")
router.register(r"assistants", LensAssistantViewSet, basename="lens-assistant")
router.register(r"skills", LensSkillViewSet, basename="lens-skill")
router.register(r"mcp-servers", LensMcpServerViewSet, basename="lens-mcp-server")
router.register(r"copilot/sessions", LensCopilotSessionViewSet, basename="lens-copilot-session")

urlpatterns = [
    path("health", health, name="lens-bridge-health"),
    path("settings/", LensOrgSettingsView.as_view(), name="lens-org-settings"),
    path("models/", LensModelProxyView.as_view(), name="lens-models-list"),
    path("models/providers/", LensModelProxyView.as_view(), name="lens-models-providers"),
    path("models/catalog/", LensModelProxyView.as_view(), name="lens-models-catalog"),
    path("models/test/", LensModelProxyView.as_view(), name="lens-models-test"),
    path("models/<uuid:config_uuid>/", LensModelProxyView.as_view(), name="lens-models-detail"),
    path(
        "models/<uuid:config_uuid>/test-call/",
        LensModelProxyView.as_view(),
        name="lens-models-test-call",
    ),
    path("copilot/assistants/", LensCopilotAssistantView.as_view(), name="lens-copilot-assistants"),
    path("copilot/bindings/", LensCopilotBindingView.as_view(), name="lens-copilot-bindings"),
    path("copilot/gateway-options/", LensCopilotGatewayOptionsView.as_view(), name="lens-copilot-gateway-options"),
    path("copilot/knowledge-sources/", LensCopilotKnowledgeSourceView.as_view(), name="lens-copilot-ks"),
    path("copilot/readiness/", LensCopilotReadinessView.as_view(), name="lens-copilot-readiness"),
    path(
        "copilot/snapshot-browse/",
        LensCopilotSnapshotBrowseView.as_view(),
        name="lens-copilot-snapshot-browse",
    ),
    path(
        "copilot/snapshot-browse/<uuid:task_id>/",
        LensCopilotSnapshotBrowseTaskView.as_view(),
        name="lens-copilot-snapshot-browse-task",
    ),
    path(
        "copilot/selection-preview/",
        LensCopilotScopePreviewView.as_view(),
        name="lens-copilot-selection-preview",
    ),
    path(
        "copilot/selection-preview/<uuid:task_id>/",
        LensCopilotScopePreviewTaskView.as_view(),
        name="lens-copilot-selection-preview-task",
    ),
    path(
        "copilot/admission-preview/",
        LensCopilotAdmissionPreviewView.as_view(),
        name="lens-copilot-admission-preview",
    ),
    path("copilot/usage/", LensCopilotUsageView.as_view(), name="lens-copilot-usage"),
    path(
        "copilot/shared-qa/",
        LensCopilotSharedQAView.as_view(),
        name="lens-copilot-shared-qa",
    ),
    path(
        "copilot/shared-qa/pdf/",
        LensCopilotSharedQAPdfView.as_view(),
        name="lens-copilot-shared-qa-pdf",
    ),
    path(
        "copilot/shared-qa/files/<uuid:file_uuid>/",
        LensCopilotSharedQAFileView.as_view(),
        name="lens-copilot-shared-qa-file",
    ),
    path(
        "copilot/usage/<uuid:run_uuid>/",
        LensCopilotUsageView.as_view(),
        name="lens-copilot-usage-detail",
    ),
    path(
        "copilot/sessions/<int:session_id>/runs/<uuid:run_uuid>/stream/",
        LensCopilotRunStreamView.as_view(),
        name="lens-copilot-session-run-stream",
    ),
    path(
        "copilot/runs/<uuid:run_uuid>/stream/",
        LensCopilotRunStreamView.as_view(),
        name="lens-copilot-run-stream",
    ),
    path("", include(router.urls)),
]
