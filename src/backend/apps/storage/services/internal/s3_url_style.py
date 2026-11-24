from __future__ import annotations

from apps.storage.repositories.models import Repository


S3_URL_STYLE_AUTO = "auto"
S3_URL_STYLE_VIRTUAL_HOSTED = "virtual_hosted"
S3_URL_STYLE_PATH = "path"
S3_URL_STYLES = frozenset(
    {S3_URL_STYLE_AUTO, S3_URL_STYLE_VIRTUAL_HOSTED, S3_URL_STYLE_PATH}
)


def default_s3_url_style(platform: str | None) -> str:
    if str(platform or "").strip().lower() == Repository.S3Platform.HUAWEICLOUD:
        return S3_URL_STYLE_VIRTUAL_HOSTED
    if str(platform or "").strip().lower() == Repository.S3Platform.MINIO:
        return S3_URL_STYLE_PATH
    return S3_URL_STYLE_AUTO


def normalize_s3_url_style(value: object, *, platform: str | None = None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default_s3_url_style(platform)
    if normalized not in S3_URL_STYLES:
        raise ValueError(
            "S3 URL style must be one of: auto, virtual_hosted, path."
        )
    return normalized


def kopia_s3_url_style(value: object, *, platform: str | None = None) -> str:
    normalized = normalize_s3_url_style(value, platform=platform)
    if normalized == S3_URL_STYLE_VIRTUAL_HOSTED:
        return "virtual-hosted"
    return normalized


def boto3_s3_addressing_style(value: object, *, platform: str | None = None) -> str:
    normalized = normalize_s3_url_style(value, platform=platform)
    if normalized == S3_URL_STYLE_VIRTUAL_HOSTED:
        return "virtual"
    return normalized
