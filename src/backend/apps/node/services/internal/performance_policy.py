"""Versioned, optional performance policy; never a prerequisite for node work."""

from __future__ import annotations

from copy import deepcopy

from django.core.exceptions import ValidationError
from django.utils import timezone

POLICY_KEY = "performance_policy"
STATUS_KEY = "performance_status"
CAPABILITY = "performance_policy_v1"
FIELDS = {
    "cpuCores": (1, 4096),
    "memCapMb": (128, 16 * 1024 * 1024),
    "bandwidth": (1, 9999),
    "kopiaCacheMb": (0, 65536),
    "logRetention": (1, 365),
    "logSpaceMb": (64, 8192),
}
ITEMS = ("cpu", "memory", "bandwidth", "cache", "logLevel", "retention", "logSpace")
STATES = {"applied", "pending", "unsupported", "degraded", "monitoring", "disabled"}


def normalize_settings(raw: object, *, role: str, legacy_cache: bool = True) -> dict:
    """Validate console input without relying on frontend bounds."""
    if not isinstance(raw, dict):
        raise ValidationError("Performance settings must be an object.")
    aliases = {
        "cpu_cores": "cpuCores",
        "mem_cap_mb": "memCapMb",
        "kopia_cache_mb": "kopiaCacheMb",
        "log_retention": "logRetention",
        "log_space_mb": "logSpaceMb",
    }
    raw = {aliases.get(key, key): value for key, value in raw.items()}
    allowed = set(FIELDS) | {"log"}
    if set(raw) - allowed:
        raise ValidationError("Unknown performance setting.")
    settings = {}
    for key, (minimum, maximum) in FIELDS.items():
        if key == "kopiaCacheMb" and (role == "gateway" or not legacy_cache):
            continue
        value = raw.get(key)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValidationError(
                {key: f"Enter an integer from {minimum} to {maximum}."}
            )
        settings[key] = value
    if raw.get("log") not in {"debug", "info", "warning", "error"}:
        raise ValidationError({"log": "Unsupported log level."})
    settings["log"] = raw["log"]
    return settings


def merge_performance_metadata(current: object, incoming: dict, *, role: str) -> dict:
    """Protect server-owned policy/status, and activate only an explicit save."""
    existing = deepcopy(current) if isinstance(current, dict) else {}
    inventory = existing.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    old_perf = existing.get("perf_settings")
    old_perf = old_perf if isinstance(old_perf, dict) else {}
    capabilities = inventory.get("capabilities")
    capable = role in {"agent", "proxy"} and isinstance(capabilities, list) and "cache_management_v1" in capabilities
    supplied = {
        key: value
        for key, value in incoming.items()
        if key not in {POLICY_KEY, STATUS_KEY, "cache_policy", "cache_status", "cache_space_check", "inventory", "capabilities", "inventory_session_id", "inventory_capabilities_session_id"}
    }
    existing.update(supplied)
    if "perf_settings" not in incoming:
        return existing
    settings = normalize_settings(incoming["perf_settings"], role=role, legacy_cache=not capable)
    cores = inventory.get("cpu_cores", inventory.get("cpu_logical_cores"))
    memory = inventory.get("memory_total_bytes")
    if type(cores) is int and cores > 0 and settings["cpuCores"] > cores:
        raise ValidationError({"cpuCores": "Quota exceeds reported host CPU capacity."})
    if (
        type(memory) is int
        and memory >= 128 * 1024**2
        and settings["memCapMb"] > round(memory / 1024**2)
    ):
        raise ValidationError(
            {"memCapMb": "Budget exceeds reported host memory capacity."}
        )
    previous = existing.get(POLICY_KEY)
    previous = previous if isinstance(previous, dict) else {}
    perf_settings = deepcopy(settings)
    if capable:
        previous_settings = previous.get("settings")
        previous_settings = previous_settings if isinstance(previous_settings, dict) else old_perf
        for key in ("kopiaCacheMb", "kopia_cache_mb"):
            if key in previous_settings:
                settings[key] = deepcopy(previous_settings[key])
            if key in old_perf:
                perf_settings[key] = deepcopy(old_perf[key])
        # Preserve hidden legacy fields per storage location, including absence.
        # An old form cannot restore a capacity limit on a capable node.
        existing["perf_settings"] = perf_settings
    revision = previous.get("revision", 0)
    revision = revision if type(revision) is int and revision >= 0 else 0
    reported = existing.get(STATUS_KEY)
    observed = reported.get("observed_revision", 0) if isinstance(reported, dict) else 0
    observed = (
        observed if type(observed) is int and 0 <= observed <= 1_000_000_000 else 0
    )
    if previous.get("settings") == settings and observed <= revision:
        return existing
    revision = max(revision, observed)
    existing["perf_settings"] = perf_settings if capable else settings
    existing[POLICY_KEY] = {
        "revision": revision + 1,
        "settings": settings,
        "saved_at": timezone.now().isoformat(),
    }
    existing.pop(STATUS_KEY, None)
    return existing


def normalize_report(raw: object, *, revision: int) -> dict:
    """Bound authenticated Agent reports; do not accept executable config."""
    if (
        not isinstance(raw, dict)
        or type(raw.get("revision")) is not int
        or raw.get("revision") != revision
    ):
        raise ValidationError("Performance report revision does not match.")
    supplied = raw.get("items")
    if not isinstance(supplied, dict) or set(supplied) - set(ITEMS):
        raise ValidationError("Invalid performance report items.")
    items = {}
    for key, item in supplied.items():
        if not isinstance(item, dict) or item.get("state") not in STATES:
            raise ValidationError("Invalid performance report state.")
        items[key] = {
            "state": item["state"],
            "reason": str(item.get("reason", ""))[:300],
        }
    report = {
        "revision": revision,
        "items": items,
        "reported_at": timezone.now().isoformat(),
    }
    observed = raw.get("observed_revision")
    if type(observed) is int and revision < observed <= 1_000_000_000:
        report["observed_revision"] = observed
    return report
