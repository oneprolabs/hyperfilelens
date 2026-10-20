"""Separate platform AI ownership without changing gateway execution identities.

The legacy organization remains until the gateway migration has moved its
remaining references. No external service calls or resource recreation occur.
"""

import django.db.models.deletion
from django.db import migrations, models

from common.deploy.migration_safety import require_exclusive_platform_ownership_v1


LEGACY_PLATFORM_ORG_KEY = "__platform_lens__"
DEFAULT_FIELDS = ("default_agent_model_ref", "default_multimodal_model_ref")


def move_platform_ai(apps, schema_editor):
    alias = schema_editor.connection.alias
    Organization = apps.get_model("iam", "Organization")
    ModelLink = apps.get_model("lens_bridge", "LensOrgModelLink")
    OrgSettings = apps.get_model("lens_bridge", "LensOrgLink")
    PlatformSettings = apps.get_model("lens_bridge", "PlatformAISettings")
    legacy_org = Organization.objects.using(alias).filter(
        key=LEGACY_PLATFORM_ORG_KEY,
    ).first()
    if legacy_org is None:
        return

    links = ModelLink._base_manager.using(alias).filter(
        organization_id=legacy_org.pk,
    )
    old_settings = OrgSettings._base_manager.using(alias).filter(
        organization_id=legacy_org.pk,
    ).first()
    defaults = {
        field: getattr(old_settings, field) if old_settings else None
        for field in DEFAULT_FIELDS
    }
    owned_refs = set(links.values_list("sl_config_uuid", flat=True))
    for field, reference in defaults.items():
        if reference is not None and reference not in owned_refs:
            raise RuntimeError(
                f"Cannot migrate platform AI: {field} does not reference a "
                "platform-owned model. Repair the saved default before upgrading."
            )
    existing = PlatformSettings.objects.using(alias).filter(pk=1).first()
    if existing and any(
        getattr(existing, field) is not None
        and reference is not None
        and getattr(existing, field) != reference
        for field, reference in defaults.items()
    ):
        raise RuntimeError("Cannot migrate conflicting platform AI defaults.")

    settings, _ = PlatformSettings.objects.using(alias).get_or_create(pk=1)
    for field, reference in defaults.items():
        if reference is not None:
            setattr(settings, field, reference)
    settings.save(using=alias, update_fields=[*DEFAULT_FIELDS, "updated_at"])
    links.update(scope="platform", organization_id=None)
    if old_settings:
        for field in DEFAULT_FIELDS:
            setattr(old_settings, field, None)
        old_settings.save(using=alias, update_fields=[*DEFAULT_FIELDS, "updated_at"])


def restore_platform_ai(apps, schema_editor):
    """Restore the old representation only when rolling back this migration."""
    alias = schema_editor.connection.alias
    Organization = apps.get_model("iam", "Organization")
    ModelLink = apps.get_model("lens_bridge", "LensOrgModelLink")
    OrgSettings = apps.get_model("lens_bridge", "LensOrgLink")
    PlatformSettings = apps.get_model("lens_bridge", "PlatformAISettings")
    links = ModelLink._base_manager.using(alias).filter(scope="platform")
    defaults = PlatformSettings.objects.using(alias).filter(pk=1).first()
    if not links.exists() and not (
        defaults and any(getattr(defaults, field) for field in DEFAULT_FIELDS)
    ):
        return
    legacy_org, _ = Organization.objects.using(alias).get_or_create(
        key=LEGACY_PLATFORM_ORG_KEY,
        defaults={"name": "Platform Lens", "is_active": True},
    )
    old_settings, _ = OrgSettings._base_manager.using(alias).get_or_create(
        organization_id=legacy_org.pk,
    )
    for field in DEFAULT_FIELDS:
        reference = getattr(defaults, field) if defaults else None
        current = getattr(old_settings, field)
        if current is not None and current != reference:
            raise RuntimeError("Cannot roll back conflicting platform AI defaults.")
        setattr(old_settings, field, reference)
    old_settings.save(using=alias, update_fields=[*DEFAULT_FIELDS, "updated_at"])
    links.update(scope="organization", organization_id=legacy_org.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0045_remove_deployment_model_metadata"),
    ]

    operations = [
        migrations.RunPython(
            require_exclusive_platform_ownership_v1, migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name="PlatformAISettings",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, editable=False, primary_key=True, serialize=False,
                    ),
                ),
                ("default_agent_model_ref", models.UUIDField(blank=True, null=True)),
                ("default_multimodal_model_ref", models.UUIDField(blank=True, null=True)),
            ],
            options={"db_table": "lens_bridge_platform_ai_settings"},
        ),
        migrations.AddField(
            model_name="lensorgmodellink",
            name="scope",
            field=models.CharField(
                choices=[("platform", "Platform"), ("organization", "Organization")],
                db_index=True, default="organization", max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="lensorgmodellink",
            name="organization",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                to="iam.organization",
            ),
        ),
        migrations.RunPython(move_platform_ai, restore_platform_ai),
        migrations.AddConstraint(
            model_name="lensorgmodellink",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(scope="platform", organization__isnull=True)
                    | models.Q(scope="organization", organization__isnull=False)
                ),
                name="ai_model_scope_organization_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="platformaisettings",
            constraint=models.CheckConstraint(
                condition=models.Q(id=1), name="platform_ai_settings_singleton",
            ),
        ),
    ]
