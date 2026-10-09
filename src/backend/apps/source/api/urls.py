from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.source.api.views import SourceResourceViewSet, health
from apps.source.api.views.source_tag import SourceTagListView, SourceTagDetailView, SourceTagAssignmentView, SourceTagAssignmentListView, SourceTagBulkAssignmentView, SourceTagSourceListView
from apps.source.api.views.backup_selectable import (
    BackupSelectableBulkDeleteView,
    BackupSelectableDeletePreflightView,
    BackupSelectableDirectoryView,
    BackupSelectableDirectoryCreateView,
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
    path("tags/", SourceTagListView.as_view(), name="source-tags"),
    path("tags/<int:pk>/", SourceTagDetailView.as_view(), name="source-tag-detail"),
    path("tags/<int:pk>/sources/", SourceTagSourceListView.as_view(), name="source-tag-sources"),
    path("tags/assignments/", SourceTagAssignmentListView.as_view(), name="source-tag-assignments"),
    path("tags/assignments/bulk/", SourceTagBulkAssignmentView.as_view(), name="source-tag-bulk-assignments"),
    path("tags/sources/<str:kind>/<int:ref_id>/", SourceTagAssignmentView.as_view(), name="source-tag-assignment"),
    path("backup-selectable/", BackupSelectableListView.as_view(), name="source-backup-selectable"),
    path("backup-selectable/directories/", BackupSelectableDirectoryView.as_view(), name="source-backup-selectable-directories"),
    path("backup-selectable/directories/create/", BackupSelectableDirectoryCreateView.as_view(), name="source-backup-selectable-directory-create"),
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
