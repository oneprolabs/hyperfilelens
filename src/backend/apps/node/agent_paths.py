"""Canonical agent data-directory paths shared by control plane and agent."""

from __future__ import annotations

DEFAULT_AGENT_DATA_DIR = "/opt/hyperfilelens-agent"

MOUNTS_DIR = "mounts"
MOUNT_REPOSITORIES_DIR = "repositories"
MOUNT_SOURCES_DIR = "sources"
MOUNT_CUSTOM_DIR = "custom"
MOUNT_VALIDATIONS_DIR = "validations"


def agent_data_dir(data_dir: str | None = None) -> str:
    root = (data_dir or DEFAULT_AGENT_DATA_DIR).strip().rstrip("/")
    return root or DEFAULT_AGENT_DATA_DIR


def agent_mounts_dir(data_dir: str | None = None) -> str:
    return f"{agent_data_dir(data_dir)}/{MOUNTS_DIR}"


def repository_mount_point(
    repository_id: int,
    *,
    node_id: int | None = None,
    data_dir: str | None = None,
) -> str:
    leaf = f"repo-{int(repository_id)}"
    if node_id is not None:
        leaf = f"{leaf}-node-{int(node_id)}"
    return f"{agent_mounts_dir(data_dir)}/{MOUNT_REPOSITORIES_DIR}/{leaf}"


def source_mount_point(resource_id: int, *, data_dir: str | None = None) -> str:
    return (
        f"{agent_mounts_dir(data_dir)}/{MOUNT_SOURCES_DIR}/source-{int(resource_id)}"
    )


def validation_mount_point(
    validation_id: str,
    repository_id: int,
    node_id: int,
    *,
    data_dir: str | None = None,
) -> str:
    safe_validation_id = str(validation_id or "").strip().lower()
    if not safe_validation_id or any(
        char not in "0123456789abcdef-" for char in safe_validation_id
    ):
        raise ValueError("validation id is invalid")
    return (
        f"{agent_mounts_dir(data_dir)}/{MOUNT_VALIDATIONS_DIR}/"
        f"{safe_validation_id}/repo-{int(repository_id)}-node-{int(node_id)}"
    )


def source_validation_mount_point(
    validation_id: str,
    node_id: int,
    *,
    data_dir: str | None = None,
) -> str:
    safe_validation_id = str(validation_id or "").strip().lower()
    if not safe_validation_id or any(
        char not in "0123456789abcdef-" for char in safe_validation_id
    ):
        raise ValueError("validation id is invalid")
    return (
        f"{agent_mounts_dir(data_dir)}/{MOUNT_VALIDATIONS_DIR}/"
        f"{safe_validation_id}/source-draft-node-{int(node_id)}"
    )


def require_agent_mount_path(path: str, *, data_dir: str | None = None) -> str:
    cleaned = str(path or "").strip().rstrip("/")
    if not cleaned:
        raise ValueError("mount path is required")
    mounts_root = agent_mounts_dir(data_dir)
    if cleaned == mounts_root or cleaned.startswith(f"{mounts_root}/"):
        return cleaned
    raise ValueError(f"mount path must be under {mounts_root}/")
