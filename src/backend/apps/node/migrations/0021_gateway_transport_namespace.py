"""Preserve installed public Gateway routing identities before moving ownership."""

from django.db import migrations, models
from django.db.models import F

from common.deploy.migration_safety import require_exclusive_platform_ownership_v1


def snapshot_gateway_namespaces(apps, schema_editor):
    alias = schema_editor.connection.alias
    Node = apps.get_model("node", "Node")
    Token = apps.get_model("node", "NodeToken")
    Link = apps.get_model("lens_bridge", "LensGatewayLink")
    Organization = apps.get_model("iam", "Organization")
    platform_links = Link._base_manager.using(alias).filter(scope="platform")
    if platform_links.exclude(gateway__role="gateway").exists():
        raise RuntimeError("A public Gateway link does not reference a Gateway Node.")
    if Token._base_manager.using(alias).filter(
        gateway_scope="platform",
    ).exclude(role="gateway").exists():
        raise RuntimeError("A platform enrollment token has an invalid Node role.")
    if platform_links.exclude(
        organization_id=F("gateway__organization_id"),
    ).exists():
        raise RuntimeError("Cannot migrate inconsistent public Gateway ownership.")

    organization_ids = set(platform_links.values_list("organization_id", flat=True))
    organization_ids.update(
        Token._base_manager.using(alias).filter(
            gateway_scope="platform", role="gateway",
        ).values_list("organization_id", flat=True),
    )
    for org in Organization.objects.using(alias).filter(pk__in=organization_ids):
        if not org.key:
            raise RuntimeError("Cannot snapshot an empty public Gateway namespace.")
        Node._base_manager.using(alias).filter(
            pk__in=platform_links.filter(organization_id=org.pk).values("gateway_id"),
            transport_namespace="",
        ).update(transport_namespace=org.key)
        Token._base_manager.using(alias).filter(
            organization_id=org.pk, gateway_scope="platform", role="gateway",
            transport_namespace="",
        ).update(transport_namespace=org.key)


class Migration(migrations.Migration):
    dependencies = [
        ("node", "0020_backfill_upgrade_operation_tasks"),
        ("lens_bridge", "0046_platform_ai_ownership"),
    ]
    operations = [
        migrations.RunPython(
            require_exclusive_platform_ownership_v1, migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name="node",
            name="transport_namespace",
            field=models.CharField(
                blank=True, db_index=True, default="", editable=False,
                help_text="Stable control-plane routing namespace for a public Gateway.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="nodetoken",
            name="transport_namespace",
            field=models.CharField(
                blank=True, db_index=True, default="", editable=False,
                help_text="Stable control-plane routing namespace authorized by this token.",
                max_length=128,
            ),
        ),
        migrations.RunPython(snapshot_gateway_namespaces, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="node",
            constraint=models.CheckConstraint(
                condition=models.Q(transport_namespace="") | models.Q(role="gateway"),
                name="node_transport_namespace_gateway",
            ),
        ),
        migrations.AddConstraint(
            model_name="nodetoken",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(transport_namespace="")
                    | models.Q(role="gateway", gateway_scope="platform")
                ),
                name="node_token_transport_platform",
            ),
        ),
    ]
