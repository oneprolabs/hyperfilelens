from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.protection.models import BackupConfig, BackupPolicy, BackupSourceSnapshot
from apps.protection.services.backup_source_snapshot import create_source_snapshot
from apps.protection.services.policy_execution import (
    _schedule_fire_key,
    cron_matches_now,
    retention_delete_candidates_for_config,
    schedule_matches_now,
)
from apps.storage.repositories.models import Repository


class PolicyExecutionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="policy-exec@test.local",
            email="policy-exec@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="policy-exec-org", name="Policy Exec Org")
        Membership.objects.create(user=self.user, organization=self.org, role=Membership.Role.ADMIN)
        self.agent = Node.objects.create(
            organization=self.org,
            name="policy-exec-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="policy-exec-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="policy-exec-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "cn-test-1",
                "prefix": "kopia/policy",
                "access_key_id": "ak",
                "secret_access_key": "sk",
                "kopia_password": "123456",
                "use_tls": False,
            },
        )
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Policy exec config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=self.repository.id,
        )
        self.policy = BackupPolicy.objects.create(
            organization_id=self.org.id,
            name="Keep latest only",
            is_active=True,
            schedule={"enabled": True, "cron_expr": "*/5 * * * *"},
            retention={
                "enabled": True,
                "recent_points": 1,
                "hourly_enabled": False,
                "hourly_hours": 1,
                "daily_enabled": False,
                "daily_days": 1,
                "weekly_enabled": False,
                "weekly_weeks": 1,
                "monthly_enabled": False,
                "monthly_months": 1,
                "annual_enabled": False,
                "annual_years": 1,
            },
            throttling={"enabled": False, "unlimited": True, "rate_mbps": 0},
            error_handling={"enabled": False},
        )

    def _snapshot(self, key: str, created_at):
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=1,
            task_uuid="00000000-0000-0000-0000-000000000001",
            idempotency_key=key,
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=1,
        )
        BackupSourceSnapshot.objects.filter(id=snapshot.id).update(
            created_at=created_at,
            started_at=created_at,
            finished_at=created_at,
        )
        snapshot.refresh_from_db()
        return snapshot

    def test_cron_matches_simple_step_expression(self):
        now = datetime(2026, 6, 26, 10, 15, tzinfo=timezone.get_current_timezone())
        self.assertTrue(cron_matches_now("*/5 * * * *", now=now))
        self.assertFalse(cron_matches_now("*/10 * * * *", now=now))

    def test_structured_schedules_use_local_time_and_start_gate(self):
        daily = {
            "enabled": True,
            "mode": "daily",
            "timezone": "Asia/Shanghai",
            "starts_at": "2026-07-31T09:30",
            "time": "09:30",
            "cron_expr": "30 9 * * *",
        }

        self.assertFalse(
            schedule_matches_now(daily, now=datetime(2026, 7, 30, 1, 30, tzinfo=UTC))
        )
        self.assertTrue(
            schedule_matches_now(daily, now=datetime(2026, 7, 31, 1, 30, tzinfo=UTC))
        )
        self.assertFalse(
            schedule_matches_now(daily, now=datetime(2026, 7, 31, 9, 30, tzinfo=UTC))
        )

    def test_weekly_and_month_end_schedules_match_selected_calendar_days(self):
        weekly = {
            "enabled": True,
            "mode": "weekly",
            "timezone": "UTC",
            "starts_at": None,
            "time": "09:15",
            "weekdays": [1, 3, 5],
            "cron_expr": "15 9 * * 1,3,5",
        }
        monthly = {
            "enabled": True,
            "mode": "monthly",
            "timezone": "UTC",
            "starts_at": None,
            "time": "08:00",
            "month_days": [31],
            "month_end": True,
            "cron_expr": "0 8 31 * *",
        }

        self.assertTrue(
            schedule_matches_now(weekly, now=datetime(2026, 7, 31, 9, 15, tzinfo=UTC))
        )
        self.assertFalse(
            schedule_matches_now(weekly, now=datetime(2026, 8, 1, 9, 15, tzinfo=UTC))
        )
        self.assertTrue(
            schedule_matches_now(monthly, now=datetime(2026, 2, 28, 8, 0, tzinfo=UTC))
        )
        self.assertFalse(
            schedule_matches_now(monthly, now=datetime(2026, 2, 27, 8, 0, tzinfo=UTC))
        )

    def test_interval_anchor_and_legacy_cron_remain_compatible(self):
        interval = {
            "enabled": True,
            "mode": "interval",
            "timezone": "Asia/Shanghai",
            "starts_at": "2026-07-31T02:30",
            "interval_unit": "hour",
            "interval_value": 6,
            "cron_expr": "0 */6 * * *",
        }
        legacy = {"enabled": True, "cron_expr": "0 2 * * *"}

        self.assertTrue(
            schedule_matches_now(interval, now=datetime(2026, 7, 31, 0, 30, tzinfo=UTC))
        )
        self.assertFalse(
            schedule_matches_now(interval, now=datetime(2026, 7, 30, 23, 30, tzinfo=UTC))
        )
        self.assertTrue(
            schedule_matches_now(legacy, now=datetime(2026, 7, 31, 2, 0, tzinfo=UTC))
        )
        self.assertFalse(
            schedule_matches_now(legacy, now=datetime(2026, 7, 31, 18, 0, tzinfo=UTC))
        )

    def test_interval_start_seconds_round_up_to_the_next_scheduler_minute(self):
        interval = {
            "enabled": True,
            "mode": "interval",
            "timezone": "UTC",
            "starts_at": "2026-07-31T09:30:30",
            "interval_unit": "hour",
            "interval_value": 1,
            "cron_expr": "0 */1 * * *",
        }

        self.assertFalse(
            schedule_matches_now(interval, now=datetime(2026, 7, 31, 9, 30, 59, tzinfo=UTC))
        )
        self.assertTrue(
            schedule_matches_now(interval, now=datetime(2026, 7, 31, 9, 31, tzinfo=UTC))
        )
        self.assertTrue(
            schedule_matches_now(interval, now=datetime(2026, 7, 31, 10, 31, tzinfo=UTC))
        )

    def test_repeated_dst_wall_minute_uses_one_fire_key(self):
        schedule = {
            "enabled": True,
            "mode": "daily",
            "timezone": "America/New_York",
            "starts_at": None,
            "time": "01:30",
            "cron_expr": "30 1 * * *",
        }
        first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
        repeated = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)

        self.assertTrue(schedule_matches_now(schedule, now=first))
        self.assertTrue(schedule_matches_now(schedule, now=repeated))
        self.assertEqual(
            _schedule_fire_key(schedule, now=first),
            _schedule_fire_key(schedule, now=repeated),
        )

    def test_interval_uses_elapsed_time_across_repeated_dst_hour(self):
        schedule = {
            "enabled": True,
            "mode": "interval",
            "timezone": "America/New_York",
            "starts_at": "2026-11-01T00:30",
            "interval_unit": "hour",
            "interval_value": 1,
            "cron_expr": "0 */1 * * *",
        }
        first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
        repeated = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)

        self.assertTrue(schedule_matches_now(schedule, now=first))
        self.assertTrue(schedule_matches_now(schedule, now=repeated))
        self.assertNotEqual(
            _schedule_fire_key(schedule, now=first),
            _schedule_fire_key(schedule, now=repeated),
        )

    def test_retention_candidates_are_logical_snapshots(self):
        now = timezone.now()
        old = self._snapshot("old-logical", now - timedelta(days=3))
        latest = self._snapshot("latest-logical", now - timedelta(hours=1))

        candidates = retention_delete_candidates_for_config(
            config=self.config,
            policy=self.policy,
            now=now,
        )

        self.assertEqual([snapshot.id for snapshot in candidates], [old.id])
        self.assertNotIn(latest.id, [snapshot.id for snapshot in candidates])
