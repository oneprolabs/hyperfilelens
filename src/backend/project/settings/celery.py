"""
Celery settings (deployment).
"""

from kombu import Queue

from .env import env_bool, env_int, env_str

CELERY_BROKER_URL = env_str("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = "django-db"
CELERY_BEAT_SCHEDULER = "common.scheduling.scheduler:CoalescingDatabaseScheduler"
CELERY_TIMEZONE = env_str("CELERY_TIMEZONE", "UTC")
CELERY_ENABLE_UTC = True

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"

CELERY_TASK_DEFAULT_QUEUE = env_str("CELERY_TASK_DEFAULT_QUEUE", "backend")
CELERY_TASK_QUEUES = (
    Queue(CELERY_TASK_DEFAULT_QUEUE),
    Queue("node.lifecycle"),
    Queue("node.ingest"),
    Queue("source.remote-io"),
    Queue("storage.provider-validation"),
)

CELERY_TASK_ROUTES = {
    "apps.node.tasks.lifecycle.*": {"queue": "node.lifecycle"},
    "apps.node.tasks.uplink_ingest.*": {"queue": "node.ingest"},
    "apps.source.tasks.connection_probe.*": {"queue": "source.remote-io"},
    "apps.storage.tasks.*storage_provider*": {"queue": "storage.provider-validation"},
}

CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": env_int("CELERY_VISIBILITY_TIMEOUT", 43200),
    "fanout_prefix": True,
    "fanout_patterns": True,
}

CELERY_TASK_TIME_LIMIT = env_int("CELERY_TASK_TIME_LIMIT", 3600)
CELERY_TASK_SOFT_TIME_LIMIT = env_int("CELERY_TASK_SOFT_TIME_LIMIT", 3000)
CELERY_RESULT_EXPIRES = env_int("CELERY_RESULT_EXPIRES", 604800)
CELERY_PERIODIC_WAKEUP_CLAIM_SECONDS = env_int(
    "CELERY_PERIODIC_WAKEUP_CLAIM_SECONDS",
    30,
)
CELERY_PERIODIC_WAKEUP_LEASE_SECONDS = env_int(
    "CELERY_PERIODIC_WAKEUP_LEASE_SECONDS",
    3600,
)
CELERY_PERIODIC_WAKEUP_MAX_QUEUE_SECONDS = env_int(
    "CELERY_PERIODIC_WAKEUP_MAX_QUEUE_SECONDS",
    86400,
)

CELERY_TASK_ACKS_LATE = env_bool("CELERY_TASK_ACKS_LATE", default=True)
CELERY_WORKER_PREFETCH_MULTIPLIER = env_int(
    "CELERY_WORKER_PREFETCH_MULTIPLIER",
    1,
)

CELERY_BEAT_SCHEDULE: dict = {}
