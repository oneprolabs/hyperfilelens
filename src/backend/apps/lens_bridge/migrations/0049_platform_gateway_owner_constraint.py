from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0048_platform_gateway_ownership"),
    ]
    operations = [
        migrations.RemoveConstraint(
            model_name="lensgatewaylink",
            name="uniq_lens_brgw_org_default",
        ),
        migrations.AddConstraint(
            model_name="lensgatewaylink",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(scope="platform", organization__isnull=True)
                    | models.Q(scope__in=["organization", "user"], organization__isnull=False)
                ),
                name="gateway_scope_organization_ck",
            ),
        ),
    ]
