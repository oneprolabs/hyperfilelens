"""Runtime settings for restore orchestration."""

from project.settings.env import env_int


_DEFAULT_DIRECTORY_CONCURRENCY = 4
_configured_directory_concurrency = env_int(
    "PROTECTION_RESTORE_DIRECTORY_CONCURRENCY",
    _DEFAULT_DIRECTORY_CONCURRENCY,
)
DIRECTORY_CONCURRENCY = (
    _configured_directory_concurrency
    if _configured_directory_concurrency >= 1
    else _DEFAULT_DIRECTORY_CONCURRENCY
)
ACTIVITY_LEASE_SECONDS = max(
    1,
    env_int("PROTECTION_RESTORE_ACTIVITY_LEASE_SECONDS", 300),
)


DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS = max(
    0,
    env_int("INSIGHT_DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 600),
)
DIRECT_NAS_USER_DATA_MOUNT_CLEANUP_GRACE_SECONDS = max(
    0,
    env_int("RESTORE_DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 0),
)
DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS = max(
    30,
    env_int("INSIGHT_DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS", 300),
)
