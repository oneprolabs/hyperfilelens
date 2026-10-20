"""Versioned guard for the platform-ownership representation boundary."""

import os


def require_exclusive_platform_ownership_v1(apps, schema_editor) -> None:
    """Reject an initialized database unless the installer stopped old writers.

    Empty databases can migrate normally on first installation. The approval is
    supplied only to the singleton migration container, never to runtime APIs.
    This guard also protects upgrades invoked through an older installer.
    """
    if os.environ.get("HFL_EXCLUSIVE_PLATFORM_MIGRATION") == "1":
        return
    alias = schema_editor.connection.alias
    organization = apps.get_model("iam", "Organization")
    membership = apps.get_model("iam", "Membership")
    user = membership._meta.get_field("user").remote_field.model
    if (
        organization._base_manager.using(alias).exists()
        or user._base_manager.using(alias).exists()
    ):
        raise RuntimeError(
            "Platform ownership requires an exclusive migration window. "
            "Run upgrade using the TARGET release's install.sh so old API, "
            "worker and scheduler containers are stopped before migration. "
            "Do not approve this migration while old application writers run."
        )
