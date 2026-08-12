"""
Feature definitions for SaaS console modules.

These keys are shared by:
- frontend route meta.requiredFeature
- backend DRF permission HasRequiredFeature
"""

from __future__ import annotations

FEATURE_DEFINITIONS: tuple[dict[str, str], ...] = (
    {"key": "dashboard", "label": "Dashboard", "default_path": "/"},
    {"key": "node", "label": "Node", "default_path": "/node/organization"},
    {"key": "task", "label": "Task", "default_path": "/ops/tasks"},
    {"key": "storage", "label": "Storage", "default_path": "/storage"},
    {"key": "backup", "label": "Backup", "default_path": "/backup"},
    {"key": "alerts", "label": "Alerts", "default_path": "/ops/alerts"},
    {"key": "audit", "label": "Audit", "default_path": "/ops/audit-logs"},
    {"key": "settings", "label": "Settings", "default_path": "/settings"},
)

FEATURE_KEYS = tuple(item["key"] for item in FEATURE_DEFINITIONS)
FEATURE_KEY_SET = set(FEATURE_KEYS)
FEATURE_DEFAULT_PATHS = {item["key"]: item["default_path"] for item in FEATURE_DEFINITIONS}
