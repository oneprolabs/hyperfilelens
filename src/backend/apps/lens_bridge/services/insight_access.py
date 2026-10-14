"""Trusted access checks for snapshot data and selected Private Gateways."""

from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.lens_bridge.models import LensGatewayLink
from apps.protection.models import BackupSourceSnapshot
from common.extension_spi import get_authz_provider


def filter_knowledge_sources(request, queryset, *, action):
    """Authorize artifact lineage before count/page, including private Chats."""
    from django.db.models import Exists, F, OuterRef, Q, Subquery
    from apps.iam.query_authorization import request_authorization
    from apps.lens_bridge.models import LensSessionLink
    from apps.node.models import Node
    from apps.protection.models import BackupConfig

    if get_authz_provider() is None or (
        getattr(request, "hfl_support_readonly", False) and request.method in ("GET", "HEAD", "OPTIONS")
    ):
        return queryset
    context = request_authorization(request, fresh=request.method not in ("GET", "HEAD", "OPTIONS"))
    if context is None or not context.valid:
        return queryset.none()
    read = request.method in ("GET", "HEAD", "OPTIONS")
    gateways = context.filter_queryset(
        Node.objects.filter(role="gateway"), "node",
        action="insights.private_data_gateways.view" if read else "insights.private_data_gateways.use",
    )
    configs = context.filter_queryset(BackupConfig.objects.all(), "backup_config", action=action)
    configs = context.filter_queryset(configs, "backup_config", action="protection.backup_data.browse")
    snapshots = BackupSourceSnapshot.objects.filter(
        organization_id=context.org.pk, pk=OuterRef("backup_source_snapshot_id"),
        backup_config_id__in=configs.values("pk"),
    )
    private_gateway = Q(
        gateway_link__organization_id=context.org.pk,
        gateway_link__scope__in=("user", "organization"),
        gateway_id__in=gateways.values("pk"),
    )
    public_gateway = Q(gateway_link__scope="platform", gateway__is_deleted=False, gateway__role="gateway")
    local_gateways = context.filter_queryset(gateways, "node", action=action)
    lineage = (
        Q(backup_source_snapshot_id__isnull=False) & Q(Exists(snapshots))
        & (private_gateway | public_gateway)
    ) | (
        Q(backup_source_snapshot_id__isnull=True) & private_gateway
        & Q(gateway_id__in=local_gateways.values("pk"))
    )
    foreign_chat = LensSessionLink.objects.filter(
        knowledge_source_id=OuterRef("pk"),
    ).exclude(hfl_user_id=request.user.pk)
    original_config = BackupSourceSnapshot.objects.filter(
        organization_id=context.org.pk, pk=OuterRef(OuterRef("backup_source_snapshot_id")),
    ).values("backup_config_id")[:1]
    pinned = BackupSourceSnapshot.objects.filter(
        organization_id=context.org.pk, pk=OuterRef("pinned_snapshot_id"),
        backup_config_id=Subquery(original_config),
        backup_config_id__in=configs.values("pk"),
    )
    return context.fence_queryset(queryset.filter(
        organization_id=context.org.pk, gateway_link__gateway_id=F("gateway_id"),
    ).filter(lineage).filter(
        Q(pinned_snapshot_id__isnull=True) | Q(Exists(pinned)),
    ).filter(~Q(Exists(foreign_chat))))


def assert_knowledge_source_inputs(request, *, organization, gateway_link, snapshot_id=None, pinned_id=None):
    """Creation and pin changes recheck actual inputs before scheduling work."""
    from apps.iam.query_authorization import request_authorization
    if get_authz_provider() is None:
        return
    context = request_authorization(request, fresh=True)
    if context is None or not context.valid or not context.has_action("backup_config", "insights.chats.use"):
        raise PermissionDenied("Knowledge source access is not granted.")
    assert_insight_access(
        organization=organization, user=request.user,
        snapshot_id=snapshot_id, gateway_link_id=gateway_link.pk,
    )
    if not snapshot_id:
        if not context.visible_refs([("node", gateway_link.gateway_id)], action="insights.chats.use"):
            raise PermissionDenied("Knowledge source access is not granted.")
    if pinned_id:
        pinned = BackupSourceSnapshot.objects.filter(organization_id=organization.pk, pk=pinned_id).first()
        original = BackupSourceSnapshot.objects.filter(organization_id=organization.pk, pk=snapshot_id).first()
        if pinned is None or original is None or pinned.backup_config_id != original.backup_config_id:
            raise ValidationError({"pinned_snapshot_id": "Select a snapshot of the same backup configuration."})
        assert_insight_access(
            organization=organization, user=request.user,
            snapshot_id=pinned_id, gateway_link_id=gateway_link.pk,
        )


def assert_insight_access(
    *, organization, user, snapshot_id=None, gateway_link_id=None
):
    provider = get_authz_provider()
    if provider is None:
        return
    refs = []

    def checked_id(value):
        if isinstance(value, bool):
            raise ValidationError("Invalid resource ID.")
        try:
            pk = int(value)
        except (TypeError, ValueError):
            raise ValidationError("Invalid resource ID.") from None
        if pk <= 0:
            raise ValidationError("Invalid resource ID.")
        return pk

    if snapshot_id:
        snapshot = BackupSourceSnapshot.objects.filter(
            organization_id=organization.id,
            pk=checked_id(snapshot_id),
        ).first()
        if snapshot is None:
            raise PermissionDenied("Snapshot access denied.")
        refs.append(("backup_config", snapshot.backup_config_id))
    if gateway_link_id:
        link = LensGatewayLink.objects.filter(pk=checked_id(gateway_link_id)).first()
        if link is None:
            raise PermissionDenied("Gateway access denied.")
        if link.scope != LensGatewayLink.GatewayScope.PLATFORM:
            if link.organization_id != organization.id:
                raise PermissionDenied("Gateway access denied.")
            refs.append(("node", link.gateway_id))
    if refs:
        allowed = set()
        for ref in refs:
            action = (
                "insights.private_data_gateways.use"
                if ref[0] == "node"
                else "insights.chats.use"
            )
            allowed.update(
                provider.visible_resource_refs(
                    user, organization.key, [ref], action=action
                )
            )
            if ref[0] != "node":
                if not provider.visible_resource_refs(
                    user,
                    organization.key,
                    [ref],
                    action="protection.backup_data.browse",
                ):
                    raise PermissionDenied("Backup data access is not granted.")
        if set(refs) - allowed:
            raise PermissionDenied(
                "The selected data or Gateway is outside your authorized resource range."
            )
