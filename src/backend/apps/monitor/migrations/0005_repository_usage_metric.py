import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0004_deployment_host"),
        ("storage", "0018_repair_bound_nas_location_owners"),
    ]

    operations = [
        migrations.CreateModel(
            name="RepositoryUsageMetric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recorded_at", models.DateTimeField()),
                ("usage_bytes", models.BigIntegerField(blank=True, null=True)),
                (
                    "usage_source",
                    models.CharField(
                        blank=True,
                        choices=[("estimated", "Estimated"), ("provider", "Provider reported")],
                        max_length=20,
                        null=True,
                    ),
                ),
                ("object_count", models.BigIntegerField(blank=True, null=True)),
                (
                    "repository",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="usage_metrics",
                        to="storage.repository",
                    ),
                ),
            ],
            options={
                "db_table": "monitor_repository_usage_metrics",
                "ordering": ["repository_id", "-recorded_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="repositoryusagemetric",
            constraint=models.UniqueConstraint(
                fields=("repository", "recorded_at"),
                name="mon_repo_usage_repo_at_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="repositoryusagemetric",
            constraint=models.CheckConstraint(
                condition=Q(("usage_bytes__gte", 0), ("usage_bytes__isnull", True), _connector="OR"),
                name="mon_repo_usage_bytes_nonneg",
            ),
        ),
        migrations.AddConstraint(
            model_name="repositoryusagemetric",
            constraint=models.CheckConstraint(
                condition=Q(("object_count__gte", 0), ("object_count__isnull", True), _connector="OR"),
                name="mon_repo_object_count_nonneg",
            ),
        ),
        migrations.AddIndex(
            model_name="repositoryusagemetric",
            index=models.Index(
                fields=["repository", "-recorded_at"],
                name="mon_repo_usage_repo_at_idx",
            ),
        ),
    ]
