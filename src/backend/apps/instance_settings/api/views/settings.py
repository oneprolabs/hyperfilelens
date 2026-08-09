"""Instance-level settings APIs (OSS essentials: email / identity / AI / environment)."""

from __future__ import annotations

import logging

from django.core.mail import EmailMessage, get_connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.configuration.models import GlobalConfig
from apps.configuration.selectors.interface import get_config, invalidate_config_cache
from apps.configuration.services.internal.registry import registry_by_key
from apps.configuration.services.internal.validation import validate_config_key
from apps.iam import conf as iam_conf
from apps.iam.config import (
    get_login_verification_code_minutes,
    get_password_reset_timeout_seconds,
    get_password_reset_verification_code_minutes,
    get_registration_token_expiry_hours,
    get_registration_verification_code_minutes,
)
from apps.insight import conf as insight_conf
from apps.instance_settings.permissions import HasPlatformPermission
from common.platform_audit import write_platform_audit_log
from common.platform_authz import ADMIN_USERS_MANAGE, INFRA_AI_MODELS_MANAGE
from apps.configuration.services import runtime_settings as runtime_settings_svc
from apps.configuration.services.runtime_settings import (
    KEY_AI_AZURE_BASE,
    KEY_AI_LANGFUSE_BASE,
    KEY_AI_LANGFUSE_ENABLED,
    KEY_AI_OPENAI_BASE,
    KEY_EMAIL_BACKEND,
    KEY_EMAIL_FROM,
    KEY_EMAIL_HOST,
    KEY_EMAIL_HOST_USER,
    KEY_EMAIL_PORT,
    KEY_EMAIL_USE_SSL,
    KEY_EMAIL_USE_TLS,
    KEY_IDENTITY_EMAIL_SIGNUP,
    KEY_IDENTITY_EMAIL_CODE_LOGIN,
    KEY_IDENTITY_GOOGLE_CLIENT_ID,
    KEY_IDENTITY_GOOGLE_OAUTH,
    KEY_IDENTITY_OPS_CIDRS,
    KEY_IDENTITY_PLATFORM_OPS,
    KEY_IDENTITY_TURNSTILE_SITE,
    SECRET_KEY_AZURE,
    SECRET_KEY_EMAIL_PASSWORD,
    SECRET_KEY_GEMINI,
    SECRET_KEY_GOOGLE,
    SECRET_KEY_LANGFUSE_PUBLIC,
    SECRET_KEY_LANGFUSE_SECRET,
    SECRET_KEY_OPENAI,
    SECRET_KEY_TURNSTILE,
    email_code_login_enabled,
    email_signup_enabled,
    password_reset_available,
    email_connection_kwargs,
    email_settings_managed_by_deployment,
    gemini_api_key,
    get_source,
    google_client_id,
    google_oauth_enabled,
    mask_secret,
    openai_api_base,
    openai_api_key,
    platform_ops_allowed_cidrs,
    platform_ops_enabled,
    secret_configured,
    set_bool,
    set_str_list,
    set_value,
    sync_google_social_app,
    turnstile_site_key,
    turnstile_enabled,
    validate_email_connection_config,
)
from apps.configuration.services.runtime_settings import (
    azure_openai_api_base as runtime_azure_base,
)
from apps.configuration.services.runtime_settings import (
    azure_openai_api_key as runtime_azure_key,
)
from apps.configuration.services.runtime_settings import (
    langfuse_base_url,
    langfuse_enabled,
    langfuse_public_key,
    langfuse_secret_key,
)
from apps.configuration.tenant_conf import CONFIG_KEY_DR_TASK_CONCURRENCY, DEFAULT_DR_TASK_CONCURRENCY
from apps.storage import conf as storage_conf
from common.deploy.site import tenant_public_url

logger = logging.getLogger(__name__)


def _audit(request, action: str, details: dict | None = None) -> None:
    write_platform_audit_log(
        request=request,
        action=action,
        target_type="platform_settings",
        target_id=action,
        details=details or {},
    )


def _google_redirect_uri() -> str:
    return f"{tenant_public_url()}/accounts/google/login/callback/"


