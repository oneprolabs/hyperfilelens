import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam.models import Membership
from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.models import LensSessionLink, LensSlUserLink, LensUsageLedger
from apps.lens_bridge.services import usage


class UsageCaptureTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="usage-capture",
            email="usage-capture@example.com",
            password="test-password",
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        LensSlUserLink.objects.create(
            hfl_user=self.user,
            sl_user_id=23,
            sl_username="hfl-u-23",
            provision_status=LensSlUserLink.ProvisionStatus.READY,
        )
        self.session = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            title="Finance Chat",
            source_scopes_json=[{"source_path": "/finance", "path_type": "dir"}],
        )

    def test_captures_all_model_calls_for_one_q_and_a(self):
        run_uuid = uuid.uuid4()
        usage.register_usage_run(
            self.session,
            run_uuid=run_uuid,
            question="Summarize finance files",
            status="running",
        )

        row = usage.capture_run_usage(
            self.session,
            {
                "uuid": str(run_uuid),
                "status": "done",
                "started_at": timezone.now().isoformat(),
                "finished_at": timezone.now().isoformat(),
                "steps": [
                    {
                        "detail": {
                            "events": [
                                {
                                    "agent_event": "llm.response",
                                    "prompt_tokens": 100,
                                    "completion_tokens": 20,
                                    "total_tokens": 120,
                                    "cost": 0.01,
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 30,
                                "completion_tokens": 5,
                                "total_tokens": 35,
                                "cost": 0.002,
                            },
                        }
                    }
                ],
            },
        )

        self.assertIsNotNone(row)
        self.assertEqual(row.prompt_tokens, 130)
        self.assertEqual(row.completion_tokens, 25)
        self.assertEqual(row.total_tokens, 155)
        self.assertEqual(row.model_calls, 2)
        self.assertEqual(row.estimated_cost, Decimal("0.012"))
        self.assertEqual(len(row.call_details_json), 2)

    def test_partial_model_call_cost_is_kept_unavailable(self):
        run_uuid = uuid.uuid4()
        usage.register_usage_run(
            self.session,
            run_uuid=run_uuid,
            question="Summarize finance files",
            status="running",
        )

        row = usage.capture_run_usage(
            self.session,
            {
                "uuid": str(run_uuid),
                "status": "done",
                "steps": [
                    {
                        "detail": {
                            "events": [
                                {
                                    "agent_event": "llm.response",
                                    "total_tokens": 10,
                                    "cost": "0.001",
                                },
                                {
                                    "agent_event": "llm.response",
                                    "total_tokens": 20,
                                },
                            ],
                        },
                    }
                ],
            },
        )

        self.assertIsNone(row.estimated_cost)

    def test_captures_top_level_usage_summary_when_steps_are_unavailable(self):
        run_uuid = uuid.uuid4()
        usage.register_usage_run(
            self.session,
            run_uuid=run_uuid,
            question="Summarize finance files",
            status="running",
        )

        row = usage.capture_run_usage(
            self.session,
            {
                "uuid": str(run_uuid),
                "status": "done",
                "prompt_tokens": 70,
                "completion_tokens": 30,
                "total_tokens": 100,
                "llm_calls": 2,
                "total_cost": "0.004",
            },
        )

        self.assertEqual(row.total_tokens, 100)
        self.assertEqual(row.model_calls, 2)
        self.assertEqual(row.estimated_cost, Decimal("0.004"))


class UsageApiIsolationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="usage-owner",
            email="usage-owner@example.com",
            password="test-password",
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        self.other_user = get_user_model().objects.create_user(
            username="usage-other",
            email="usage-other@example.com",
            password="test-password",
        )
        Membership.objects.create(
            user=self.other_user,
            organization=self.org,
            role=Membership.Role.OPERATOR,
        )
        LensSlUserLink.objects.create(
            hfl_user=self.user,
            sl_user_id=23,
            sl_username="hfl-u-23",
            provision_status=LensSlUserLink.ProvisionStatus.READY,
        )
        self.other_row = LensUsageLedger.objects.create(
            organization=self.org,
            hfl_user=self.other_user,
            sl_user_id=24,
            sl_run_uuid=uuid.uuid4(),
            chat_title="Other User Chat",
            question="Private question",
            occurred_at=timezone.now(),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("apps.lens_bridge.services.usage.sl_client.request_json")
    def test_overview_reads_only_the_current_users_hfl_ledger(self, request_json):
        LensUsageLedger.objects.create(
            organization=self.org,
            hfl_user=self.user,
            sl_user_id=23,
            sl_run_uuid=uuid.uuid4(),
            question="My usage",
            run_status="done",
            total_tokens=1200,
            estimated_cost=Decimal("0.25"),
            occurred_at=timezone.now(),
            source_synced_at=timezone.now(),
        )

        response = self.client.get(
            reverse("lens-copilot-usage"),
            {"user_id": "999", "page_size": 20},
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        payload = payload.get("data", payload)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["results"][0]["question"], "My usage")
        self.assertNotIn("Private question", str(payload))
        request_json.assert_not_called()

    def test_detail_does_not_expose_another_users_ledger(self):
        response = self.client.get(
            reverse(
                "lens-copilot-usage-detail",
                kwargs={"run_uuid": self.other_row.sl_run_uuid},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 404)

    @patch("apps.lens_bridge.services.usage.sl_client.request_json")
    def test_detail_is_also_a_pure_ledger_read(self, request_json):
        row = LensUsageLedger.objects.create(
            organization=self.org,
            hfl_user=self.user,
            sl_user_id=23,
            sl_run_uuid=uuid.uuid4(),
            question="Ledger-only detail",
            run_status="done",
            occurred_at=timezone.now(),
        )

        response = self.client.get(
            reverse("lens-copilot-usage-detail", kwargs={"run_uuid": row.sl_run_uuid}),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        request_json.assert_not_called()

    def test_today_overview_aggregates_model_calls_and_hourly_trend(self):
        now = timezone.localtime()
        LensUsageLedger.objects.create(
            organization=self.org,
            hfl_user=self.user,
            sl_user_id=23,
            sl_run_uuid=uuid.uuid4(),
            chat_title="Finance Chat",
            backup_source_name="Finance Server",
            question="Summarize today's changes",
            prompt_tokens=100,
            completion_tokens=25,
            cached_tokens=20,
            reasoning_tokens=5,
            total_tokens=125,
            model_calls=3,
            estimated_cost=Decimal("0.0125"),
            occurred_at=now,
        )

        payload = usage.usage_overview(self.org, self.user, {})

        self.assertEqual(payload["period"]["start_date"], now.date().isoformat())
        self.assertEqual(payload["period"]["end_date"], now.date().isoformat())
        self.assertEqual(payload["summary"]["model_calls"], 3)
        self.assertEqual(payload["summary"]["q_and_a_requests"], 1)
        self.assertEqual(payload["by_backup_source"][0]["model_calls"], 3)
        self.assertEqual(len(payload["trend"]), now.hour + 1)
        self.assertTrue(all("T" in row["bucket"] for row in payload["trend"]))
        current_hour = payload["trend"][now.hour]
        self.assertEqual(current_hour["total_calls"], 3)
        self.assertEqual(current_hour["total_tokens"], 125)

    def test_unknown_cost_is_not_reported_as_zero(self):
        now = timezone.now()
        LensUsageLedger.objects.create(
            organization=self.org,
            hfl_user=self.user,
            sl_user_id=23,
            sl_run_uuid=uuid.uuid4(),
            question="Cost unavailable",
            run_status="done",
            total_tokens=100,
            estimated_cost=None,
            occurred_at=now,
            source_synced_at=now,
        )

        payload = usage.usage_overview(self.org, self.user, {})

        self.assertIsNone(payload["summary"]["estimated_cost"])
        self.assertIsNone(payload["summary"]["average_cost_per_q_and_a"])
        self.assertIsNone(payload["by_backup_source"][0]["estimated_cost"])
        self.assertIsNone(payload["trend"][timezone.localtime(now).hour]["total_cost"])


class UsageReconciliationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="usage-reconciliation",
            email="usage-reconciliation@example.com",
            password="test-password",
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        LensSlUserLink.objects.create(
            hfl_user=self.user,
            sl_user_id=29,
            sl_username="hfl-u-29",
            provision_status=LensSlUserLink.ProvisionStatus.READY,
        )
        self.session = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            title="Reconciliation Chat",
        )

    @patch("apps.lens_bridge.services.usage.sl_client.request_json")
    def test_terminal_run_is_reconciled_and_active_run_is_cleared(self, request_json):
        run_uuid = uuid.uuid4()
        self.session.active_run_uuid = run_uuid
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )
        row = usage.register_usage_run(
            self.session,
            run_uuid=run_uuid,
            question="Close the browser",
            status="running",
        )
        request_json.return_value = {
            "uuid": str(run_uuid),
            "status": "done",
            "steps": [
                {
                    "detail": {
                        "usage": {
                            "prompt_tokens": 80,
                            "completion_tokens": 20,
                            "total_tokens": 100,
                            "cost": "0.004",
                        },
                    },
                }
            ],
            "finished_at": timezone.now().isoformat(),
        }

        result = usage.reconcile_usage_ledgers(limit=10)

        self.assertEqual(result["reconciled"], 1)
        row.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(row.run_status, "done")
        self.assertEqual(row.total_tokens, 100)
        self.assertEqual(row.estimated_cost, Decimal("0.004"))
        self.assertIsNotNone(row.source_synced_at)
        self.assertIsNone(row.reconciliation_next_at)
        self.assertIsNone(self.session.active_run_uuid)
        request_json.assert_called_once_with(
            "GET",
            f"/api/lens/runs/{run_uuid}/",
            hfl_user=self.user,
            timeout=30,
        )

    @patch("apps.lens_bridge.services.usage.sl_client.request_json")
    def test_failure_is_persisted_with_backoff_for_retry(self, request_json):
        from apps.lens_bridge.services import sl_client

        row = usage.register_usage_run(
            self.session,
            run_uuid=uuid.uuid4(),
            question="Retry me",
            status="running",
        )
        request_json.side_effect = sl_client.LensBridgeUnavailable()

        result = usage.reconcile_usage_ledgers(limit=10)

        self.assertEqual(len(result["failed"]), 1)
        row.refresh_from_db()
        self.assertEqual(row.reconciliation_attempts, 1)
        self.assertIsNone(row.reconciliation_claim_token)
        self.assertIsNotNone(row.reconciliation_next_at)
        self.assertTrue(row.reconciliation_error)

    @patch("apps.lens_bridge.services.usage.sl_client.request_json")
    def test_missing_source_run_is_closed_instead_of_retried_forever(
        self,
        request_json,
    ):
        from apps.lens_bridge.services import sl_client

        run_uuid = uuid.uuid4()
        self.session.active_run_uuid = run_uuid
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )
        row = usage.register_usage_run(
            self.session,
            run_uuid=run_uuid,
            question="Missing upstream run",
            status="running",
        )
        error = sl_client.LensBridgeError("Run not found.")
        error.status_code = 404
        request_json.side_effect = error
        old_time = timezone.now() - timedelta(minutes=10)
        LensUsageLedger.objects.filter(id=row.id).update(
            created_at=old_time,
            reconciliation_attempts=2,
        )

        result = usage.reconcile_usage_ledgers(limit=10)

        self.assertEqual(result["claimed"], 1)
        row.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(row.run_status, "failed")
        self.assertEqual(row.run_error, "SOURCE_RUN_NOT_FOUND")
        self.assertIsNone(row.reconciliation_next_at)
        self.assertIsNone(self.session.active_run_uuid)

    @patch("apps.lens_bridge.services.usage.sl_client.request_json")
    def test_missing_historical_source_run_keeps_its_terminal_ledger_status(
        self,
        request_json,
    ):
        from apps.lens_bridge.services import sl_client

        row = usage.register_usage_run(
            self.session,
            run_uuid=uuid.uuid4(),
            question="Historical completed run",
            status="done",
        )
        error = sl_client.LensBridgeError("Run not found.")
        error.status_code = 404
        request_json.side_effect = error
        old_time = timezone.now() - timedelta(minutes=10)
        LensUsageLedger.objects.filter(id=row.id).update(
            created_at=old_time,
            reconciliation_attempts=2,
        )

        usage.reconcile_usage_ledgers(limit=10)

        row.refresh_from_db()
        self.assertEqual(row.run_status, "done")
        self.assertIsNotNone(row.source_synced_at)
        self.assertIsNone(row.reconciliation_next_at)

    @patch("apps.lens_bridge.services.usage.sl_client.request_json")
    def test_first_not_found_response_is_retried_during_grace_period(
        self,
        request_json,
    ):
        from apps.lens_bridge.services import sl_client

        row = usage.register_usage_run(
            self.session,
            run_uuid=uuid.uuid4(),
            question="Eventually visible run",
            status="running",
        )
        error = sl_client.LensBridgeError("Run not found.")
        error.status_code = 404
        request_json.side_effect = error

        usage.reconcile_usage_ledgers(limit=10)

        row.refresh_from_db()
        self.assertEqual(row.run_status, "running")
        self.assertEqual(row.reconciliation_attempts, 1)
        self.assertIsNotNone(row.reconciliation_next_at)

    @patch(
        "apps.lens_bridge.tasks.usage_reconciliation."
        "execute_usage_ledger_reconciliation_task.delay"
    )
    def test_dispatcher_claims_each_due_row_only_once(self, delay):
        from apps.lens_bridge.tasks.usage_reconciliation import (
            reconcile_usage_ledgers_task,
        )

        row = usage.register_usage_run(
            self.session,
            run_uuid=uuid.uuid4(),
            question="Dispatch me",
            status="running",
        )

        first = reconcile_usage_ledgers_task(limit=10)
        second = reconcile_usage_ledgers_task(limit=10)

        self.assertEqual(first["claimed"], 1)
        self.assertEqual(second["claimed"], 0)
        row.refresh_from_db()
        self.assertIsNotNone(row.reconciliation_claim_token)
        delay.assert_called_once_with(
            ledger_id=row.id,
            claim_token=str(row.reconciliation_claim_token),
        )

    @patch(
        "apps.lens_bridge.tasks.usage_reconciliation."
        "execute_usage_ledger_reconciliation_task.delay"
    )
    def test_dispatcher_seeds_a_missing_active_run_ledger(self, delay):
        from apps.lens_bridge.tasks.usage_reconciliation import (
            reconcile_usage_ledgers_task,
        )

        run_uuid = uuid.uuid4()
        self.session.active_run_uuid = run_uuid
        self.session.active_run_status = "running"
        self.session.save(
            update_fields=["active_run_uuid", "active_run_status", "updated_at"]
        )

        result = reconcile_usage_ledgers_task(limit=10)

        row = LensUsageLedger.objects.get(sl_run_uuid=run_uuid)
        self.assertEqual(result["seeded"], [row.id])
        self.assertEqual(result["claimed"], 1)
        self.assertEqual(row.run_status, "running")
        delay.assert_called_once_with(
            ledger_id=row.id,
            claim_token=str(row.reconciliation_claim_token),
        )
