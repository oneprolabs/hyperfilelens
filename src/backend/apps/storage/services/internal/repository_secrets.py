from __future__ import annotations

import secrets
import re
import string
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.node.models.base import NodeRole
from apps.storage.repositories.models import Credential, Repository
from apps.storage.services.internal.repository_endpoints import repository_data_endpoint
from apps.storage.services.internal.s3_url_style import normalize_s3_url_style


SECRET_CONFIG_FIELDS = frozenset({"secret_access_key", "smb_password", "kopia_password"})
SECRET_VALUE_KEYS = frozenset(
    {
        "secret_access_key",
        "smb_password",
        "kopia_password",
        "password",
        "secret",
        "ciphertext",
    }
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(secret_access_key|smb_password|kopia_password|password|secret)(\s*[:=]\s*)([^,\s}\]]+)"
)


def generate_kopia_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(max(24, int(length))))


def sanitize_repository_config(config: dict | None) -> dict:
    normalized = dict(config or {})
    for key in SECRET_CONFIG_FIELDS:
        normalized.pop(key, None)
    return normalized


def storage_secret_config_fallback_enabled() -> bool:
    raw = getattr(settings, "STORAGE_SECRET_CONFIG_FALLBACK", True)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def build_credential_metadata(
    *,
    repository_type: str,
    config: dict | None,
    credential_payload: dict | None = None,
) -> dict:
    config = config if isinstance(config, dict) else {}
    credential_payload = credential_payload if isinstance(credential_payload, dict) else {}
    metadata: dict[str, Any] = {"repository_type": repository_type}
    access_key_id = credential_payload.get("access_key_id", config.get("access_key_id"))
    if access_key_id:
        metadata["access_key_id"] = str(access_key_id).strip()
    smb_username = credential_payload.get("smb_username", config.get("smb_username"))
    if smb_username:
        metadata["smb_username"] = str(smb_username).strip()
    smb_domain = credential_payload.get("smb_domain", config.get("smb_domain"))
    if smb_domain:
        metadata["smb_domain"] = str(smb_domain).strip()
    return metadata


def build_secret_payload(
    *,
    repository_type: str,
    nas_protocol: str | None = None,
    config: dict | None = None,
    credential_payload: dict | None = None,
    existing_secrets: dict | None = None,
) -> dict:
    config = config if isinstance(config, dict) else {}
    credential_payload = credential_payload if isinstance(credential_payload, dict) else {}
    existing_secrets = existing_secrets if isinstance(existing_secrets, dict) else {}
    payload = dict(existing_secrets)

    kopia_password = str(
        credential_payload.get("kopia_password")
        or config.get("kopia_password")
        or payload.get("kopia_password")
        or ""
    ).strip()
    if not kopia_password:
        kopia_password = generate_kopia_password()
    payload["kopia_password"] = kopia_password

    if repository_type == Repository.Type.S3:
        secret_access_key = credential_payload.get("secret_access_key", config.get("secret_access_key"))
        if secret_access_key is not None and str(secret_access_key) != "":
            payload["secret_access_key"] = str(secret_access_key)
    elif repository_type == Repository.Type.NAS and nas_protocol == Repository.NasProtocol.SMB:
        if "smb_password" in credential_payload and str(credential_payload.get("smb_password") or "").strip() == "":
            pass
        else:
            smb_password = credential_payload.get("smb_password", config.get("smb_password"))
            if smb_password is not None and str(smb_password) != "":
                payload["smb_password"] = str(smb_password)
    return payload


def credential_hint(repository: Repository) -> dict:
    try:
        credential = _credential_for_repository(repository)
    except ValidationError:
        credential = None
    if credential is None:
        configured = bool(_fallback_secret_payload(repository))
        return {"configured": configured, "type": None, "secret_hint": "******" if configured else ""}
    metadata = credential.metadata if isinstance(credential.metadata, dict) else {}
    hint: dict[str, Any] = {
        "configured": True,
        "type": credential.credential_type,
        "secret_hint": "******",
        "updated_at": credential.updated_at.isoformat() if credential.updated_at else None,
    }
    if metadata.get("access_key_id"):
        hint["access_key_id_hint"] = _mask_tail(str(metadata["access_key_id"]))
    if metadata.get("smb_username"):
        hint["smb_username"] = metadata["smb_username"]
    if metadata.get("smb_domain"):
        hint["smb_domain"] = metadata["smb_domain"]
    return hint


def resolve_repository_secrets(repository: Repository) -> dict:
    credential = _credential_for_repository(repository)
    if credential is not None:
        try:
            payload = credential.get_secret_payload()
        except Exception as exc:  # pragma: no cover - defensive wrapper around crypto backend
            raise ValidationError("Repository credential cannot be decrypted.") from exc
        return payload if isinstance(payload, dict) else {}
    if storage_secret_config_fallback_enabled():
        return _fallback_secret_payload(repository)
    raise ValidationError("Repository credential is not configured.")


