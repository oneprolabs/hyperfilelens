import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("node", "0013_node_repository_server_address"),
        ("task", "0012_task_blocked_idempotency_and_dependency_checks"),
    ]

    operations = [
        migrations.AddField(
            model_name="nodetask",
            name="parent_task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="node_tasks",
                to="task.task",
            ),
        ),
    ]
