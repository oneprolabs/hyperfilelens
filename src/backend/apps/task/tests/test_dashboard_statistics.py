"""Dashboard batching must preserve the ordinary filtered statistics contract."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.restore.models import RestoreRecord
from apps.task.models import Task
from apps.task.selectors.interface import dashboard_task_statistics, list_tasks, task_statistics


class DashboardStatisticsTests(TestCase):
    def setUp(self):
        from common.extension_spi import clear_providers_for_tests, restore_providers_for_tests

        previous = clear_providers_for_tests()
        self.addCleanup(restore_providers_for_tests, previous)
        self.org = Organization.objects.create(key="dashboard-stats")
        self.user = get_user_model().objects.create_user(username="dashboard-reader")
        Membership.objects.create(user=self.user, organization=self.org)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.now = timezone.now().replace(microsecond=0)
        self.windows = [
            {"finished_after": self.now - timedelta(days=1), "finished_before": self.now},
        ]
        for kind, state in [
            ("backup", "success"), ("backup", "timeout"), ("restore", "running"),
            ("restore", "success"), ("insight_workspace_restore", "success"),
        ]:
            Task.objects.create(
                organization_id=self.org.pk, task_type=kind, status=state,
                finished_at=self.now - timedelta(hours=1),
            )
        Task.objects.create(organization_id=987654321, task_type="backup", status="success")
        self.queryset = Task.objects.filter(organization_id=self.org.pk)

    def params(self):
        return {
            "recovery_created_after": (self.now - timedelta(days=1)).isoformat(),
            "windows": json.dumps([
                {key: value.isoformat() for key, value in window.items()}
                for window in self.windows
            ]),
        }

    def test_one_scan_preserves_total_restore_and_day_statistics(self):
        with self.assertNumQueries(1):
            result = dashboard_task_statistics(
                organization_id=self.org.pk, queryset=self.queryset,
                windows=self.windows, recovery_created_after=self.now - timedelta(days=1),
            )
        self.assertEqual(result["total"], task_statistics(
            organization_id=self.org.pk, queryset=self.queryset,
        ))
        self.assertEqual(result["recovery_24h"], task_statistics(
            organization_id=self.org.pk,
            queryset=list_tasks(
                organization_id=self.org.pk, task_type="restore",
                created_after=self.now - timedelta(days=1), exclude_insight_workspace_restores=True,
            ),
        ))
        self.assertEqual(result["days"][0], task_statistics(
            organization_id=self.org.pk,
            queryset=list_tasks(
                organization_id=self.org.pk, terminal_only=True, **self.windows[0],
            ),
        ))

    def test_ordinary_statistics_use_one_query_and_keep_empty_shape(self):
        with self.assertNumQueries(1):
            result = task_statistics(organization_id=self.org.pk, queryset=self.queryset)
        self.assertEqual(result["total"], 5)
        empty = task_statistics(organization_id=self.org.pk, queryset=self.queryset.none())
        self.assertEqual(empty["total"], 0)
        self.assertEqual(empty["by_task_type"], {})

    def test_replacement_folding_does_not_rewrite_previous_day_outcomes(self):
        yesterday = self.now - timedelta(days=1)
        original = Task.objects.create(
            organization_id=self.org.id, task_type=Task.Type.REPOSITORY_OPERATION,
            display_name="Quick maintenance", status=Task.Status.FAILED,
            trigger_type=Task.TriggerType.SYSTEM, started_at=yesterday,
            finished_at=yesterday,
        )
        replacement = Task.objects.create(
            organization_id=self.org.id, task_type=original.task_type,
            display_name=original.display_name, replaces_task=original,
            trigger_type=original.trigger_type, status=Task.Status.SUCCESS,
            started_at=self.now, finished_at=self.now,
        )
        window = {
            "finished_after": (yesterday - timedelta(hours=1)).isoformat(),
            "finished_before": (yesterday + timedelta(hours=1)).isoformat(),
        }
        response = self.client.get("/api/v1/tasks/dashboard-statistics/", {
            "recovery_created_after": yesterday.isoformat(),
            "windows": json.dumps([window]),
        }, HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["days"][0]["failed"], 1)
        historical_rows = self.client.get("/api/v1/tasks/", {
            **window, "terminal_only": "true",
        }, HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(historical_rows.status_code, 200, historical_rows.data)
        self.assertEqual(
            [row["task_uuid"] for row in historical_rows.data["data"]["list"]],
            [str(original.task_uuid)],
        )
        statistics = self.client.get(
            "/api/v1/tasks/statistics/", window, HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(statistics.data["failed"], 1)
        failed_rows = self.client.get("/api/v1/tasks/", {
            "status": "failed", "task_type": original.task_type,
        }, HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(
            [row["task_uuid"] for row in failed_rows.data["data"]["list"]],
            [str(original.task_uuid)],
        )
        default_rows = self.client.get("/api/v1/tasks/", {
            "task_type": original.task_type,
        }, HTTP_X_ORG_KEY=self.org.key)
        self.assertEqual(
            [row["task_uuid"] for row in default_rows.data["data"]["list"]],
            [str(replacement.task_uuid)],
        )

    def test_api_is_additive_and_tenant_scoped(self):
        response = self.client.get(
            "/api/v1/tasks/dashboard-statistics/", self.params(), HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"]["total"], 5)
        self.assertEqual(len(response.data["days"]), 1)
        self.assertEqual(
            self.client.get("/api/v1/tasks/statistics/", HTTP_X_ORG_KEY=self.org.key).status_code,
            200,
        )

    def test_api_bounds_and_validates_windows(self):
        for windows in ("not-json", "{}", "[]", json.dumps([{}] * 8)):
            with self.subTest(windows=windows):
                response = self.client.get(
                    "/api/v1/tasks/dashboard-statistics/",
                    {**self.params(), "windows": windows}, HTTP_X_ORG_KEY=self.org.key,
                )
                self.assertEqual(response.status_code, 400, response.data)
        params = self.params()
        params["windows"] = json.dumps([{
            "finished_after": self.now.isoformat(),
            "finished_before": (self.now - timedelta(days=1)).isoformat(),
        }])
        self.assertEqual(self.client.get(
            "/api/v1/tasks/dashboard-statistics/", params, HTTP_X_ORG_KEY=self.org.key,
        ).status_code, 400)

    def test_authentication_is_still_required(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(
            "/api/v1/tasks/dashboard-statistics/", self.params(), HTTP_X_ORG_KEY=self.org.key,
        ).status_code, 401)

    def test_legacy_workspace_restores_are_excluded_only_from_recovery_metrics(self):
        legacy = Task.objects.create(
            organization_id=self.org.pk, task_type=Task.Type.RESTORE,
            status=Task.Status.SUCCESS, finished_at=self.now,
        )
        RestoreRecord.objects.create(
            organization_id=self.org.pk, requesting_organization_id=self.org.pk,
            target_execution_organization_id=self.org.pk, target_execution_node_id=100,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key="dashboard-legacy", workspace_binding_id=200,
            restore_uid="dashboard-legacy", source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=legacy.pk, task_uuid=legacy.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT, source_ref_id=300,
            source_snapshot_id=400, target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=100, target_path="/tmp/workspace",
            scope=RestoreRecord.Scope.PATHS, conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )
        result = dashboard_task_statistics(
            organization_id=self.org.pk, queryset=self.queryset,
            windows=self.windows, recovery_created_after=self.now - timedelta(days=1),
        )
        self.assertEqual(result["total"]["total"], 6)
        self.assertEqual(result["days"][0]["total"], 5)
        self.assertEqual(result["recovery_24h"]["total"], 2)
        self.assertEqual(result["recovery_24h"], task_statistics(
            organization_id=self.org.pk,
            queryset=list_tasks(
                organization_id=self.org.pk, task_type="restore",
                created_after=self.now - timedelta(days=1), exclude_insight_workspace_restores=True,
            ),
        ))

    def test_day_windows_match_existing_inclusive_boundaries_and_terminal_filter(self):
        for finished in (
            self.windows[0]["finished_after"],
            self.windows[0]["finished_before"],
            self.windows[0]["finished_before"] + timedelta(microseconds=1),
            None,
        ):
            Task.objects.create(
                organization_id=self.org.pk, task_type=Task.Type.BACKUP,
                status=Task.Status.SUCCESS, finished_at=finished,
            )
        result = dashboard_task_statistics(
            organization_id=self.org.pk, queryset=self.queryset,
            windows=self.windows, recovery_created_after=self.now - timedelta(days=1),
        )
        self.assertEqual(result["days"][0], task_statistics(
            organization_id=self.org.pk,
            queryset=list_tasks(
                organization_id=self.org.pk, terminal_only=True, **self.windows[0],
            ),
        ))
