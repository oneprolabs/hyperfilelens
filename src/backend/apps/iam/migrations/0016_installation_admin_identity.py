"""Record trusted installation administrators, never infer them from email."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("iam", "0015_member_onboarding")]
    operations = [
        migrations.AddField(
            model_name="profile",
            name="is_installation_admin",
            field=models.BooleanField(default=False, editable=False),
        ),
    ]
