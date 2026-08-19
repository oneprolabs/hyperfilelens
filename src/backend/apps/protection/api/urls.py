from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.protection.api.views import (
    BackupConfigViewSet,
    BackupSourceSnapshotViewSet,
    BackupTaskCancelView,
    BackupTaskRetryDirectoryView,
    BackupTaskRuntimeView,
    BackupTaskStartView,
    BackupTargetValidationView,
    BackupPolicyViewSet,
    FileFilterRuleViewSet,
    SnapshotDirectoryBatchDownloadTaskView,
    SnapshotDirectoryBrowseView,
    SnapshotDirectoryDownloadView,
    SnapshotDirectoryDownloadTaskView,
    SnapshotDownloadArtifactFileView,
    SnapshotDownloadArtifactContentView,
    SnapshotDownloadArtifactDownloadUrlView,
    health,
)

router = DefaultRouter()
router.register(r"policies", BackupPolicyViewSet, basename="protection-policy")
router.register(r"filters", FileFilterRuleViewSet, basename="protection-filter")
router.register(r"backup-configs", BackupConfigViewSet, basename="protection-backup-config")
router.register(r"backup-source-snapshots", BackupSourceSnapshotViewSet, basename="protection-backup-source-snapshot")

urlpatterns = [
    path("health", health, name="protection-health"),
    path("backup-tasks/", BackupTaskStartView.as_view(), name="protection-backup-task-start"),
    path(
        "backup-target-validations/",
        BackupTargetValidationView.as_view(),
        name="protection-backup-target-validation",
    ),
    path(
        "backup-tasks/<uuid:task_uuid>/cancel/",
        BackupTaskCancelView.as_view(),
        name="protection-backup-task-cancel",
    ),
    path(
        "backup-tasks/<uuid:task_uuid>/retry-directory/",
        BackupTaskRetryDirectoryView.as_view(),
        name="protection-backup-task-retry-directory",
    ),
    path(
        "backup-tasks/<uuid:task_uuid>/runtime/",
        BackupTaskRuntimeView.as_view(),
        name="protection-backup-task-runtime",
    ),
    path(
        "backup-source-snapshot-directories/<int:directory_id>/browse/",
        SnapshotDirectoryBrowseView.as_view(),
        name="protection-backup-source-snapshot-directory-browse",
    ),
    path(
        "backup-source-snapshot-directories/<int:directory_id>/download/",
        SnapshotDirectoryDownloadView.as_view(),
        name="protection-backup-source-snapshot-directory-download",
    ),
    path(
        "backup-source-snapshot-directories/<int:directory_id>/download-tasks/",
        SnapshotDirectoryDownloadTaskView.as_view(),
        name="protection-backup-source-snapshot-directory-download-task",
    ),
    path(
        "backup-source-snapshot-directories/<int:directory_id>/batch-download-tasks/",
        SnapshotDirectoryBatchDownloadTaskView.as_view(),
        name="protection-backup-source-snapshot-directory-batch-download-task",
    ),
    path(
        "snapshot-download-artifacts/<int:artifact_id>/content/",
        SnapshotDownloadArtifactContentView.as_view(),
        name="protection-snapshot-download-artifact-content",
    ),
    path(
        "snapshot-download-artifacts/<int:artifact_id>/download-url/",
        SnapshotDownloadArtifactDownloadUrlView.as_view(),
        name="protection-snapshot-download-artifact-download-url",
    ),
    path(
        "snapshot-download-artifacts/<int:artifact_id>/file/",
        SnapshotDownloadArtifactFileView.as_view(),
        name="protection-snapshot-download-artifact-file",
    ),
    path("", include(router.urls)),
]
