"""Complete manifest-only inventory: no retention filtering or tree traversal."""

import json
import re

from apps.storage.services.internal import kopia_cli


SNAPSHOT_ID = re.compile(r"^[0-9a-f]{32}$")


def parse_snapshot_inventory(raw):
    rows = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(rows, list):
        raise ValueError("Snapshot inventory is incomplete.")
    seen = set()
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed snapshot manifest.")
        snapshot_id = row.get("id")
        labels = row.get("labels")
        if (
            not isinstance(snapshot_id, str) or not SNAPSHOT_ID.fullmatch(snapshot_id)
            or snapshot_id in seen or not isinstance(labels, dict)
            or labels.get("type") != "snapshot"
            or not all(isinstance(value, str) for value in labels.values())
        ):
            raise ValueError("Snapshot inventory identity or labels are invalid.")
        seen.add(snapshot_id)
        result.append({
            "snapshot_id": snapshot_id,
            "source": {
                "host": labels.get("hostname", ""),
                "user": labels.get("username", ""),
                "path": labels.get("path", ""),
            },
            "operation_id": labels.get("tag:hfl-operation", ""),
        })
    return result


def controller_snapshot_inventory(repository):
    from apps.storage.services.internal.repository_ownership import verify_s3_repository_ownership

    verify_s3_repository_ownership(repository, adopt_legacy=False)
    config_file = kopia_cli.usage_config_file(repository)
    kopia_cli.connect_s3_repository(repository, config_file=config_file)
    result = kopia_cli._run_repository_command(
        repository, ["manifest", "list", "--filter=type:snapshot", "--json"],
        config_file=config_file, timeout_seconds=120,
    )
    if result.returncode:
        raise ValueError("Unable to obtain complete snapshot inventory.")
    return parse_snapshot_inventory(result.stdout)
