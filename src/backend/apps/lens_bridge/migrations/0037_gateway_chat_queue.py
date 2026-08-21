import django.db.models.deletion
import uuid

from django.db import migrations, models


def initialize_queue_times(apps, schema_editor):
    session_model = apps.get_model("lens_bridge", "LensSessionLink")
    session_model.objects.filter(
        lifecycle_status="provisioning",
        gateway_queue_entered_at__isnull=True,
    ).update(gateway_queue_entered_at=models.F("created_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0036_lens_session_share_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensgatewaylink",
            name="chat_prepare_concurrency",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="lensgatewaylink",
            name="chat_queue_capacity",
            field=models.PositiveIntegerField(default=10),
        ),
        migrations.AddField(
            model_name="lenssessionlink",
            name="gateway_queue_entered_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="LensGatewayChatSlot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("slot_number", models.PositiveSmallIntegerField()),
                ("session_generation", models.PositiveBigIntegerField()),
                (
                    "lease_token",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("acquired_at", models.DateTimeField()),
                ("heartbeat_at", models.DateTimeField(db_index=True)),
                (
                    "gateway_link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_prepare_slots",
                        to="lens_bridge.lensgatewaylink",
                    ),
                ),
                (
                    "session_link",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="gateway_chat_slot",
                        to="lens_bridge.lenssessionlink",
                    ),
                ),
            ],
            options={"db_table": "lens_bridge_gateway_chat_slot"},
        ),
        migrations.AddConstraint(
            model_name="lensgatewaylink",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("chat_prepare_concurrency__gte", 1),
                    ("chat_prepare_concurrency__lte", 32),
                ),
                name="lens_brgw_chat_conc_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="lensgatewaylink",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("chat_queue_capacity__gte", 0),
                    ("chat_queue_capacity__lte", 1000),
                ),
                name="lens_brgw_chat_queue_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="lensgatewaychatslot",
            constraint=models.UniqueConstraint(
                fields=("gateway_link", "slot_number"),
                name="uniq_lens_brgw_chat_slot",
            ),
        ),
        migrations.AddIndex(
            model_name="lensgatewaychatslot",
            index=models.Index(
                fields=["gateway_link", "heartbeat_at"],
                name="lens_brgw_chat_hb_idx",
            ),
        ),
        migrations.RunPython(initialize_queue_times, migrations.RunPython.noop),
    ]
