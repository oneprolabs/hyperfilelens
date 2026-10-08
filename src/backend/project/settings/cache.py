"""
Django cache configuration (deployment).
"""

from __future__ import annotations

from copy import deepcopy
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from redis.backoff import NoBackoff
from redis.retry import Retry

from .env import build_cache_config, env_str


def _redis_cache_url() -> str:
    explicit = env_str("CACHE_REDIS_URL")
    if explicit:
        return explicit

    broker = env_str("CELERY_BROKER_URL", "redis://redis:6379/0")
    parsed = urlparse(broker)
    return f"{parsed.scheme}://{parsed.netloc}/1"


_CACHE_BACKEND = env_str("CACHE_BACKEND", "redis").lower()

if _CACHE_BACKEND == "redis":
    CACHES = build_cache_config(
        "redis",
        redis_location=_redis_cache_url(),
    )
elif _CACHE_BACKEND == "memcached":
    CACHES = build_cache_config(
        "memcached",
        memcached_location=env_str("CACHE_MEMCACHED_URL", "127.0.0.1:11211"),
    )
elif _CACHE_BACKEND == "database":
    CACHES = build_cache_config("database")
else:
    CACHES = build_cache_config("locmem")


def gateway_directory_cache_config(default: dict) -> dict:
    """Isolate optional status caching from the application's cache timeouts."""
    config = deepcopy(default)
    backend = config["BACKEND"]
    if backend == "django.core.cache.backends.redis.RedisCache":
        # Redis URL query options override ConnectionPool keyword options.
        # Do not let an application-wide URL undo this alias's short timeouts.
        locations = config["LOCATION"]
        if isinstance(locations, str):
            locations = re.split("[;,]", locations)
        bounded_options = {
            "socket_connect_timeout",
            "socket_timeout",
            "retry_on_timeout",
            "retry_on_error",
            "retry",
        }
        urls = []
        for location in locations:
            parts = urlsplit(location)
            query = [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key not in bounded_options
            ]
            urls.append(urlunsplit(parts._replace(query=urlencode(query))))
        config["LOCATION"] = urls
        config.setdefault("OPTIONS", {}).update(
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
            retry_on_timeout=False,
            retry_on_error=[],
            retry=Retry(NoBackoff(), 0),
        )
    elif backend == "django.core.cache.backends.memcached.PyMemcacheCache":
        config.setdefault("OPTIONS", {}).update(
            connect_timeout=0.25,
            timeout=0.25,
        )
    elif backend != "django.core.cache.backends.locmem.LocMemCache":
        # Database/custom caches have no portable per-operation deadline.
        # Persisted HFL snapshots still provide the cross-process successful cache.
        config = build_cache_config("locmem")["default"]
        config["LOCATION"] = "gateway-directory-status"
    config["KEY_PREFIX"] = f"{config.get('KEY_PREFIX', '')}:gateway-directory"
    return config


CACHES["gateway_directory"] = gateway_directory_cache_config(CACHES["default"])

CACHE_MIDDLEWARE_ALIAS = "default"
CACHE_MIDDLEWARE_SECONDS = 300
CACHE_MIDDLEWARE_KEY_PREFIX = "backend"
