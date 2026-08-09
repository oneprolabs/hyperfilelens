"""
Subscription write/read facade.
"""

from apps.subscription.services.internal.license_ops import (
    activate_license,
    build_current_payload,
    get_active_license,
    get_instance_active_license,
    get_or_create_machine_code,
    resolve_instance_license_organization,
)
from apps.subscription.services.quota import (
    assert_gateway_select_within_limits,
    enforce_license_quota,
    enforce_node_role_quota,
    enforce_repository_type_quota,
    normalize_scope_path,
    resolve_scope_entry,
    summarize_gateway_select_scopes,
    validate_quota,
)

__all__ = [
    "activate_license",
    "assert_gateway_select_within_limits",
    "build_current_payload",
    "enforce_license_quota",
    "enforce_node_role_quota",
    "enforce_repository_type_quota",
    "get_active_license",
    "get_instance_active_license",
    "get_or_create_machine_code",
    "normalize_scope_path",
    "resolve_instance_license_organization",
    "resolve_scope_entry",
    "summarize_gateway_select_scopes",
    "validate_quota",
]
