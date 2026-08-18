from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.protection.models import BackupConfig, BackupPolicy, FileFilterRule
from apps.protection.selectors.interface import policy_display_name


class ProtectionPolicyApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="protection-api@test.local",
            email="protection-api@test.local",
            password="test-pass",
        )
        self.other_user = user_model.objects.create_user(
            username="protection-other@test.local",
            email="protection-other@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="protection-test-org", name="Protection Test Org")
        self.other_org = Organization.objects.create(key="protection-other-org", name="Protection Other Org")
        Membership.objects.create(user=self.user, organization=self.org, role=Membership.Role.ADMIN)
        Membership.objects.create(
            user=self.other_user,
            organization=self.other_org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)

    def _headers(self, org: Organization | None = None):
        return {"HTTP_X_ORG_KEY": (org or self.org).key}

    def _policy_payload(self, name: str = "Daily production policy"):
        return {
            "name": name,
            "is_active": True,
            "schedule": {"enabled": True, "cron_expr": "0 2 * * *"},
            "retention": {
                "enabled": True,
                "recent_points": 10,
                "hourly_enabled": True,
                "hourly_hours": 48,
                "daily_enabled": True,
                "daily_days": 30,
                "weekly_enabled": True,
                "weekly_weeks": 4,
                "monthly_enabled": True,
                "monthly_months": 12,
                "annual_enabled": True,
                "annual_years": 5,
            },
            "throttling": {"enabled": True, "unlimited": True, "rate_mbps": 0},
            "error_handling": {
                "enabled": True,
                "ignore_directory_read_errors": True,
                "ignore_file_read_errors": False,
                "ignore_unknown_entries": True,
            },
        }

    def _filter_payload(self, name: str = "Skip cache"):
        return {
            "name": name,
            "is_active": True,
            "ignore_patterns": " **/node_modules/** \n\n **/.git/** ",
            "large_file_limit_enabled": True,
            "large_file_bytes_max": 1024,
            "ignore_cache_directories": True,
            "current_filesystem_only": False,
        }

    def test_create_policy_persists_and_survives_refresh(self):
        create = self.client.post(
            "/api/v1/protection/policies/",
            self._policy_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        policy_id = create.data["id"]

        listing = self.client.get(
            "/api/v1/protection/policies/?page=1&page_size=10",
            **self._headers(),
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertIn(policy_id, [row["id"] for row in listing.data["results"]])
        self.assertEqual(policy_display_name(policy_id=policy_id, organization_id=self.org.id), "Daily production policy")

    def test_structured_schedule_validates_and_round_trips(self):
        weekly = self._policy_payload("Shanghai weekdays")
        weekly["schedule"] = {
            "enabled": True,
            "mode": "weekly",
            "timezone": "Asia/Shanghai",
            "starts_at": "2026-07-31T09:30:45",
            "time": "09:30",
            "weekdays": [5, 1, 3, 3],
            "cron_expr": "ignored by normalization",
        }

        response = self.client.post(
            "/api/v1/protection/policies/",
            weekly,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(
            response.data["schedule"],
            {
                "enabled": True,
                "mode": "weekly",
                "timezone": "Asia/Shanghai",
                "starts_at": "2026-07-31T09:30:45",
                "time": "09:30",
                "weekdays": [1, 3, 5],
                "cron_expr": "30 9 * * 1,3,5",
            },
        )
        self.assertIn("Asia/Shanghai", response.data["schedule_summary"])

        legacy_start = self._policy_payload("Legacy minute start")
        legacy_start["schedule"] = {
            **weekly["schedule"],
            "starts_at": "2026-07-31T09:30",
        }
        legacy_response = self.client.post(
            "/api/v1/protection/policies/",
            legacy_start,
            format="json",
            **self._headers(),
        )
        self.assertEqual(legacy_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(legacy_response.data["schedule"]["starts_at"], "2026-07-31T09:30:00")

        invalid_second = self._policy_payload("Invalid start second")
        invalid_second["schedule"] = {
            **weekly["schedule"],
            "starts_at": "2026-07-31T09:30:60",
        }
        invalid_second_response = self.client.post(
            "/api/v1/protection/policies/",
            invalid_second,
            format="json",
            **self._headers(),
        )
        self.assertEqual(invalid_second_response.status_code, status.HTTP_400_BAD_REQUEST)

        invalid_timezone = self._policy_payload("Invalid timezone")
        invalid_timezone["schedule"] = {
            **weekly["schedule"],
            "timezone": "Mars/Olympus",
        }
        timezone_response = self.client.post(
            "/api/v1/protection/policies/",
            invalid_timezone,
            format="json",
            **self._headers(),
        )
        self.assertEqual(timezone_response.status_code, status.HTTP_400_BAD_REQUEST)

        nonexistent_start = self._policy_payload("Nonexistent DST start")
        nonexistent_start["schedule"] = {
            **weekly["schedule"],
            "timezone": "America/New_York",
            "starts_at": "2026-03-08T02:30:15",
        }
        dst_response = self.client.post(
            "/api/v1/protection/policies/",
            nonexistent_start,
            format="json",
            **self._headers(),
        )
        self.assertEqual(dst_response.status_code, status.HTTP_400_BAD_REQUEST)

        missing_weekday = self._policy_payload("Missing weekday")
        missing_weekday["schedule"] = {**weekly["schedule"], "weekdays": []}
        weekday_response = self.client.post(
            "/api/v1/protection/policies/",
            missing_weekday,
            format="json",
            **self._headers(),
        )
        self.assertEqual(weekday_response.status_code, status.HTTP_400_BAD_REQUEST)

        month_end = self._policy_payload("Month end")
        month_end["schedule"] = {
            "enabled": True,
            "mode": "monthly",
            "timezone": "UTC",
            "starts_at": None,
            "time": "08:00",
            "month_days": [1, 15],
            "month_end": True,
        }
        month_end_response = self.client.post(
            "/api/v1/protection/policies/",
            month_end,
            format="json",
            **self._headers(),
        )
        self.assertEqual(month_end_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(month_end_response.data["schedule"]["cron_expr"], "0 8 1,15,31 * *")

    def test_policy_search_validation_tenant_state_and_delete_protection(self):
        first = BackupPolicy.objects.create(
            organization_id=self.org.id,
            **self._policy_payload("Searchable policy"),
        )
        other = BackupPolicy.objects.create(
            organization_id=self.other_org.id,
            **self._policy_payload("Other tenant policy"),
        )

        search = self.client.get(
            "/api/v1/protection/policies/?search=Searchable",
            **self._headers(),
        )
        self.assertEqual(search.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in search.data["results"]], [first.id])

        name_field = self.client.get(
            "/api/v1/protection/policies/?search=enabled&search_field=name&page=1&page_size=10",
            **self._headers(),
        )
        self.assertEqual(name_field.status_code, status.HTTP_200_OK)
        self.assertEqual(name_field.data["count"], 0)

        self.client.force_authenticate(user=self.other_user)
        other_list = self.client.get(
            "/api/v1/protection/policies/",
            **self._headers(self.other_org),
        )
        self.assertEqual(other_list.status_code, status.HTTP_200_OK)
        self.assertIn(other.id, [row["id"] for row in other_list.data["results"]])
        self.assertNotIn(first.id, [row["id"] for row in other_list.data["results"]])

        self.client.force_authenticate(user=self.user)
        bad_cron = self._policy_payload("Bad cron")
        bad_cron["schedule"] = {"enabled": True, "cron_expr": "* * *"}
        cron_res = self.client.post(
            "/api/v1/protection/policies/",
            bad_cron,
            format="json",
            **self._headers(),
        )
        self.assertEqual(cron_res.status_code, status.HTTP_400_BAD_REQUEST)

        bad_retention = self._policy_payload("Bad retention")
        bad_retention["retention"]["hourly_hours"] = 0
        bad_retention["retention"]["hourly_enabled"] = True
        retention_res = self.client.post(
            "/api/v1/protection/policies/",
            bad_retention,
            format="json",
            **self._headers(),
        )
        self.assertEqual(retention_res.status_code, status.HTTP_400_BAD_REQUEST)

        missing_enabled_period = self._policy_payload("Missing enabled period")
        missing_enabled_period["retention"].pop("daily_days")
        missing_period_res = self.client.post(
            "/api/v1/protection/policies/",
            missing_enabled_period,
            format="json",
            **self._headers(),
        )
        self.assertEqual(missing_period_res.status_code, status.HTTP_400_BAD_REQUEST)

        disabled_retention = self._policy_payload("Disabled tiers omit periods")
        disabled_retention["retention"].update(
            {
                "hourly_enabled": False,
                "daily_enabled": False,
                "weekly_enabled": False,
                "monthly_enabled": False,
                "annual_enabled": False,
            }
        )
        for field in ("hourly_hours", "daily_days", "weekly_weeks", "monthly_months", "annual_years"):
            disabled_retention["retention"].pop(field)
        disabled_res = self.client.post(
            "/api/v1/protection/policies/",
            disabled_retention,
            format="json",
            **self._headers(),
        )
        self.assertEqual(disabled_res.status_code, status.HTTP_201_CREATED, disabled_res.content)
        for field in ("hourly_hours", "daily_days", "weekly_weeks", "monthly_months", "annual_years"):
            self.assertNotIn(field, disabled_res.data["retention"])

        legacy_hourly = self._policy_payload("Legacy hourly days")
        legacy_hourly["retention"].pop("hourly_hours", None)
        legacy_hourly["retention"]["hourly_days"] = 2
        legacy_res = self.client.post(
            "/api/v1/protection/policies/",
            legacy_hourly,
            format="json",
            **self._headers(),
        )
        self.assertEqual(legacy_res.status_code, status.HTTP_201_CREATED, legacy_res.content)
        self.assertEqual(legacy_res.data["retention"]["hourly_hours"], 48)
        self.assertNotIn("hourly_days", legacy_res.data["retention"])

        state_res = self.client.post(
            "/api/v1/protection/policies/bulk-state/",
            {"ids": [first.id], "is_active": False},
            format="json",
            **self._headers(),
        )
        self.assertEqual(state_res.status_code, status.HTTP_200_OK)
        first.refresh_from_db()
        self.assertFalse(first.is_active)

        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Policy-bound config",
            source_type="agent",
            source_ref_id=101,
            repository_id=10,
            backup_policy_id=first.id,
            compression_level="balanced",
        )
        delete_res = self.client.delete(
            f"/api/v1/protection/policies/{first.id}/",
            **self._headers(),
        )
        self.assertEqual(delete_res.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(BackupPolicy.objects.filter(id=first.id).exists())

    def test_create_filter_persists_normalizes_and_bulk_deletes(self):
        create = self.client.post(
            "/api/v1/protection/filters/",
            self._filter_payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        rule_id = create.data["id"]
        self.assertEqual(create.data["ignore_patterns"], "**/node_modules/**\n**/.git/**")
        self.assertTrue(create.data["ignore_cache_directories"])

        listing = self.client.get(
            "/api/v1/protection/filters/?search=node_modules&page=1&page_size=10",
            **self._headers(),
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertIn(rule_id, [row["id"] for row in listing.data["results"]])

        name_field = self.client.get(
            "/api/v1/protection/filters/?search=node_modules&search_field=name&page=1&page_size=10",
            **self._headers(),
        )
        self.assertEqual(name_field.status_code, status.HTTP_200_OK)
        self.assertEqual(name_field.data["count"], 0)

        invalid = self._filter_payload("Invalid large")
        invalid["large_file_bytes_max"] = 0
        invalid_res = self.client.post(
            "/api/v1/protection/filters/",
            invalid,
            format="json",
            **self._headers(),
        )
        self.assertEqual(invalid_res.status_code, status.HTTP_400_BAD_REQUEST)

        state_res = self.client.post(
            "/api/v1/protection/filters/bulk-state/",
            {"ids": [rule_id, 999999], "is_active": False},
            format="json",
            **self._headers(),
        )
        self.assertEqual(state_res.status_code, status.HTTP_200_OK)
        self.assertEqual(state_res.data["updated"], [rule_id])
        self.assertEqual(state_res.data["failed"][0]["id"], 999999)

        delete_res = self.client.post(
            "/api/v1/protection/filters/bulk-delete/",
            {"ids": [rule_id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(delete_res.status_code, status.HTTP_200_OK)
        self.assertEqual(delete_res.data["deleted"], [rule_id])
        self.assertFalse(FileFilterRule.objects.filter(id=rule_id).exists())