class PlatformOpsSettingsEmailView(APIView):
    permission_classes = [HasPlatformPermission.for_actions(ADMIN_USERS_MANAGE)]

    def get(self, request):
        cfg = email_connection_kwargs()
        return Response(
            {
                "enterprise_identity_enabled": (
                    runtime_settings_svc.enterprise_identity_enabled()
                ),
                "backend": cfg["backend"],
                "host": cfg["host"],
                "port": cfg["port"],
                "use_tls": cfg["use_tls"],
                "use_ssl": cfg["use_ssl"],
                "host_user": cfg["username"],
                "password_configured": bool(cfg["password"]),
                "password_hint": mask_secret(cfg["password"]) if cfg["password"] else "",
                "from_email": cfg["from_email"],
                "delivery_configured": not bool(cfg["configuration_error"]),
                "configuration_error": cfg["configuration_error"],
                "managed_by_deployment": cfg["managed_by_deployment"],
                "source": cfg["source"],
                "sources": {
                    "host": cfg["source"],
                    "from_email": cfg["source"],
                },
            }
        )

    def patch(self, request):
        # SMTP console is an enterprise identity surface (signup / reset / codes).
        if not runtime_settings_svc.enterprise_identity_enabled():
            return Response(
                {
                    "detail": (
                        "Outbound email settings require the platform extension."
                    ),
                    "code": "IDENTITY_EXTENSION_REQUIRED",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if email_settings_managed_by_deployment():
            return Response(
                {
                    "detail": "Email settings are managed by deployment configuration.",
                    "code": "EMAIL_SETTINGS_MANAGED_BY_DEPLOYMENT",
                },
                status=status.HTTP_409_CONFLICT,
            )
        data = request.data or {}
        current = email_connection_kwargs()
        try:
            candidate = {
                "backend": str(data.get("backend", current["backend"]) or ""),
                "host": str(data.get("host", current["host"]) or ""),
                "port": int(data.get("port", current["port"])),
                "use_tls": bool(data.get("use_tls", current["use_tls"])),
                "use_ssl": bool(data.get("use_ssl", current["use_ssl"])),
                "username": str(data.get("host_user", current["username"]) or ""),
                "password": str(data.get("password", current["password"]) or ""),
                "from_email": str(data.get("from_email", current["from_email"]) or ""),
            }
        except (TypeError, ValueError):
            return Response(
                {"detail": "SMTP port must be an integer between 1 and 65535."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        configuration_error = validate_email_connection_config(candidate)
        if configuration_error:
            return Response(
                {"detail": configuration_error, "code": "EMAIL_CONFIGURATION_INVALID"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        mapping = {
            "backend": KEY_EMAIL_BACKEND,
            "host": KEY_EMAIL_HOST,
            "host_user": KEY_EMAIL_HOST_USER,
            "from_email": KEY_EMAIL_FROM,
        }
        for field, key in mapping.items():
            if field in data:
                set_value(key=key, value=str(data[field] or ""), user=request.user)
        if "port" in data:
            set_value(key=KEY_EMAIL_PORT, value=str(int(data["port"])), user=request.user)
        if "use_tls" in data:
            set_bool(KEY_EMAIL_USE_TLS, bool(data["use_tls"]), user=request.user)
        if "use_ssl" in data:
            set_bool(KEY_EMAIL_USE_SSL, bool(data["use_ssl"]), user=request.user)
        if "password" in data and str(data["password"] or "").strip():
            set_value(key=SECRET_KEY_EMAIL_PASSWORD, secret=str(data["password"]), user=request.user)
        _audit(request, "platform_settings.email.update")
        return self.get(request)


class PlatformOpsSettingsEmailTestView(APIView):
    permission_classes = [HasPlatformPermission.for_actions(ADMIN_USERS_MANAGE)]

    def post(self, request):
        if not runtime_settings_svc.enterprise_identity_enabled():
            return Response(
                {
                    "detail": (
                        "Outbound email settings require the platform extension."
                    ),
                    "code": "IDENTITY_EXTENSION_REQUIRED",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        recipient = str((request.data or {}).get("recipient") or request.user.email or "").strip()
        if not recipient:
            return Response({"detail": "recipient is required"}, status=status.HTTP_400_BAD_REQUEST)
        cfg = email_connection_kwargs()
        if cfg["configuration_error"]:
            return Response(
                {
                    "ok": False,
                    "error": "Email delivery is not configured.",
                    "code": "EMAIL_SERVICE_UNAVAILABLE",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            connection = get_connection(
                backend=cfg["backend"],
                host=cfg["host"] or None,
                port=cfg["port"] or None,
                username=cfg["username"] or None,
                password=cfg["password"] or None,
                use_tls=cfg["use_tls"],
                use_ssl=cfg["use_ssl"],
            )
            EmailMessage(
                subject="HyperFileLens test email",
                body="This is a test email from Admin Console platform settings.",
                from_email=cfg["from_email"],
                to=[recipient],
                connection=connection,
            ).send()
        except Exception as exc:
            logger.warning("platform email test failed: %s", exc, exc_info=True)
            return Response(
                {
                    "ok": False,
                    "error": "Email service is temporarily unavailable.",
                    "code": "EMAIL_SERVICE_UNAVAILABLE",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        _audit(request, "platform_settings.email.test", {"recipient": recipient})
        return Response({"ok": True, "recipient": recipient})


_EE_IDENTITY_PATCH_FIELDS = frozenset(
    {
        "email_signup_enabled",
        "email_code_login_enabled",
        "google_oauth_enabled",
        "google_client_id",
        "google_client_secret",
        "turnstile_site_key",
        "turnstile_secret_key",
        "iam",
    }
)


def _identity_patch_requires_extension(data: dict) -> bool:
    """Return whether this PATCH body touches enterprise identity settings.

    An empty ``iam`` object is ignored so Community clients can still update
    Admin Console / CIDR controls without triggering the extension gate.
    """
    for field in _EE_IDENTITY_PATCH_FIELDS:
        if field not in data:
            continue
        if field == "iam":
            iam = data.get("iam")
            if isinstance(iam, dict) and iam:
                return True
            continue
        return True
    return False


class PlatformOpsSettingsIdentityView(APIView):
    permission_classes = [HasPlatformPermission.for_actions(ADMIN_USERS_MANAGE)]

    def get(self, request):
        identity_enabled = runtime_settings_svc.enterprise_identity_enabled()
        return Response(
            {
                "enterprise_identity_enabled": identity_enabled,
                "email_signup_enabled": email_signup_enabled(),
                "email_code_login_enabled": email_code_login_enabled(),
                "platform_ops_enabled": platform_ops_enabled(),
                "platform_ops_allowed_cidrs": platform_ops_allowed_cidrs(),
                "platform_ops_source": get_source(KEY_IDENTITY_PLATFORM_OPS),
                "turnstile_enabled": turnstile_enabled() if identity_enabled else False,
                "turnstile_site_key": turnstile_site_key() if identity_enabled else "",
                "turnstile_secret_configured": (
                    secret_configured(
                        SECRET_KEY_TURNSTILE,
                        env_name="TURNSTILE_SECRET_KEY",
                        settings_attr="TURNSTILE_SECRET_KEY",
                    )
                    if identity_enabled
                    else False
                ),
                "google_client_id": google_client_id() if identity_enabled else "",
                "google_client_secret_configured": (
                    secret_configured(
                        SECRET_KEY_GOOGLE,
                        env_name="GOOGLE_CLIENT_SECRET",
                        settings_attr="GOOGLE_CLIENT_SECRET",
                    )
                    if identity_enabled
                    else False
                ),
                "google_oauth_enabled": google_oauth_enabled(),
                "google_oauth_redirect_uri": _google_redirect_uri(),
                "iam": {
                    "registration_verification_code_minutes": get_registration_verification_code_minutes(),
                    "registration_token_expiry_hours": get_registration_token_expiry_hours(),
                    "password_reset_verification_code_minutes": get_password_reset_verification_code_minutes(),
                    "login_verification_code_minutes": get_login_verification_code_minutes(),
                    "password_reset_timeout_seconds": get_password_reset_timeout_seconds(),
                },
            }
        )

    def patch(self, request):
        data = request.data or {}
        if _identity_patch_requires_extension(data):
            if not runtime_settings_svc.enterprise_identity_enabled():
                return Response(
                    {
                        "detail": (
                            "Self-serve identity settings require the platform extension."
                        ),
                        "code": "IDENTITY_EXTENSION_REQUIRED",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        if "platform_ops_enabled" in data:
            if get_source(KEY_IDENTITY_PLATFORM_OPS) == "deployment":
                return Response(
                    {
                        "detail": "Admin Console availability is managed by deployment configuration.",
                        "code": "PLATFORM_OPS_MANAGED_BY_DEPLOYMENT",
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            if platform_ops_enabled() and not bool(data["platform_ops_enabled"]):
                if str(data.get("confirm_disable") or "") != "DISABLE":
                    return Response(
                        {
                            "detail": "Type DISABLE to confirm turning off Admin Console access.",
                            "code": "PLATFORM_OPS_DISABLE_CONFIRMATION_REQUIRED",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        bool_map = {
            "email_signup_enabled": KEY_IDENTITY_EMAIL_SIGNUP,
            "email_code_login_enabled": KEY_IDENTITY_EMAIL_CODE_LOGIN,
            "google_oauth_enabled": KEY_IDENTITY_GOOGLE_OAUTH,
            "platform_ops_enabled": KEY_IDENTITY_PLATFORM_OPS,
        }
        for field, key in bool_map.items():
            if field in data:
                set_bool(key, bool(data[field]), user=request.user)
        if "platform_ops_allowed_cidrs" in data:
            cidrs = data["platform_ops_allowed_cidrs"]
            if isinstance(cidrs, str):
                cidrs = [part.strip() for part in cidrs.split(",") if part.strip()]
            set_str_list(KEY_IDENTITY_OPS_CIDRS, list(cidrs or []), user=request.user)
        if "turnstile_site_key" in data:
            set_value(key=KEY_IDENTITY_TURNSTILE_SITE, value=str(data["turnstile_site_key"] or ""), user=request.user)
        if "turnstile_secret_key" in data and str(data["turnstile_secret_key"] or "").strip():
            set_value(key=SECRET_KEY_TURNSTILE, secret=str(data["turnstile_secret_key"]), user=request.user)
        if "google_client_id" in data:
            set_value(key=KEY_IDENTITY_GOOGLE_CLIENT_ID, value=str(data["google_client_id"] or ""), user=request.user)
        if "google_client_secret" in data and str(data["google_client_secret"] or "").strip():
            set_value(key=SECRET_KEY_GOOGLE, secret=str(data["google_client_secret"]), user=request.user)

        iam = data.get("iam") or {}
        if isinstance(iam, dict):
            self._patch_iam_global(iam, user=request.user)

        sync_google_social_app()
        _audit(request, "platform_settings.identity.update")
        return self.get(request)

    def _patch_iam_global(self, iam: dict, *, user) -> None:
        specs = {
            "registration_verification_code_minutes": (
                iam_conf.CONFIG_KEY_REGISTRATION_CODE_MINUTES,
                GlobalConfig.ValueType.NUMBER,
                iam_conf.DEFAULT_REGISTRATION_VERIFICATION_CODE_MINUTES,
            ),
            "registration_token_expiry_hours": (
                iam_conf.CONFIG_KEY_REGISTRATION_TOKEN_EXPIRY_HOURS,
                GlobalConfig.ValueType.NUMBER,
                iam_conf.DEFAULT_REGISTRATION_TOKEN_EXPIRY_HOURS,
            ),
            "password_reset_verification_code_minutes": (
                iam_conf.CONFIG_KEY_PASSWORD_RESET_CODE_MINUTES,
                GlobalConfig.ValueType.NUMBER,
                iam_conf.DEFAULT_PASSWORD_RESET_VERIFICATION_CODE_MINUTES,
            ),
            "login_verification_code_minutes": (
                iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES,
                GlobalConfig.ValueType.NUMBER,
                iam_conf.DEFAULT_LOGIN_VERIFICATION_CODE_MINUTES,
            ),
            "password_reset_timeout_seconds": (
                iam_conf.CONFIG_KEY_PASSWORD_RESET_TIMEOUT,
                GlobalConfig.ValueType.NUMBER,
                iam_conf.DEFAULT_PASSWORD_RESET_TIMEOUT_SECONDS,
            ),
        }
        for field, (key, value_type, default) in specs.items():
            if field not in iam:
                continue
            value = iam[field]
            validate_config_key(key)
            registry = registry_by_key().get(key)
            GlobalConfig.objects.update_or_create(
                key=key,
                scope=GlobalConfig.Scope.GLOBAL,
                tenant_key="",
                defaults={
                    "value": value,
                    "value_type": value_type,
                    "category": registry.category if registry else "iam",
                    "description": registry.description if registry else "",
                    "is_active": True,
                    "updated_by": user,
                    "created_by": user,
                },
            )
            invalidate_config_cache(key=key, tenant_key="", scope="global")


class PlatformOpsSettingsAiView(APIView):
    permission_classes = [HasPlatformPermission.for_actions(INFRA_AI_MODELS_MANAGE)]

    def get(self, request):
        return Response(
            {
                "openai_api_key_configured": bool(openai_api_key()),
                "openai_api_key_hint": mask_secret(openai_api_key() or ""),
                "openai_api_base": openai_api_base(),
                "azure_openai_api_key_configured": bool(runtime_azure_key()),
                "azure_openai_api_key_hint": mask_secret(runtime_azure_key() or ""),
                "azure_openai_api_base": runtime_azure_base() or "",
                "gemini_api_key_configured": bool(gemini_api_key()),
                "gemini_api_key_hint": mask_secret(gemini_api_key() or ""),
                "langfuse_enabled": langfuse_enabled(),
                "langfuse_public_key_configured": bool(langfuse_public_key()),
                "langfuse_secret_key_configured": bool(langfuse_secret_key()),
                "langfuse_base_url": langfuse_base_url(),
                "llm": {
                    "provider": get_config(
                        insight_conf.CONFIG_KEY_LLM_PROVIDER,
                        default=insight_conf.DEFAULT_LLM_PROVIDER,
                    ),
                    "output_language": get_config(
                        insight_conf.CONFIG_KEY_LLM_OUTPUT_LANGUAGE,
                        default=insight_conf.DEFAULT_LLM_OUTPUT_LANGUAGE,
                    ),
                    "openai_model": get_config(
                        insight_conf.CONFIG_KEY_OPENAI_MODEL,
                        default=insight_conf.DEFAULT_OPENAI_MODEL,
                    ),
                },
            }
        )

    def patch(self, request):
        data = request.data or {}
        secret_fields = {
            "openai_api_key": SECRET_KEY_OPENAI,
            "azure_openai_api_key": SECRET_KEY_AZURE,
            "gemini_api_key": SECRET_KEY_GEMINI,
            "langfuse_public_key": SECRET_KEY_LANGFUSE_PUBLIC,
            "langfuse_secret_key": SECRET_KEY_LANGFUSE_SECRET,
        }
        for field, key in secret_fields.items():
            if field in data and str(data[field] or "").strip():
                set_value(key=key, secret=str(data[field]), user=request.user)
        if "openai_api_base" in data:
            set_value(key=KEY_AI_OPENAI_BASE, value=str(data["openai_api_base"] or ""), user=request.user)
        if "azure_openai_api_base" in data:
            set_value(key=KEY_AI_AZURE_BASE, value=str(data["azure_openai_api_base"] or ""), user=request.user)
        if "langfuse_base_url" in data:
            set_value(key=KEY_AI_LANGFUSE_BASE, value=str(data["langfuse_base_url"] or ""), user=request.user)
        if "langfuse_enabled" in data:
            set_bool(KEY_AI_LANGFUSE_ENABLED, bool(data["langfuse_enabled"]), user=request.user)

        llm = data.get("llm") or {}
        if isinstance(llm, dict):
            self._patch_llm_global(llm, user=request.user)

        _audit(request, "platform_settings.ai.update")
        return self.get(request)

    def _patch_llm_global(self, llm: dict, *, user) -> None:
        mapping = {
            "provider": (insight_conf.CONFIG_KEY_LLM_PROVIDER, GlobalConfig.ValueType.STRING),
            "output_language": (insight_conf.CONFIG_KEY_LLM_OUTPUT_LANGUAGE, GlobalConfig.ValueType.STRING),
            "openai_model": (insight_conf.CONFIG_KEY_OPENAI_MODEL, GlobalConfig.ValueType.STRING),
        }
        for field, (key, value_type) in mapping.items():
            if field not in llm:
                continue
            registry = registry_by_key().get(key)
            GlobalConfig.objects.update_or_create(
                key=key,
                scope=GlobalConfig.Scope.GLOBAL,
                tenant_key="",
                defaults={
                    "value": llm[field],
                    "value_type": value_type,
                    "category": registry.category if registry else "insight",
                    "description": registry.description if registry else "",
                    "is_active": True,
                    "updated_by": user,
                    "created_by": user,
                },
            )
            invalidate_config_cache(key=key, tenant_key="", scope="global")


class PlatformOpsSettingsAiTestView(APIView):
    permission_classes = [HasPlatformPermission.for_actions(INFRA_AI_MODELS_MANAGE)]

    def post(self, request):
        key = openai_api_key() or runtime_azure_key() or gemini_api_key()
        if not key:
            return Response({"ok": False, "error": "No API key configured"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "message": "API key is configured"})


class PlatformOpsSettingsDefaultsView(APIView):
    permission_classes = [HasPlatformPermission.for_actions(ADMIN_USERS_MANAGE)]

    def get(self, request):
        retention_default = get_config(storage_conf.CONFIG_KEY_RETENTION, default={})
        filters_default = get_config(storage_conf.CONFIG_KEY_FILTERS, default={})
        dr_default = get_config(
            CONFIG_KEY_DR_TASK_CONCURRENCY,
            default=DEFAULT_DR_TASK_CONCURRENCY,
        )
        return Response(
            {
                "dr_task_concurrency": dr_default,
                "retention_default": retention_default,
                "filters_default": filters_default,
            }
        )

    def patch(self, request):
        data = request.data or {}
        if "dr_task_concurrency" in data:
            GlobalConfig.objects.update_or_create(
                key=CONFIG_KEY_DR_TASK_CONCURRENCY,
                scope=GlobalConfig.Scope.GLOBAL,
                tenant_key="",
                defaults={
                    "value": int(data["dr_task_concurrency"]),
                    "value_type": GlobalConfig.ValueType.NUMBER,
                    "category": "file_dr",
                    "is_active": True,
                    "updated_by": request.user,
                    "created_by": request.user,
                },
            )
            invalidate_config_cache(key=CONFIG_KEY_DR_TASK_CONCURRENCY, tenant_key="", scope="global")
        if "retention_default" in data:
            self._upsert_object(storage_conf.CONFIG_KEY_RETENTION, data["retention_default"], user=request.user)
        if "filters_default" in data:
            self._upsert_object(storage_conf.CONFIG_KEY_FILTERS, data["filters_default"], user=request.user)
        _audit(request, "platform_settings.defaults.update")
        return self.get(request)

    def _upsert_object(self, key: str, value, *, user) -> None:
        registry = registry_by_key().get(key)
        GlobalConfig.objects.update_or_create(
            key=key,
            scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="",
            defaults={
                "value": value,
                "value_type": GlobalConfig.ValueType.OBJECT,
                "category": registry.category if registry else "backup",
                "description": registry.description if registry else "",
                "is_active": True,
                "updated_by": user,
                "created_by": user,
            },
        )
        invalidate_config_cache(key=key, tenant_key="", scope="global")


class PlatformOpsSettingsEnvironmentView(APIView):
    permission_classes = [HasPlatformPermission.for_actions(ADMIN_USERS_MANAGE)]

    def get(self, request):
        from apps.instance_settings.services.environment_payload import (
            deploy_profile_staff_payload,
            system_health_payload,
        )

        cfg = email_connection_kwargs()
        identity_enabled = runtime_settings_svc.enterprise_identity_enabled()
        return Response(
            {
                "app_version": deploy_profile_staff_payload().get("app_version"),
                "agent_version": deploy_profile_staff_payload().get("agent_version"),
                "django_debug": deploy_profile_staff_payload().get("django_debug"),
                "effective": {
                    "tenant_public_url": tenant_public_url(),
                    "email_signup_enabled": email_signup_enabled(),
                    "email_code_login_enabled": email_code_login_enabled(),
                    "password_reset_available": password_reset_available(),
                    "platform_ops_enabled": platform_ops_enabled(),
                    "turnstile_enabled": (
                        turnstile_enabled() if identity_enabled else False
                    ),
                    "google_oauth_enabled": google_oauth_enabled(),
                    "email_host_configured": bool(cfg["host"]),
                    "email_password_configured": bool(cfg["password"]),
                    "openai_configured": bool(openai_api_key()),
                },
                "sources": {
                    "email_signup_enabled": get_source(KEY_IDENTITY_EMAIL_SIGNUP),
                    "email_code_login_enabled": get_source(
                        KEY_IDENTITY_EMAIL_CODE_LOGIN
                    ),
                    "google_oauth_enabled": get_source(KEY_IDENTITY_GOOGLE_OAUTH),
                    "turnstile_enabled": "env" if identity_enabled else "extension",
                    "email_host": cfg["source"],
                },
                "health": system_health_payload(),
            }
        )


# Stable aliases (prefer these in new code)
InstanceSettingsEmailView = PlatformOpsSettingsEmailView
InstanceSettingsEmailTestView = PlatformOpsSettingsEmailTestView
InstanceSettingsIdentityView = PlatformOpsSettingsIdentityView
InstanceSettingsAiView = PlatformOpsSettingsAiView
InstanceSettingsAiTestView = PlatformOpsSettingsAiTestView
InstanceSettingsDefaultsView = PlatformOpsSettingsDefaultsView
InstanceSettingsEnvironmentView = PlatformOpsSettingsEnvironmentView
