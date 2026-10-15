"""Stable, credential-free reconciliation diagnostics."""

from django.core.exceptions import ValidationError

from apps.storage.services.internal.kopia_cli import KopiaCliError
from apps.storage.services.internal.repository_ownership import RepositoryOwnershipError


class OrphanReconciliationError(Exception):
    def __init__(self, code, message, *, execution_unknown=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.execution_unknown = execution_unknown


def classify_reconciliation_error(error, stage):
    """Never echo CLI output or arbitrary exceptions that can contain secrets."""
    if isinstance(error, OrphanReconciliationError):
        code, message = error.code, error.message
    elif isinstance(error, RepositoryOwnershipError):
        code = "ORPHAN_OWNERSHIP_UNVERIFIED"
        message = "Verify the physical repository ownership before manually retrying reconciliation."
    elif isinstance(error, (ConnectionError, TimeoutError, KopiaCliError, OSError)):
        code = "ORPHAN_STORAGE_UNAVAILABLE"
        message = "Repository access failed. Check owner connectivity and storage access; the inventory check retries once."
    elif isinstance(error, ValidationError):
        code = "ORPHAN_CONFIGURATION_INVALID"
        message = "Repository access configuration is invalid. Correct it before manually retrying reconciliation."
    elif isinstance(error, ValueError):
        code = "ORPHAN_INVENTORY_INVALID"
        message = "A complete snapshot inventory could not be verified. No unconfirmed snapshot will be deleted."
    else:
        code = "ORPHAN_INTERNAL_ERROR"
        message = "An internal reconciliation error occurred. Inspect the error type and stage; the inventory check retries once before ending this cycle."
    return {
        "error_code": code,
        "message": message,
        "stage": stage,
        "exception_type": type(error).__name__,
        "execution_unknown": (
            error.execution_unknown if isinstance(error, OrphanReconciliationError) else False
        ),
    }
