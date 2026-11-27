from apps.iam.auth.views.impersonation import ImpersonationControlView
from django.urls import path
from rest_framework.permissions import AllowAny

from dj_rest_auth.views import LoginView

from apps.iam.auth.views import (
    ChangePasswordView,
    CustomUserDetailsView,
    EmailLoginView,
    EmailCodeLoginSendView,
    EmailCodeLoginVerifyView,
    EmailRegisterView,
    EmailRegisterConfirmView,
    EmailRegisterSendCodeView,
    ForgotPasswordView,
    ForgotPasswordConfirmView,
    GetAvailableScenesView,
    GoogleOAuthConfigView,
    GoogleOAuthErrorEventConsumeView,
    OrgSelectView,
    TokenRefreshView,
    TurnstileConfigView,
    LogoutView,
)


class CustomLoginView(LoginView):
    permission_classes = [AllowAny]


urlpatterns = [
    path("api/v1/impersonation/exchange", ImpersonationControlView.as_view(operation="exchange")),
    path("api/v1/impersonation/session", ImpersonationControlView.as_view()),
    path("api/v1/auth/turnstile/config", TurnstileConfigView.as_view(), name="turnstile_config"),
    path("api/v1/auth/google/config", GoogleOAuthConfigView.as_view(), name="google_oauth_config"),
    path(
        "api/v1/auth/google/error-events/consume",
        GoogleOAuthErrorEventConsumeView.as_view(),
        name="google_oauth_error_event_consume",
    ),
    path("api/v1/auth/email-login", EmailLoginView.as_view(), name="email_login"),
    path(
        "api/v1/auth/email-code-login/send-code",
        EmailCodeLoginSendView.as_view(),
        name="email_code_login_send",
    ),
    path(
        "api/v1/auth/email-code-login/verify",
        EmailCodeLoginVerifyView.as_view(),
        name="email_code_login_verify",
    ),
    path("api/v1/auth/org-select", OrgSelectView.as_view(), name="org_select"),
    path("api/v1/auth/email-register/send-code", EmailRegisterSendCodeView.as_view(), name="email_register_send_code"),
    path("api/v1/auth/email-register", EmailRegisterView.as_view(), name="email_register"),
    path("api/v1/auth/email-register/confirm", EmailRegisterConfirmView.as_view(), name="email_register_confirm"),
    path("api/v1/auth/forgot-password", ForgotPasswordView.as_view(), name="forgot_password"),
    path("api/v1/auth/forgot-password/confirm", ForgotPasswordConfirmView.as_view(), name="forgot_password_confirm"),
    path("api/v1/auth/change-password", ChangePasswordView.as_view(), name="change_password"),
    path("api/v1/auth/login", CustomLoginView.as_view(), name="rest_login"),
    path("api/v1/auth/logout", LogoutView.as_view(), name="rest_logout"),
    path(
        "api/v1/auth/token/refresh",
        TokenRefreshView.as_view(),
        name="token_refresh",
    ),
    path(
        "api/v1/auth/user",
        CustomUserDetailsView.as_view(),
        name="rest_user_details",
    ),
    path(
        "api/v1/auth/scenes",
        GetAvailableScenesView.as_view(),
        name="available_scenes",
    ),
]
