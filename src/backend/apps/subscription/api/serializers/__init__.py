"""Subscription API serializers (OSS + EE via extend_path)."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from apps.subscription.api.serializers.license import (
    ActivateLicenseSerializer,
    LicenseHistorySerializer,
    LicenseSerializer,
    MachineCodeSerializer,
    ValidateQuotaQuerySerializer,
)

__all__ = [
    "LicenseSerializer",
    "LicenseHistorySerializer",
    "MachineCodeSerializer",
    "ActivateLicenseSerializer",
    "ValidateQuotaQuerySerializer",
]
