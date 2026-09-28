from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0044_lens_run_submission_agent_rounds"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="lensorgmodellink",
            name="uniq_lens_borgmdl_org_mgmt_key",
        ),
        migrations.RemoveField(
            model_name="lensorgmodellink",
            name="management_key",
        ),
        migrations.RemoveField(
            model_name="lensorgmodellink",
            name="deployment_role",
        ),
        migrations.RemoveField(
            model_name="lensorgmodellink",
            name="is_deployment_history",
        ),
        migrations.RemoveField(
            model_name="lensorgmodellink",
            name="deployment_fingerprint",
        ),
    ]
