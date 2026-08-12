from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.notification.api.views import (
    NotificationChannelViewSet,
    NotificationLogViewSet,
    UserNotificationInboxView,
    UserNotificationMarkAllReadView,
    UserNotificationReadView,
)
from apps.notification.api.views.health import health


router = DefaultRouter()
router.register(r"channels", NotificationChannelViewSet, basename="notification-channel")
router.register(r"logs", NotificationLogViewSet, basename="notification-log")

urlpatterns = [
    path("health", health, name="notification-health"),
    path("inbox/", UserNotificationInboxView.as_view(), name="notification-inbox"),
    path(
        "inbox/mark-all-read/",
        UserNotificationMarkAllReadView.as_view(),
        name="notification-mark-all-read",
    ),
    path(
        "inbox/<int:notification_id>/read/",
        UserNotificationReadView.as_view(),
        name="notification-read",
    ),
    path("", include(router.urls)),
]
