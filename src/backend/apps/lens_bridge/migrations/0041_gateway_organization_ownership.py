from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_gateway_created_by(apps, schema_editor):
    LensGatewayLink = apps.get_model("lens_bridge", "LensGatewayLink")
    LensGatewayLink.objects.filter(
        created_by_id__isnull=True,
        owner_user_id__isnull=False,
    ).update(created_by_id=models.F("owner_user_id"))


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0040_session_analysis_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="lensgatewaylink",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="lens_gateway_links_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="lensgatewaylink",
            name="scope",
            field=models.CharField(
                choices=[
                    ("platform", "Platform"),
                    ("organization", "Organization"),
                    ("user", "Legacy organization"),
                ],
                db_index=True,
                default="user",
                max_length=16,
            ),
        ),
        migrations.RunPython(
            backfill_gateway_created_by,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="lensgatewaylink",
            name="lens_brgw_scope_owner_ck",
        ),
        migrations.AddConstraint(
            model_name="lensgatewaylink",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(scope="platform", owner_user_id__isnull=True)
                    | models.Q(scope="user", owner_user_id__isnull=False)
                    | models.Q(scope="organization")
                ),
                name="lens_brgw_scope_owner_ck",
            ),
        ),
    ]
