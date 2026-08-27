from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.restore.api.views import (
    RestorePlanViewSet,
    RestoreRecordViewSet,
    RestoreSnapshotDirectoryBrowseView,
    RestoreSnapshotDirectoryPathInfoView,
    RestoreTargetValidationView,
)
from apps.restore.api.views.restore_task import RestoreTaskCancelView

router = DefaultRouter()
router.register(r"plans", RestorePlanViewSet, basename="restore-plan")
router.register(r"records", RestoreRecordViewSet, basename="restore-record")

urlpatterns = [
    path(
        "target-validations/",
        RestoreTargetValidationView.as_view(),
        name="restore-target-validation",
    ),
    path(
        "tasks/<uuid:task_uuid>/cancel/",
        RestoreTaskCancelView.as_view(),
        name="restore-task-cancel",
    ),
    path(
        "snapshot-directories/<int:directory_id>/browse/",
        RestoreSnapshotDirectoryBrowseView.as_view(),
        name="restore-snapshot-directory-browse",
    ),
    path(
        "snapshot-directories/<int:directory_id>/path-info/",
        RestoreSnapshotDirectoryPathInfoView.as_view(),
        name="restore-snapshot-directory-path-info",
    ),
    path("", include(router.urls)),
]
