from django.urls import path

from common.meta.views import DeployProfileView, RegistrationOptionsView

urlpatterns = [
    path(
        "deploy-profile",
        DeployProfileView.as_view(),
        name="meta-deploy-profile",
    ),
    path(
        "registration-options",
        RegistrationOptionsView.as_view(),
        name="meta-registration-options",
    ),
]
