"""Runtime settings for direct NAS mount cleanup."""

from project.settings.env import env_int


DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS = max(
    0,
    env_int("INSIGHT_DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS", 600),
)
DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS = max(
    30,
    env_int("INSIGHT_DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS", 300),
)
