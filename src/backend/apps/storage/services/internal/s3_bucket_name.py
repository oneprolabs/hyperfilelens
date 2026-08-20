from __future__ import annotations

import ipaddress
import re


_DNS_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_ALIYUN_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_DNS_STYLE_PLATFORMS = {"aws", "huaweicloud"}


def s3_bucket_name_error(*, platform: object, bucket: object) -> str:
    """Return a safe product-facing error for managed new-bucket names.

    Custom providers intentionally remain provider-authoritative because an
    S3-compatible service may implement naming rules that differ from the
    managed Provider Catalog.
    """

    normalized_platform = str(platform or "").strip().lower()
    name = str(bucket or "").strip()
    if normalized_platform == "aliyun":
        if not _ALIYUN_BUCKET_RE.fullmatch(name):
            return (
                "Bucket names must be 3–63 characters and use only lowercase "
                "letters, numbers, and hyphens. They must start and end with "
                "a letter or number."
            )
        return ""
    if normalized_platform not in _DNS_STYLE_PLATFORMS:
        return ""
    if not _DNS_BUCKET_RE.fullmatch(name) or ".." in name:
        return (
            "Bucket names must be 3–63 characters and use only lowercase "
            "letters, numbers, hyphens, and periods. They must start and end "
            "with a letter or number."
        )
    if any(not label or label[0] == "-" or label[-1] == "-" for label in name.split(".")):
        return "Each part of the bucket name must start and end with a letter or number."
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return ""
    return "Bucket names cannot be formatted as an IP address."


__all__ = ["s3_bucket_name_error"]
