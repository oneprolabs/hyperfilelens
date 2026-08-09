"""HyperFileLens fixed tenant/operations deployment settings."""

from .env import env_bool, env_csv, env_int, env_str

HFL_EMAIL_SIGNUP_ENABLED = env_bool("HFL_EMAIL_SIGNUP_ENABLED", default=False)
HFL_EMAIL_CODE_LOGIN_ENABLED = env_bool(
    "HFL_EMAIL_CODE_LOGIN_ENABLED",
    default=False,
)
HFL_PLATFORM_OPS_ENABLED = env_bool("HFL_PLATFORM_OPS_ENABLED", default=True)
HFL_PLATFORM_OPS_ALLOWED_CIDRS = env_csv("HFL_PLATFORM_OPS_ALLOWED_CIDRS")
HFL_ADMIN_PORT = env_int("HFL_ADMIN_PORT", 11444)
HFL_ADMIN_PUBLIC_URL = env_str("HFL_ADMIN_PUBLIC_URL")
HFL_INSECURE_TLS = env_bool("HFL_INSECURE_TLS", default=True)

# Org EffectiveQuota create-path enforcement flag (Host). Plugin Provider performs checks.
# Intentionally NOT read from env (no customer bypass). manage.py test → False.
def resolve_quota_enforcement_enabled() -> bool:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        return False
    return False  # community / Host default; plugin still enforces when registered


HFL_QUOTA_ENFORCEMENT_ENABLED = resolve_quota_enforcement_enabled()

# License asymmetric key placeholders (PEM text). Empty = HMAC HFL-ACT only.
# Public key: runtime verify (future). Private key: offline issuer only — do not
# ship in Community/EE control-plane images.
HFL_LICENSE_PUBLIC_KEY_PEM = env_str("HFL_LICENSE_PUBLIC_KEY_PEM", "")
HFL_LICENSE_PRIVATE_KEY_PEM = env_str("HFL_LICENSE_PRIVATE_KEY_PEM", "")
