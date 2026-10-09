from django.db import migrations, models
import django.db.models.deletion
import django.db.models.functions.text


class Migration(migrations.Migration):
    dependencies = [("source", "0013_source_resource_probing_status")]

    operations = [
        migrations.CreateModel(
            name="SourceTag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="iam.organization")),
            ],
            options={"db_table": "source_tag"},
        ),
        migrations.CreateModel(
            name="SourceTagAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_kind", models.CharField(max_length=16)),
                ("ref_id", models.BigIntegerField()),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="iam.organization")),
                ("tag", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assignments", to="source.sourcetag")),
            ],
            options={"db_table": "source_tag_assignment"},
        ),
        migrations.AddConstraint(
            model_name="sourcetag",
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower("name"), "organization", name="uniq_source_tag_org_name"),
        ),
        migrations.AddConstraint(
            model_name="sourcetagassignment",
            constraint=models.UniqueConstraint(fields=("organization", "tag", "source_kind", "ref_id"), name="uniq_source_tag_assignment"),
        ),
        migrations.AddIndex(
            model_name="sourcetagassignment",
            index=models.Index(fields=["organization", "source_kind", "ref_id"], name="src_tag_org_source_idx"),
        ),
    ]
