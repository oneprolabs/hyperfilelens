from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("node", "0022_platform_node_ownership"),
        ("lens_bridge", "0048_platform_gateway_ownership"),
    ]
    operations = [
        migrations.AddConstraint(
            model_name="nodetoken",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(organization__isnull=False) & ~models.Q(gateway_scope="platform")
                    | (
                        models.Q(organization__isnull=True, gateway_scope="platform", role="gateway")
                        & ~models.Q(transport_namespace="")
                    )
                ),
                name="node_token_resource_owner_ck",
            ),
        ),
    ]
