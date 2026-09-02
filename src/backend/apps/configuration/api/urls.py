"""Configuration API routes."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.configuration.api.views import GlobalConfigViewSet


router = DefaultRouter()
router.register(r"configs", GlobalConfigViewSet, basename="configuration-config")

urlpatterns = [
    path("", include(router.urls)),
]
