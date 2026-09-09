"""Neutral product terminology for persisted task events, not service logs."""

import re
from typing import Any


_ENGINE_NAME = re.compile("kopia", re.IGNORECASE)
_ENGINE_CODE = re.compile(r"kopia(?=_)", re.IGNORECASE)
_IDENTIFIER_FIELDS = frozenset({"step", "step_name", "current_step", "event_type"})


def neutral_event_text(value: str) -> str:
    value = _ENGINE_CODE.sub("REPOSITORY", value)
    return _ENGINE_NAME.sub("repository engine", value)


def neutral_event_metadata(value: Any) -> Any:
    """Copy text values recursively, preserving keys and routing identifiers."""
    if isinstance(value, str):
        return neutral_event_text(value)
    if isinstance(value, dict):
        return {
            key: item
            if key in _IDENTIFIER_FIELDS or key.endswith(("_id", "_ids", "_uuid"))
            else neutral_event_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [neutral_event_metadata(item) for item in value]
    return value
