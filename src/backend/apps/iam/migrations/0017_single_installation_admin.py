"""Keep the trusted installation identity unique without guessing on upgrade."""

from django.db import migrations, models


def check_installation_identity(apps, schema_editor):
    profiles = apps.get_model("iam", "Profile")
    ids = list(profiles.objects.using(schema_editor.connection.alias).filter(
        is_installation_admin=True,
    ).values_list("user_id", flat=True))
    if len(ids) > 1:
        raise RuntimeError(
            "Multiple trusted installation administrators exist "
            f"(user IDs: {ids}). Resolve the identities offline before upgrading."
        )


class Migration(migrations.Migration):
    dependencies = [("iam", "0016_installation_admin_identity")]

    operations = [
        migrations.RunPython(check_installation_identity, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="profile",
            constraint=models.UniqueConstraint(
                fields=("is_installation_admin",),
                condition=models.Q(is_installation_admin=True),
                name="iam_single_installation_admin",
            ),
        ),
    ]