def build_repository_runtime_payload(
    *,
    repository: Repository,
    execution_target: Any | None = None,
    repository_endpoint_type: object = "external",
    repository_endpoint: object = None,
    authorized_bind_node_id: int | None = None,
) -> dict[str, Any]:
    from apps.storage.services.internal.nas_repository import (
        nas_agent_repository_subdir,
        nas_proxy_repository_subdir,
        nas_repository_payload,
    )

    config = sanitize_repository_config(repository.config if isinstance(repository.config, dict) else {})
    secrets_payload = resolve_repository_secrets(repository)
    kopia_password = str(secrets_payload.get("kopia_password") or "").strip()
    if repository.repo_type == Repository.Type.S3:
        if repository.status != Repository.Status.CREATED:
            raise ValidationError({"repository_id": "S3 repository has not been initialized."})
        return {
            "id": repository.id,
            "type": Repository.Type.S3,
            "bucket": str(repository.s3_bucket or "").strip(),
            "region": str(config.get("region") or "").strip(),
            "endpoint": repository_data_endpoint(
                config,
                endpoint_type=repository_endpoint_type,
                endpoint=repository_endpoint,
            ),
            "prefix": str(config.get("prefix") or "").strip(),
            "access_key_id": str(config.get("access_key_id") or "").strip(),
            "secret_access_key": str(secrets_payload.get("secret_access_key") or "").strip(),
            "kopia_password": kopia_password,
            "use_tls": config.get("use_tls") is not False,
            "s3_url_style": normalize_s3_url_style(
                config.get("s3_url_style"), platform=repository.s3_platform
            ),
        }
    if repository.repo_type == Repository.Type.PROXY_FS:
        if execution_target is None:
            raise ValidationError({"repository_id": "Proxy filesystem repository is bound to a proxy node."})
        if execution_target.node.role != NodeRole.PROXY:
            raise ValidationError(
                {"repository_id": "Proxy filesystem repository must be accessed through its bound proxy node."}
            )
        expected_node_id = authorized_bind_node_id or repository.bind_node_id
        if int(expected_node_id or 0) != execution_target.node.id:
            raise ValidationError({"repository_id": "Proxy filesystem repository is bound to a different proxy node."})
        proxy_dir = str(config.get("proxy_node_dir") or "").strip()
        if not proxy_dir:
            raise ValidationError({"repository_id": "Proxy filesystem repository path is empty."})
        payload = {
            "id": repository.id,
            "type": Repository.Type.PROXY_FS,
            "path": proxy_dir,
            "kopia_password": kopia_password,
        }
        if str(config.get("proxy_fs_layout") or "") == "managed_subdir_v1":
            payload.update(
                {
                    "base_path": str(config.get("proxy_node_base_dir") or "").strip(),
                    "layout": "managed_subdir_v1",
                }
            )
        return payload
    if repository.repo_type == Repository.Type.NAS:
        if repository.bind_node_type == Repository.BindNodeType.PROXY:
            if execution_target is None:
                raise ValidationError({"repository_id": "NAS repository is bound to a proxy node."})
            if execution_target.node.role != NodeRole.PROXY:
                raise ValidationError(
                    {"repository_id": "NAS repository must be accessed through its bound proxy node."}
                )
            expected_node_id = authorized_bind_node_id or repository.bind_node_id
            if int(expected_node_id or 0) != execution_target.node.id:
                raise ValidationError({"repository_id": "NAS repository is bound to a different proxy node."})
            subdir = nas_proxy_repository_subdir(repository)
            node_id = execution_target.node.id
        else:
            if execution_target is None:
                node_id = int(repository.bind_node_id or 0) or None
            else:
                node_id = execution_target.node.id
            subdir = nas_agent_repository_subdir(node_id) if node_id else nas_proxy_repository_subdir(repository)
        return nas_repository_payload(
            repository=repository,
            subdir=subdir,
            node_id=node_id,
            secrets_payload=secrets_payload,
        )
    raise ValidationError({"repository_id": f"Repository type {repository.repo_type} is not supported for immediate backup."})


def scrub_secrets(value: Any, *, extra_values: list[str] | tuple[str, ...] | None = None) -> Any:
    replacements = [str(v) for v in (extra_values or []) if str(v or "")]
    if isinstance(value, dict):
        return {
            key: "******" if _is_secret_key(str(key)) else scrub_secrets(item, extra_values=replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_secrets(item, extra_values=replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_secrets(item, extra_values=replacements) for item in value)
    if isinstance(value, str):
        text = value
        for secret_value in replacements:
            if secret_value:
                text = text.replace(secret_value, "******")
        text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}******", text)
        return text
    return value


def secret_values_for_scrub(repository: Repository | None = None, secrets_payload: dict | None = None) -> list[str]:
    values: list[str] = []
    if repository is not None and isinstance(repository.config, dict):
        values.extend(str(v) for k, v in repository.config.items() if k in SECRET_CONFIG_FIELDS and v)
    if isinstance(secrets_payload, dict):
        values.extend(str(v) for v in secrets_payload.values() if v)
    return values


def _credential_for_repository(repository: Repository) -> Credential | None:
    if not repository.credential_id:
        return None
    credential = Credential.objects.filter(
        id=repository.credential_id,
        organization_id=repository.organization_id,
    ).first()
    if credential is None:
        raise ValidationError("Repository credential not found in this organization.")
    return credential


def _fallback_secret_payload(repository: Repository) -> dict:
    config = repository.config if isinstance(repository.config, dict) else {}
    payload = {
        key: value
        for key, value in config.items()
        if key in SECRET_CONFIG_FIELDS and value not in (None, "")
    }
    return payload


def _mask_tail(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "****"
    return f"{text[:4]}****{text[-4:]}" if len(text) > 8 else f"****{text[-4:]}"


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SECRET_VALUE_KEYS or "password" in lowered or "secret" in lowered
