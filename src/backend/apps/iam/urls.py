from django.urls import include, path
from rest_framework.routers import DefaultRouter
from apps.iam.resource_capabilities import ResourceCapabilitiesView
from apps.iam.account_views import MyOrganizationsView, OrganizationCreationOptionsView, CreateOrganizationView

from apps.iam.views import (
    health,
    MembershipViewSet,
    OrganizationViewSet,
    PersonalApiKeyViewSet,
)


router = DefaultRouter()
router.register(r"orgs", OrganizationViewSet, basename="org")
router.register(r"memberships", MembershipViewSet, basename="membership")
router.register(r"api-keys", PersonalApiKeyViewSet, basename="api-key")

urlpatterns = [
    path("my-organizations/", MyOrganizationsView.as_view(), name="my-organizations"),
    path("organization-creation-options/", OrganizationCreationOptionsView.as_view(), name="organization-creation-options"),
    path("create-organization/", CreateOrganizationView.as_view(), name="create-organization"),
    path("resource-capabilities/", ResourceCapabilitiesView.as_view(), name="resource-capabilities"),
    path("health", health, name="iam-health"),
    path("", include(router.urls)),
]

