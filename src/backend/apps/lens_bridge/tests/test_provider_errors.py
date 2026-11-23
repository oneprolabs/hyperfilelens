"""Provider diagnostics use only supported REST reads and exact run evidence."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.alert.constants import AlertStatus
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.defaults import create_missing_default_alert_rules
from apps.iam.models import Organization
from apps.lens_bridge.models import LensOrgModelLink, LensUsageLedger
from apps.lens_bridge.services import provider_errors as diagnostics
from apps.lens_bridge.services.usage import _public_run_failure, capture_ledger_usage


def _localized_credit_error():
    return bytes.fromhex(
        "e5bd93e5898de7a79fe688b72063726564697420"
        "e8b4a6e688b7e4bd99e9a29de4b88de8b6b3"
    ).decode()


class ProviderErrorClassificationTests(SimpleTestCase):
    def test_only_explicit_provider_credit_evidence_is_accepted(self):
        for value in (
            _localized_credit_error(),
            "insufficient_balance",
            "Insufficient credits", "Your credit balance is too low.",
        ):
            with self.subTest(value=value):
                self.assertTrue(diagnostics._explicit_insufficient_credit(value))
        for value in (
            "EMPTY_AGENT_RESPONSE", "HTTP 402", "quota exceeded",
            "rate limit", "timeout", "", None,
        ):
            with self.subTest(value=value):
                self.assertFalse(diagnostics._explicit_insufficient_credit(value))

    def test_confirmed_credit_overrides_public_fallback_not_cancellation(self):
        state = {"status": "confirmed", "code": diagnostics.INSUFFICIENT_CREDIT}
        self.assertEqual(
            _public_run_failure("failed", "EMPTY_AGENT_RESPONSE", state),
            (diagnostics.INSUFFICIENT_CREDIT, diagnostics.CREDIT_MESSAGE),
        )
        self.assertEqual(
            _public_run_failure("cancelled", "", state)[0], "RUN_CANCELLED",
        )
        self.assertEqual(
            _public_run_failure("failed", "EMPTY_AGENT_RESPONSE", {})[0],
            "AI_RUN_FAILED",
        )


@patch("apps.alert.services.internal.lifecycle.send_notification")
class ProviderDiagnosticTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Credit tests", key="credit-tests")
        self.user = get_user_model().objects.create_user(username="credit-tests")
        self.model = LensOrgModelLink.objects.create(
            scope="platform", organization=None, sl_config_uuid=uuid.uuid4(),
            display_name="Shared model",
        )
        create_missing_default_alert_rules(
            owner_scope="platform", default_keys={"ai_model.insufficient_credit"},
        )
        self.started = timezone.now() - timedelta(minutes=2)
        self.finished = self.started + timedelta(seconds=30)
        self.row = self.new_row()
        self.call_id = str(uuid.uuid4())

    def new_row(self, status="failed"):
        return LensUsageLedger.objects.create(
            organization=self.org, hfl_user=self.user, sl_user_id=7,
            sl_run_uuid=uuid.uuid4(), occurred_at=self.started,
            started_at=self.started, finished_at=self.finished,
            run_status=status, run_error="EMPTY_AGENT_RESPONSE" if status == "failed" else "",
        )

    def run_payload(self, row=None):
        row = row or self.row
        return {
            "uuid": str(row.sl_run_uuid), "status": row.run_status,
            "error": row.run_error, "answer": "",
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat(),
            "model_calls": [{"uuid": self.call_id, "success": False}],
        }

    def usage_item(self, row=None):
        row = row or self.row
        return {
            "id": self.call_id, "success": False, "model": "unknown",
            "error": _localized_credit_error() + ". api_key=must-not-persist",
            "created_at": row.finished_at.isoformat(), "completion_tokens": 0,
            "metadata": {
                "run_uuid": str(row.sl_run_uuid),
                "selected_model_ref": str(self.model.sl_config_uuid),
            },
        }

    def claim(self, row=None):
        row = row or self.row
        diagnostics.initialize_diagnostic(row)
        row.save(update_fields=["provider_error_state_json"])
        claims = diagnostics.claim_due_diagnostics()
        return next(token for pk, token in claims if pk == row.pk)

    def diagnose(self, *, row=None, run=None, items=None):
        row = row or self.row
        token = self.claim(row)
        with patch.object(diagnostics.sl_client, "request_json", side_effect=[
            run or self.run_payload(row),
            {"results": items if items is not None else [self.usage_item(row)]},
        ]) as request:
            result = diagnostics.diagnose_claimed_ledger(
                ledger_id=row.pk, claim_token=token,
            )
        row.refresh_from_db()
        return result, request

    def test_confirms_unknown_model_using_exact_run_and_call_ids(self, notify):
        result, request = self.diagnose()
        self.assertEqual(result, "confirmed")
        state = self.row.provider_error_state_json
        self.assertEqual(state["code"], diagnostics.INSUFFICIENT_CREDIT)
        self.assertEqual(self.row.run_error, "EMPTY_AGENT_RESPONSE")
        self.assertNotIn("must-not-persist", str(state))
        self.assertNotIn(_localized_credit_error(), str(state))
        alert = AlertRecord.objects.get()
        self.assertEqual(alert.owner_scope, "platform")
        self.assertIsNone(alert.organization_id)
        self.assertEqual(alert.resource_id, str(self.model.pk))
        self.assertEqual(alert.metadata["affected_requests"], 1)
        self.assertNotIn("must-not-persist", str(alert.metadata))
        self.assertIn("/api/v1/admin/llm-usage/?", request.call_args_list[1].args[1])
        self.assertIn("deadline", request.call_args.kwargs)
        notify.assert_not_called()  # Must not send while transaction is uncommitted.

    def test_repeated_delivery_does_not_count_or_notify_twice(self, notify):
        self.diagnose()
        state = dict(self.row.provider_error_state_json)
        self.assertEqual(diagnostics.diagnose_claimed_ledger(
            ledger_id=self.row.pk, claim_token="stale-token",
        ), "skipped")
        self.row.refresh_from_db()
        self.assertEqual(state, self.row.provider_error_state_json)
        self.assertEqual(AlertRecord.objects.get().metadata["affected_requests"], 1)

    def test_multiple_runs_share_one_model_incident(self, notify):
        self.diagnose()
        self.diagnose(row=self.new_row())
        self.assertEqual(AlertRecord.objects.count(), 1)
        self.assertEqual(AlertRecord.objects.get().metadata["affected_requests"], 2)

    def test_acknowledgement_and_notification_cooldown_survive_more_failures(self, notify):
        with self.captureOnCommitCallbacks() as first_callbacks:
            self.diagnose()
        self.assertEqual(len(first_callbacks), 2)  # Delivery and existing inbox publisher.
        AlertRecord.objects.update(status=AlertStatus.ACKNOWLEDGED)
        with self.captureOnCommitCallbacks() as later_callbacks:
            self.diagnose(row=self.new_row())
        self.assertEqual(len(later_callbacks), 0)
        alert = AlertRecord.objects.get()
        self.assertEqual(alert.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(alert.metadata["affected_requests"], 2)

    def test_delivery_metadata_does_not_overwrite_a_newer_failure_count(self, notify):
        from apps.alert.services.internal.notifier import _mark_notified

        self.diagnose()
        stale = AlertRecord.objects.get()
        self.diagnose(row=self.new_row())
        _mark_notified(stale, extra={"test_delivery": "sent"})
        alert = AlertRecord.objects.get()
        self.assertEqual(alert.metadata["affected_requests"], 2)
        self.assertEqual(alert.metadata["test_delivery"], "sent")

    def test_repeat_delivery_is_reserved_while_an_earlier_send_is_in_flight(self, notify):
        self.diagnose()
        alert = AlertRecord.objects.get()
        previous_send = (timezone.now() - timedelta(hours=2)).isoformat()
        alert.metadata.update(
            last_notification_at=previous_send,
            last_notification_scheduled_at=previous_send,
        )
        alert.save(update_fields=["metadata"])
        with self.captureOnCommitCallbacks() as callbacks:
            self.diagnose(row=self.new_row())
            self.diagnose(row=self.new_row())
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(AlertRecord.objects.get().metadata["affected_requests"], 3)

    def test_expired_reservation_does_not_bypass_recent_delivery(self, notify):
        self.diagnose()
        alert = AlertRecord.objects.get()
        alert.metadata.update(
            last_notification_scheduled_at=(timezone.now() - timedelta(minutes=2)).isoformat(),
            last_notification_at=(timezone.now() - timedelta(minutes=1)).isoformat(),
        )
        alert.save(update_fields=["metadata"])
        with self.captureOnCommitCallbacks() as callbacks:
            self.diagnose(row=self.new_row())
        self.assertEqual(len(callbacks), 0)

    def test_expired_reservation_allows_retry_after_failed_delivery(self, notify):
        self.diagnose()
        alert = AlertRecord.objects.get()
        alert.metadata["last_notification_scheduled_at"] = (
            timezone.now() - timedelta(minutes=2)
        ).isoformat()
        alert.save(update_fields=["metadata"])
        with self.captureOnCommitCallbacks() as callbacks:
            self.diagnose(row=self.new_row())
        self.assertEqual(len(callbacks), 1)

    def test_organization_model_alert_is_owned_by_its_organization(self, notify):
        AlertPolicy.objects.all().delete()
        create_missing_default_alert_rules(
            owner_scope="organization", organization=self.org,
            default_keys={"ai_model.insufficient_credit"},
        )
        self.model.scope = "organization"
        self.model.organization = self.org
        self.model.save(update_fields=["scope", "organization"])
        self.diagnose()
        alert = AlertRecord.objects.get()
        self.assertEqual(alert.owner_scope, "organization")
        self.assertEqual(alert.organization_id, self.org.pk)

    def test_wrong_run_call_or_model_ownership_cannot_be_evidence(self, notify):
        foreign_org = Organization.objects.create(name="Other", key="credit-other")
        foreign_model = LensOrgModelLink.objects.create(
            organization=foreign_org, scope="organization", sl_config_uuid=uuid.uuid4(),
        )
        for kind in ("run", "call", "model", "success", "message_only"):
            with self.subTest(kind=kind):
                row = self.new_row()
                item = self.usage_item(row)
                if kind == "run":
                    item["metadata"]["run_uuid"] = str(uuid.uuid4())
                elif kind == "call":
                    item["id"] = str(uuid.uuid4())
                elif kind == "model":
                    item["metadata"]["selected_model_ref"] = str(foreign_model.sl_config_uuid)
                elif kind == "success":
                    item["success"] = True
                else:
                    item["error"] = "timeout"
                    item["message"] = _localized_credit_error() + "."
                result, _ = self.diagnose(row=row, items=[item])
                self.assertEqual(result, "unconfirmed")
                self.assertNotEqual(row.provider_error_state_json["status"], "confirmed")
        self.assertFalse(AlertRecord.objects.exists())

    def test_api_outage_preserves_error_and_bounds_retries(self, notify):
        for attempt in range(diagnostics.MAX_ATTEMPTS):
            token = self.claim()
            with patch.object(diagnostics.sl_client, "request_json", side_effect=RuntimeError("secret")):
                diagnostics.diagnose_claimed_ledger(
                    ledger_id=self.row.pk, claim_token=token,
                )
            self.row.refresh_from_db()
            self.assertNotIn("secret", str(self.row.provider_error_state_json))
            if attempt + 1 < diagnostics.MAX_ATTEMPTS:
                state = dict(self.row.provider_error_state_json)
                state["next_at"] = self.started.isoformat()
                self.row.provider_error_state_json = state
                self.row.save(update_fields=["provider_error_state_json"])
        self.assertEqual(self.row.provider_error_state_json["status"], "unconfirmed")
        self.assertFalse(diagnostics.claim_due_diagnostics())
        self.assertEqual(self.row.run_error, "EMPTY_AGENT_RESPONSE")
        self.assertFalse(AlertRecord.objects.exists())

    def test_disabled_default_is_not_reenabled(self, notify):
        self.diagnose()
        AlertPolicy.objects.update(enabled=False)
        self.diagnose(row=self.new_row())
        self.assertFalse(AlertPolicy.objects.get().enabled)
        self.assertEqual(AlertRecord.objects.get().metadata["affected_requests"], 1)

    def test_deleted_default_is_not_recreated_by_a_failure(self, notify):
        self.diagnose()
        AlertPolicy.objects.all().delete()
        result, _ = self.diagnose(row=self.new_row())
        self.assertEqual(result, "confirmed")
        self.assertFalse(AlertPolicy.objects.exists())
        self.assertEqual(AlertRecord.objects.count(), 1)

    def test_pagination_has_a_hard_request_limit(self, notify):
        token = self.claim()
        unrelated = [self.usage_item() for _ in range(diagnostics.PAGE_SIZE)]
        for item in unrelated:
            item["id"] = str(uuid.uuid4())
        with patch.object(diagnostics.sl_client, "request_json", side_effect=[
            self.run_payload(),
            *[{"results": unrelated} for _ in range(diagnostics.MAX_PAGES)],
        ]) as request:
            diagnostics.diagnose_claimed_ledger(ledger_id=self.row.pk, claim_token=token)
        self.assertEqual(request.call_count, 1 + diagnostics.MAX_PAGES)
        self.assertFalse(AlertRecord.objects.exists())

    def test_finds_evidence_on_second_page(self, notify):
        token = self.claim()
        unrelated = [dict(self.usage_item(), id=str(uuid.uuid4())) for _ in range(diagnostics.PAGE_SIZE)]
        with patch.object(diagnostics.sl_client, "request_json", side_effect=[
            self.run_payload(), {"results": unrelated}, {"results": [self.usage_item()]},
        ]):
            result = diagnostics.diagnose_claimed_ledger(ledger_id=self.row.pk, claim_token=token)
        self.assertEqual(result, "confirmed")

    def test_real_output_recovers_but_empty_output_and_other_model_do_not(self, notify):
        self.diagnose()
        for kind in ("empty_answer", "zero_output", "other_model", "valid"):
            with self.subTest(kind=kind):
                row = self.new_row(status="done")
                row.started_at = self.finished + timedelta(seconds=1)
                row.finished_at = self.finished + timedelta(seconds=5)
                row.save(update_fields=["started_at", "finished_at"])
                run = self.run_payload(row)
                run["answer"] = "" if kind == "empty_answer" else "A real answer."
                item = self.usage_item(row)
                item.update(success=True, error="", completion_tokens=0 if kind == "zero_output" else 10)
                if kind == "other_model":
                    item["metadata"]["selected_model_ref"] = str(uuid.uuid4())
                self.diagnose(row=row, run=run, items=[item])
                alert = AlertRecord.objects.get()
                self.assertEqual(
                    alert.status, AlertStatus.RESOLVED if kind == "valid" else AlertStatus.FIRING,
                )

    def test_capture_only_enqueues_state_not_remote_io(self, notify):
        with patch.object(diagnostics.sl_client, "request_json") as request:
            capture_ledger_usage(self.row, self.run_payload())
        request.assert_not_called()
        self.row.refresh_from_db()
        self.assertEqual(self.row.provider_error_state_json["status"], "pending")

    def test_success_before_failure_diagnosis_prevents_a_stale_incident(self, notify):
        diagnostics.initialize_diagnostic(self.row)
        self.row.save(update_fields=["provider_error_state_json"])
        successful = self.new_row(status="done")
        successful.started_at = self.finished + timedelta(seconds=1)
        successful.finished_at = self.finished + timedelta(seconds=5)
        successful.save(update_fields=["started_at", "finished_at"])
        run = self.run_payload(successful)
        run["answer"] = "A real answer."
        item = self.usage_item(successful)
        item.update(success=True, error="", completion_tokens=10)
        self.diagnose(row=successful, run=run, items=[item])
        successful.refresh_from_db()
        self.assertEqual(successful.provider_error_state_json["status"], "checked")
        self.row.refresh_from_db()
        token = self.row.provider_error_state_json["claim_token"]
        with patch.object(diagnostics.sl_client, "request_json", side_effect=[
            self.run_payload(), {"results": [self.usage_item()]},
        ]):
            diagnostics.diagnose_claimed_ledger(ledger_id=self.row.pk, claim_token=token)
        self.assertFalse(AlertRecord.objects.exists())

    def later_answer(self, *, kind="valid", organization=None):
        row = self.new_row(status="done")
        row.started_at = self.finished + timedelta(seconds=1)
        row.finished_at = self.finished + timedelta(seconds=5)
        if organization is not None:
            row.organization = organization
        row.save(update_fields=["started_at", "finished_at", "organization"])
        call_id = str(uuid.uuid4())
        run = self.run_payload(row)
        run.update(
            answer="" if kind == "empty_answer" else "A real later answer.",
            model_calls=[{"uuid": call_id, "success": True, "completion_tokens": 10}],
        )
        item = self.usage_item(row)
        item.update(
            id=call_id, success=True, error="",
            completion_tokens=0 if kind == "zero_output" else 10,
        )
        if kind == "other_model":
            other = LensOrgModelLink.objects.create(
                scope="platform", organization=None, sl_config_uuid=uuid.uuid4(),
            )
            item["metadata"]["selected_model_ref"] = str(other.sl_config_uuid)
        elif kind == "wrong_run":
            item["metadata"]["run_uuid"] = str(uuid.uuid4())
        elif kind == "wrong_call":
            item["id"] = str(uuid.uuid4())
        elif kind == "incomplete":
            run["model_calls"].append({"uuid": str(uuid.uuid4())})
        return row, run, item

    def diagnose_late_failure(self, *, kind="valid", organization=None):
        failed_run = self.run_payload()
        failed_item = self.usage_item()
        # HFL still sees the older run streaming when the newer answer arrives.
        self.row.run_status = "streaming"
        self.row.run_error = ""
        self.row.save(update_fields=["run_status", "run_error"])
        successful, run, item = self.later_answer(kind=kind, organization=organization)
        capture_ledger_usage(successful, run)
        successful.refresh_from_db()
        self.assertEqual(successful.provider_error_state_json, {})
        capture_ledger_usage(self.row, failed_run)
        self.row.refresh_from_db()
        token = self.claim()

        def read(method, path, **kwargs):
            if f"/runs/{self.row.sl_run_uuid}/" in path:
                return failed_run
            if f"/runs/{successful.sl_run_uuid}/" in path:
                if kind == "unavailable":
                    raise diagnostics.sl_client.LensBridgeUnavailable()
                return run
            if "/llm-usage/" in path:
                return {"results": [failed_item, item]}
            raise AssertionError(path)

        with patch.object(diagnostics.sl_client, "request_json", side_effect=read) as request:
            result = diagnostics.diagnose_claimed_ledger(
                ledger_id=self.row.pk, claim_token=token,
            )
        self.row.refresh_from_db()
        successful.refresh_from_db()
        self.assertEqual(result, "confirmed")
        self.assertEqual(self.row.provider_error_state_json["code"], diagnostics.INSUFFICIENT_CREDIT)
        return successful, request

    def test_late_failure_capture_checks_real_later_answer_before_firing(self, notify):
        with self.captureOnCommitCallbacks() as callbacks:
            successful, request = self.diagnose_late_failure()
        self.assertEqual(request.call_count, 4)
        self.assertEqual(successful.provider_error_state_json["status"], "checked")
        self.assertEqual(
            successful.provider_error_state_json["recovered_model_ref"],
            str(self.model.sl_config_uuid),
        )
        self.assertNotIn("must-not-persist", str(successful.provider_error_state_json))
        self.assertFalse(AlertRecord.objects.exists())
        self.assertEqual(len(callbacks), 0)

    def test_later_answer_from_another_org_can_recover_a_platform_model(self, notify):
        other = Organization.objects.create(name="Other", key="credit-recovery-other")
        successful, _ = self.diagnose_late_failure(organization=other)
        self.assertEqual(successful.provider_error_state_json["status"], "checked")
        self.assertFalse(AlertRecord.objects.exists())

    def test_later_answers_without_correlated_output_cannot_suppress_failure(self, notify):
        for kind in (
            "empty_answer", "zero_output", "other_model", "wrong_run",
            "wrong_call", "incomplete", "unavailable",
        ):
            with self.subTest(kind=kind):
                successful, _ = self.diagnose_late_failure(kind=kind)
                self.assertEqual(successful.provider_error_state_json, {})
                self.assertEqual(AlertRecord.objects.get().status, AlertStatus.FIRING)
                # Keep each scenario independent without unrelated candidates.
                successful.delete()
                AlertRecord.objects.all().delete()
                self.row.provider_error_state_json = {}
                self.row.save(update_fields=["provider_error_state_json"])

    def test_other_org_answer_is_not_checked_for_an_organization_model(self, notify):
        self.model.scope = "organization"
        self.model.organization = self.org
        self.model.save(update_fields=["scope", "organization"])
        create_missing_default_alert_rules(
            owner_scope="organization", organization=self.org,
            default_keys={"ai_model.insufficient_credit"},
        )
        other = Organization.objects.create(name="Other", key="credit-unrelated")
        successful, request = self.diagnose_late_failure(organization=other)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(successful.provider_error_state_json, {})
        self.assertEqual(AlertRecord.objects.get().organization_id, self.org.pk)

    def test_historical_recovery_reads_are_bounded(self, notify):
        candidates = {}
        for _ in range(diagnostics.MAX_RECOVERY_RUNS + 2):
            row, run, _ = self.later_answer(kind="empty_answer")
            candidates[str(row.sl_run_uuid)] = run
        token = self.claim()

        def read(method, path, **kwargs):
            if f"/runs/{self.row.sl_run_uuid}/" in path:
                return self.run_payload()
            for run_id, run in candidates.items():
                if f"/runs/{run_id}/" in path:
                    return run
            if "/llm-usage/" in path:
                return {"results": [self.usage_item()]}
            raise AssertionError(path)

        with patch.object(diagnostics.sl_client, "request_json", side_effect=read) as request:
            result = diagnostics.diagnose_claimed_ledger(
                ledger_id=self.row.pk, claim_token=token,
            )
        self.assertEqual(result, "confirmed")
        self.assertEqual(request.call_count, 2 + diagnostics.MAX_RECOVERY_RUNS * 2)
        self.assertEqual(AlertRecord.objects.get().status, AlertStatus.FIRING)

    def test_incomplete_usage_page_cannot_prove_recovery(self, notify):
        self.diagnose()
        successful = self.new_row(status="done")
        run = self.run_payload(successful)
        run["answer"] = "A real answer."
        run["model_calls"].append({"uuid": str(uuid.uuid4())})
        item = self.usage_item(successful)
        item.update(success=True, error="", completion_tokens=10)
        self.diagnose(row=successful, run=run, items=[item])
        self.assertEqual(AlertRecord.objects.get().status, AlertStatus.FIRING)

    def test_expired_lease_cannot_be_finished_by_old_worker(self, notify):
        old = self.claim()
        self.row.refresh_from_db()
        state = dict(self.row.provider_error_state_json)
        state["next_at"] = self.started.isoformat()
        self.row.provider_error_state_json = state
        self.row.save(update_fields=["provider_error_state_json"])
        new = dict(diagnostics.claim_due_diagnostics())[self.row.pk]
        self.assertNotEqual(new, old)
        with patch.object(diagnostics.sl_client, "request_json") as request:
            result = diagnostics.diagnose_claimed_ledger(
                ledger_id=self.row.pk, claim_token=old,
            )
        self.assertEqual(result, "skipped")
        request.assert_not_called()
