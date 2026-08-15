"""
Public model imports for the storage domain.

Prefer importing from subdomains directly (e.g. `apps.storage.repositories.models`).
This module exists as a convenience façade for cross-domain references.
"""

from apps.storage.repositories.models import (
    Credential,
    Repository,
    RepositoryExecutionTarget,
    RepositoryDeploymentIdentity,
    RepositoryLocationClaim,
    RepositoryLocationNamespace,
    RepositoryMaintenanceState,
    RepositoryTask,
    RepositoryUsageShard,
)
from apps.storage.provider_catalog.models import (
    StorageProviderOverride,
    StorageProviderRegionValidation,
    StorageProviderValidationRun,
)

__all__ = [
    "Credential",
    "Repository",
    "RepositoryExecutionTarget",
    "RepositoryDeploymentIdentity",
    "RepositoryLocationClaim",
    "RepositoryLocationNamespace",
    "RepositoryMaintenanceState",
    "RepositoryTask",
    "RepositoryUsageShard",
    "StorageProviderOverride",
    "StorageProviderRegionValidation",
    "StorageProviderValidationRun",
]
