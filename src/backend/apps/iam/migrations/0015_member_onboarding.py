from django.db import migrations, models


def seed_lock(apps, schema_editor):
    apps.get_model("iam", "IdentityProvisioningLock").objects.using(
        schema_editor.connection.alias
    ).get_or_create(key="local_identity")


class Migration(migrations.Migration):
    dependencies = [("iam", "0014_drop_membership_role_column")]
    operations = [
        migrations.AddField(
            model_name="profile",
            name="requires_password_change",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="IdentityProvisioningLock",
            fields=[
                (
                    "key",
                    models.CharField(max_length=32, primary_key=True, serialize=False),
                )
            ],
            options={"db_table": "iam_identity_provisioning_lock"},
        ),
        migrations.RunPython(seed_lock, migrations.RunPython.noop),
    ]
