"""
Base Django settings for HyperFileLens (deployment / framework only).

Business defaults live in each app's ``conf.py`` and are resolved via
``apps.configuration`` + per-app ``config.py`` (e.g. ``apps.insight.config``).

Submodules merged at the end: database, rest, channels, swagger, celery,
cache, logging_config, iam (deploy), security.
"""

from pathlib import Path

from .env import BASE_DIR, env_bool, env_csv, env_int, env_str

# Load extensions onto sys.path before INSTALLED_APPS (Gunicorn/Daphne entrypoints).
try:
    from common.extension_loader import bootstrap_extensions, extension_installed_apps

    bootstrap_extensions(backend_dir=BASE_DIR)
except Exception:  # pragma: no cover - settings must still load with empty socket
    def extension_installed_apps() -> list[str]:
        return []

# Override in production; default is for local dev only.
SECRET_KEY = env_str("SECRET_KEY", "dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
SENTRY_ENABLED = env_bool("SENTRY_ENABLED", default=False)
SENTRY_ENVIRONMENT = env_str("SENTRY_ENVIRONMENT")
PROTECTION_BACKUP_EXECUTION_BACKEND = env_str(
    "PROTECTION_BACKUP_EXECUTION_BACKEND",
    "celery",
).lower()
SOURCE_BACKUP_SELECTABLE_QUERY_MODE = env_str(
    "SOURCE_BACKUP_SELECTABLE_QUERY_MODE",
    "legacy",
).lower()
SOURCE_FORCE_UNREGISTER_CLEANUP_WAIT_SECONDS = env_int(
    "SOURCE_FORCE_UNREGISTER_CLEANUP_WAIT_SECONDS", 120,
)
SOURCE_UNREGISTER_CLEANUP_WAIT_SECONDS = env_int(
    "SOURCE_UNREGISTER_CLEANUP_WAIT_SECONDS", 900,
)

CELERY_BACKUP_TASK_SOFT_TIME_LIMIT = env_int("CELERY_BACKUP_TASK_SOFT_TIME_LIMIT", 30)
CELERY_BACKUP_TASK_TIME_LIMIT = env_int("CELERY_BACKUP_TASK_TIME_LIMIT", 60)

_hosts = env_csv("DJANGO_ALLOWED_HOSTS")
ALLOWED_HOSTS = _hosts if _hosts else ["*"]

INSTALLED_APPS = [
    "common.apps.PlatformConfig",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "channels",
    "django_celery_beat",
    "django_celery_results",
    "dj_rest_auth",
    "dj_rest_auth.registration",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.headless",
    "corsheaders",
    "apps.configuration.apps.ConfigurationConfig",
    "apps.instance_settings.apps.InstanceSettingsConfig",
    "apps.iam.apps.IamConfig",
    "apps.node.apps.NodeConfig",
    "apps.storage.apps.StorageConfig",
    "apps.protection.apps.ProtectionConfig",
    "apps.restore.apps.RestoreConfig",
    "apps.task.apps.TaskConfig",
    "apps.alert.apps.AlertConfig",
    "apps.audit.apps.AuditConfig",
    "apps.monitor.apps.MonitorConfig",
    "apps.notification.apps.NotificationConfig",
    "apps.insight.apps.InsightConfig",
    "apps.lens_bridge.apps.LensBridgeConfig",
    "apps.subscription.apps.SubscriptionConfig",
    "apps.source.apps.SourceConfig",
    "apps.platform_ops.apps.PlatformOpsConfig",
]
INSTALLED_APPS += extension_installed_apps()

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.iam.auth.middleware.AdminJWTSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "common.observability.middleware.RequestIdMiddleware",
    "common.i18n.middleware.LanguageCodeMappingMiddleware",
    "django.middleware.locale.LocaleMiddleware",
]

if env_bool("CORS_ALLOW_ALL_ORIGINS"):
    CORS_ALLOW_ALL_ORIGINS = True
else:
    _cors_origins = env_csv("CORS_ALLOWED_ORIGINS")
    if _cors_origins:
        CORS_ALLOWED_ORIGINS = _cors_origins

# Allow credentials for session cookies (required for session-based auth)
CORS_ALLOW_CREDENTIALS = True

ROOT_URLCONF = "project.urls_http"
WSGI_APPLICATION = "project.wsgi.application"
ASGI_APPLICATION = "project.asgi_http.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

LANGUAGE_CODE = "en"
LANGUAGES = (("en", "English"),)
LANGUAGE_CODE_MAPPING: dict[str, str] = {}
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

LOCALE_PATHS = [
    BASE_DIR.parent / "locale",
]

from .lang_packs import (  # noqa: E402
    EXTRA_LANGUAGE_CODE_MAPPING,
    EXTRA_LANGUAGES,
    EXTRA_LOCALE_PATHS,
)

LANGUAGES = LANGUAGES + tuple(EXTRA_LANGUAGES)
LOCALE_PATHS = list(LOCALE_PATHS) + list(EXTRA_LOCALE_PATHS)
LANGUAGE_CODE_MAPPING = {**LANGUAGE_CODE_MAPPING, **EXTRA_LANGUAGE_CODE_MAPPING}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = Path(env_str("HFL_MEDIA_ROOT", str(BASE_DIR / "media")))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Enrollment bootstrap stub templates (see apps.node.api.views.bootstrap_templates).
HFL_BOOTSTRAP_DIR = env_str("HFL_BOOTSTRAP_DIR", "")

from .database import *  # noqa: F401,F403,E402
from .rest import *  # noqa: F401,F403,E402
from .channels import *  # noqa: F401,F403,E402
from .swagger import *  # noqa: F401,F403,E402
from .celery import *  # noqa: F401,F403,E402
from .cache import *  # noqa: F401,F403,E402
from .logging_config import *  # noqa: F401,F403,E402
from .iam import *  # noqa: F401,F403,E402
from .security import *  # noqa: F401,F403,E402
from .deploy import *  # noqa: F401,F403,E402
