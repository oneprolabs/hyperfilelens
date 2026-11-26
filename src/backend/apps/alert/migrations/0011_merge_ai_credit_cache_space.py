"""Join independent upstream AI-credit and cache-warning migration branches."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("alerts", "0010_seed_ai_model_credit_rule"),
        ("alerts", "0009_cache_space_warning_rule"),
    ]

    operations = []
