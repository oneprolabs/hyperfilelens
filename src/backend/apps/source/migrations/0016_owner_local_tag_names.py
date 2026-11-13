from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("source", "0015_source_tag_details")]
    operations = [
        migrations.RemoveConstraint(
            model_name="sourcetag",
            name="uniq_source_tag_org_name",
        ),
    ]
