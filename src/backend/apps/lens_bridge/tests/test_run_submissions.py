import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.models import LensRunSubmission, LensSessionLink
from apps.lens_bridge.services import run_submissions
from apps.lens_bridge.tasks.run_submission_recovery import (
    execute_run_submission_recovery_task,
)


class RunSubmissionRecoveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="run-submission-owner",
            email="run-submission-owner@example.com",
            password="test-password",
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        self.session = LensSessionLink.objects.create(
            organization=self.org,
            hfl_user=self.user,
            sl_session_uuid=uuid.uuid4(),
            title="Recoverable Chat",
            lifecycle_status=LensSessionLink.LifecycleStatus.READY,
            provision_phase=LensSessionLink.ProvisionPhase.READY,
        )

    @patch("apps.lens_bridge.services.run_submissions.sl_client.request_json")
    def test_claimed_submission_is_replayed_and_bound(self, request_json):
        run_uuid = uuid.uuid4()
        submission = LensRunSubmission.objects.create(
            organization=self.org,
            hfl_user=self.user,
            session_link=self.session,
            idempotency_key="recover-me",
            question="Recover this Run",
        )
        request_json.return_value = {
            "uuid": str(run_uuid),
            "status": "queued",
            "idempotency_key": "recover-me",
            "created_at": "2026-08-11T02:00:00Z",
        }
        claims = run_submissions.claim_due_submissions(limit=10)

        self.assertEqual(len(claims), 1)
        result = execute_run_submission_recovery_task(
            submission_id=submission.id,
            claim_token=str(claims[0][1]),
        )

        submission.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(result["run_uuid"], str(run_uuid))
        self.assertEqual(submission.status, LensRunSubmission.Status.BOUND)
        self.assertEqual(submission.sl_run_uuid, run_uuid)
        self.assertIsNone(submission.recovery_claim_token)
        self.assertEqual(self.session.active_run_uuid, run_uuid)
        request_json.assert_called_once_with(
            "POST",
            f"/api/lens/sessions/{self.session.sl_session_uuid}/runs/",
            json_body={
                "question": "Recover this Run",
                "idempotency_key": "recover-me",
            },
            hfl_user=self.user,
        )

    def test_stale_worker_does_not_fail_a_newer_recovery_claim(self):
        submission = LensRunSubmission.objects.create(
            organization=self.org,
            hfl_user=self.user,
            session_link=self.session,
            idempotency_key="claim-race",
            question="Keep the newer claim",
        )
        claims = run_submissions.claim_due_submissions(limit=10)
        stale_token = claims[0][1]
        current_token = uuid.uuid4()
        LensRunSubmission.objects.filter(pk=submission.pk).update(
            recovery_claim_token=current_token
        )

        result = execute_run_submission_recovery_task(
            submission_id=submission.id,
            claim_token=str(stale_token),
        )

        submission.refresh_from_db()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(submission.status, LensRunSubmission.Status.PENDING)
        self.assertEqual(submission.recovery_claim_token, current_token)

    @patch("apps.lens_bridge.services.run_submissions.sl_client.request_json")
    def test_contract_error_fails_without_retrying_forever(self, request_json):
        submission = LensRunSubmission.objects.create(
            organization=self.org,
            hfl_user=self.user,
            session_link=self.session,
            idempotency_key="invalid-contract",
            question="Do not retry this response forever",
        )
        request_json.return_value = {
            "status": "queued",
            "idempotency_key": "invalid-contract",
        }
        claims = run_submissions.claim_due_submissions(limit=10)

        result = execute_run_submission_recovery_task(
            submission_id=submission.id,
            claim_token=str(claims[0][1]),
        )

        submission.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(submission.status, LensRunSubmission.Status.FAILED)
        self.assertEqual(submission.recovery_attempts, 1)
        self.assertIsNone(submission.recovery_next_at)
        self.assertIn("omitted its UUID", submission.last_error)
