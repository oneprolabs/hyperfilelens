"""Resource detail links shared by event writers and legacy projections."""

from apps.source.constants import ResourceType


def source_event_target(resource_id: str | int, source_type: str) -> str:
    if source_type in ResourceType.REQUIRES_MOUNT:
        return f"/protection/backup-sources?tab=nas&openSource={resource_id}"
    return "/protection/backup-sources?tab=host"


def repository_event_target(resource_id: str | int, repo_type: str = "") -> str:
    tab = f"tab={repo_type}&" if repo_type in {"s3", "nas", "proxy_fs"} else ""
    return f"/node/repositories?{tab}focus={resource_id}"
