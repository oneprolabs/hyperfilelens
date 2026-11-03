from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.alert.api.views import (
    AlertPolicyViewSet,
    AlertRecordViewSet,
    MetadataResourcesView,
    MetadataView,
)
from apps.alert.api.views.platform_defaults import (
    PlatformDefaultAlertRulesPreviewView,
    PlatformDefaultAlertRulesRestoreView,
)

router = DefaultRouter()
router.register(r"policies", AlertPolicyViewSet, basename="alert-policy")
router.register(r"records", AlertRecordViewSet, basename="alert-record")

urlpatterns = [
    path("metadata/<str:kind>/", MetadataView.as_view(), name="alert-metadata"),
    path("metadata/resources/", MetadataResourcesView.as_view(), name="alert-metadata-resources"),
    path(
        "platform-defaults/restore-preview/",
        PlatformDefaultAlertRulesPreviewView.as_view(),
        name="platform-default-alert-rules-restore-preview",
    ),
    path(
        "platform-defaults/restore/",
        PlatformDefaultAlertRulesRestoreView.as_view(),
        name="platform-default-alert-rules-restore",
    ),
    path("", include(router.urls)),
]
