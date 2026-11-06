"""Resolve repository-only cache settings without activating resource limits."""

DEFAULT_KOPIA_CACHE_MB = 8192
MAX_KOPIA_CACHE_MB = 65536


def node_kopia_cache_size_mb(node) -> int:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    policy = metadata.get("performance_policy")
    settings = policy.get("settings") if isinstance(policy, dict) else None
    if not isinstance(settings, dict):
        # The upgrade migrates this cache field without creating a complete
        # performance policy (which would also activate CPU/memory limits).
        settings = metadata.get("perf_settings")
    settings = settings if isinstance(settings, dict) else {}
    raw = settings.get("kopiaCacheMb", settings.get("kopia_cache_mb"))
    if type(raw) is not int or not 0 <= raw <= MAX_KOPIA_CACHE_MB:
        return DEFAULT_KOPIA_CACHE_MB
    return raw


def attach_node_kopia_cache_policy(*, node, payload: dict | None) -> dict | None:
    if node.role not in {"agent", "proxy"}:
        return payload
    if not isinstance(payload, dict) or not isinstance(payload.get("repository"), dict):
        return payload
    return {**payload, "kopia_cache_size_mb": node_kopia_cache_size_mb(node)}
