"""AI default settings for a real tenant or the platform itself."""

from apps.iam.models import Organization
from apps.lens_bridge.models import LensOrgLink, PlatformAISettings


def get_model_defaults(
    organization: Organization | None,
) -> LensOrgLink | PlatformAISettings:
    """Resolve model settings; None explicitly selects platform ownership.

    Platform selection is for trusted internal callers. Request handlers must
    authorize their target scope before calling this service.
    """
    if organization is None:
        settings, _ = PlatformAISettings.objects.get_or_create(pk=1)
        return settings

    from apps.lens_bridge.services.provisioning import get_or_create_org_link

    return get_or_create_org_link(organization)
