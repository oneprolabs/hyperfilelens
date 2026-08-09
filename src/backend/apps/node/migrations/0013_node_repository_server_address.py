from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("node", "0012_node_operation_failure_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="repository_server_address",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Optional source-reachable address advertised by a Proxy "
                    "Repository Server."
                ),
                max_length=253,
            ),
        ),
    ]
