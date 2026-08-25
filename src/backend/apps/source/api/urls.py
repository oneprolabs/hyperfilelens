from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.source.api.views import SourceResourceViewSet, health
from apps.source.api.views.backup_selectable import (
    BackupSelectableBulkDeleteView,
    BackupSelectableDeletePreflightView,
    BackupSelectableDirectoryView,
    BackupSelectableFileCaptureView,
    BackupSelectableListView,
    BackupSelectablePathInfoView,
    BackupSelectablePipelineRevertView,
    BackupSelectablePipelineView,
    BackupSelectableRevertPreflightView,
)

router = DefaultRouter()
router.register(r"resources", SourceResourceViewSet, basename="source-resource")

urlpatterns = [
    path("health", health, name="source-health"),
    path("backup-selectable/", BackupSelectableListView.as_view(), name="source-backup-selectable"),
    path("backup-selectable/directories/", BackupSelectableDirectoryView.as_view(), name="source-backup-selectable-directories"),
    path("backup-selectable/file-capture/", BackupSelectableFileCaptureView.as_view(), name="source-backup-selectable-file-capture"),
    path("backup-selectable/path-info/", BackupSelectablePathInfoView.as_view(), name="source-backup-selectable-path-info"),
    path("backup-selectable/pipeline/", BackupSelectablePipelineView.as_view(), name="source-backup-selectable-pipeline"),
    path(
        "backup-selectable/pipeline/revert/",
        BackupSelectablePipelineRevertView.as_view(),
        name="source-backup-selectable-pipeline-revert",
    ),
    path(
        "backup-selectable/delete-preflight/",
        BackupSelectableDeletePreflightView.as_view(),
        name="source-backup-selectable-delete-preflight",
    ),
    path(
        "backup-selectable/bulk-delete/",
        BackupSelectableBulkDeleteView.as_view(),
        name="source-backup-selectable-bulk-delete",
    ),
    path(
        "backup-selectable/revert-preflight/",
        BackupSelectableRevertPreflightView.as_view(),
        name="source-backup-selectable-revert-preflight",
    ),
    path("", include(router.urls)),
]
