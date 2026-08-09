"""Subscription / license domain constants."""

# Community default: do not hard-block create paths without a QuotaProvider.
# Plugin builds enforce via SPI regardless; tests may override settings.
QUOTA_ENFORCEMENT_ENABLED = False

LICENSE_STATUS_ACTIVE = "active"
LICENSE_STATUS_EXPIRED = "expired"
LICENSE_STATUS_REVOKED = "revoked"

CHANGE_INITIAL = "initial"
CHANGE_RENEWAL = "renewal"
CHANGE_UPGRADE = "upgrade"
CHANGE_DOWNGRADE = "downgrade"
CHANGE_REVOKED = "revoked"

UNLIMITED = -1

# Canonical org-level quota keys (EffectiveQuota / UI / enforcement).
# max_workloads (design primary meter) maps to protected sources for now.
#
# Public gateway model (three layers — do not conflate):
# 1) License.max_public_gateways — instance count of Public Gateways (not org-split)
# 2) LensGatewayLink.capacity_gb — per-gateway infrastructure capacity
# 3) max_public_gateway_capacity_gb — org quota for total Public Gateway capacity use
# max_gateways remains private/user Data Gateway node count (org-allocatable).
QUOTA_KEYS = (
    "max_source_hosts",
    "max_source_nas",
    "max_protected_sources",
    "max_proxies",
    "max_gateways",
    "max_object_storage",
    "max_target_nas",
    "max_standalone_disk",
    "max_storage_gb",
    "max_users",
    "ai_tokens",  # Lifetime Copilot/LLM total_tokens (LensUsageLedger; no period reset)
    "max_public_gateway_capacity_gb",
    "max_alert_policies",
    "gateway_select_max_files",
    "gateway_select_max_bytes",
)
# Intentionally NOT quota keys: user/backup Tasks (License.max_tasks is legacy only).

QUOTA_UNITS: dict[str, str] = {
    "max_storage_gb": "gb",
    "max_public_gateway_capacity_gb": "gb",
    "gateway_select_max_bytes": "bytes",
    "ai_tokens": "tokens",
}

# Map quota key -> collect_usage_stats() field. None = policy-only (no lifetime used).
USAGE_KEY_BY_QUOTA: dict[str, str | None] = {
    "max_source_hosts": "agents_count",
    "max_source_nas": "source_nas_count",
    "max_protected_sources": "protected_sources_count",
    "max_proxies": "proxies_count",
    "max_gateways": "gateways_count",
    "max_object_storage": "object_storage_count",
    "max_target_nas": "target_nas_count",
    "max_standalone_disk": "standalone_disk_count",
    "max_storage_gb": "storage_used_gb",
    "max_users": "users_count",
    "ai_tokens": "ai_tokens_used",
    "max_public_gateway_capacity_gb": "public_gateway_capacity_used_gb",
    "max_alert_policies": "alert_policies_count",
    "gateway_select_max_files": None,
    "gateway_select_max_bytes": None,
}

# Unsigned default instance grant (pubkey/privkey injection not wired yet).
# Sized as a generous private-deployment pool so Ops can allocate before signing.
DEFAULT_LIMITS = {
    "max_organizations": 50,
    "max_users": 500,
    "max_nodes": 200,
    "max_storage_gb": 5000,
    "max_gateways": 50,
    # Platform Public Gateway count (instance license; not org-split).
    "max_public_gateways": 20,
    # Instance pool for org Public Gateway capacity allocations (GiB).
    "max_public_gateway_capacity_gb": 5000,
    # License.ai_insights_quota stores the instance AI token budget (total_tokens).
    "ai_insights_quota": 50_000_000,
    "ai_tokens": 50_000_000,  # Canonical meter key (alias of ai_insights_quota).
    # max_tasks intentionally omitted — Tasks are not a quota meter.
    "max_alert_policies": 500,
    "max_source_hosts": 200,
    "max_source_nas": 200,
    "max_source_proxies": 200,
    "max_object_storage": 200,
    "max_target_nas": 200,
    "max_standalone_disk": 200,
    "max_protected_sources": 500,
    "gateway_select_max_files": UNLIMITED,
    "gateway_select_max_bytes": UNLIMITED,
}
