"""Bulk membership writes are tenant-scoped and roll back as one operation."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.iam.models import Membership, Organization
from apps.iam.tests.test_membership_api import _CommunitySpiMixin
from apps.iam.views import MembershipViewSet


class MembershipBulkTests(_CommunitySpiMixin, APITestCase):
    def setUp(self):
        super().setUp()
        users = get_user_model()
        self.org = Organization.objects.create(key="member-bulk", name="Members")
        self.other_org = Organization.objects.create(key="member-bulk-other", name="Other")
        self.actor = users.objects.create_user(username="member-bulk-actor")
        self.actor_membership = Membership.objects.create(
            organization=self.org, user=self.actor, is_active=True
        )
        self.targets = []
        for index in range(3):
            user = users.objects.create_user(username=f"member-bulk-{index}")
            self.targets.append(Membership.objects.create(
                organization=self.org, user=user, is_active=index == 0
            ))
        self.foreign = Membership.objects.create(
            organization=self.other_org,
            user=users.objects.create_user(username="member-bulk-foreign"),
            is_active=True,
        )
        self.url = "/api/v1/iam/memberships/bulk/"
        self.headers = {"HTTP_X_ORG_KEY": self.org.key}
        self.client.force_authenticate(user=self.actor)

    def post(self, action, ids, **extra):
        return self.client.post(
            self.url,
            {"action": action, "membership_ids": ids, **extra},
            format="json",
            **self.headers,
        )

    def test_activate_checks_entire_quota_and_skips_already_active(self):
        with patch("apps.subscription.services.interface.enforce_license_quota") as quota:
            response = self.post("activate", [row.pk for row in self.targets])
        self.assertEqual(response.status_code, 200, response.data)
        quota.assert_any_call(self.org, "max_users", additional=2)
        payload = response.data.get("data", response.data)
        self.assertEqual(payload["updated"], 2)
        self.assertEqual(payload["skipped"], 1)
        self.assertFalse(Membership.objects.filter(
            pk__in=[row.pk for row in self.targets], is_active=False
        ).exists())

    def test_deactivate_preserves_users_and_skips_disabled_members(self):
        response = self.post("deactivate", [row.pk for row in self.targets])
        self.assertEqual(response.status_code, 200, response.data)
        payload = response.data.get("data", response.data)
        self.assertEqual(payload["updated"], 1)
        self.assertEqual(payload["skipped"], 2)
        self.assertEqual(get_user_model().objects.filter(
            pk__in=[row.user_id for row in self.targets]
        ).count(), 3)

    def test_remove_deletes_affiliations_not_accounts_or_other_tenants(self):
        ids = [row.pk for row in self.targets[:2]]
        user_ids = [row.user_id for row in self.targets[:2]]
        response = self.post("remove", ids)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Membership.objects.filter(pk__in=ids).exists())
        self.assertEqual(get_user_model().objects.filter(pk__in=user_ids).count(), 2)
        self.assertTrue(Membership.objects.filter(pk=self.foreign.pk).exists())
        self.assertEqual(AuditLog.objects.filter(
            organization=self.org, action="membership.remove"
        ).count(), 2)

    def test_foreign_or_missing_target_prevents_all_mutations(self):
        for invalid in (self.foreign.pk, 2**63 - 1):
            with self.subTest(invalid=invalid):
                response = self.post("remove", [self.targets[0].pk, invalid])
                self.assertEqual(response.status_code, 404, response.data)
                self.assertTrue(Membership.objects.filter(pk=self.targets[0].pk).exists())
        self.assertFalse(AuditLog.objects.filter(
            organization=self.org, action="membership.remove"
        ).exists())

    def test_invalid_duplicate_unbounded_and_extra_fields_are_rejected(self):
        for ids in (
            [], [self.targets[0].pk] * 2, list(range(1, 202)),
            [0], [-1], [2**63], [True],
        ):
            with self.subTest(ids=ids):
                self.assertEqual(self.post("remove", ids).status_code, 400)
        self.assertEqual(self.post(
            "remove", [self.targets[0].pk], organization=self.other_org.pk
        ).status_code, 400)
        self.assertEqual(self.post("delete", [self.targets[0].pk]).status_code, 400)

    def test_quota_failure_rolls_back_all_activations(self):
        ids = [row.pk for row in self.targets[1:]]
        with patch(
            "apps.subscription.services.interface.enforce_license_quota",
            side_effect=ValidationError("Member quota exceeded."),
        ):
            response = self.post("activate", ids)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Membership.objects.filter(pk__in=ids, is_active=True).exists())
        self.assertFalse(AuditLog.objects.filter(
            organization=self.org, action="membership.update"
        ).exists())

    def test_later_remove_failure_rolls_back_members_and_audit_entries(self):
        original = MembershipViewSet.perform_destroy
        ids = [row.pk for row in self.targets[:2]]

        def fail_second(view, target):
            if target.pk == ids[1]:
                raise ValidationError("Cannot remove this member.")
            original(view, target)

        with patch.object(MembershipViewSet, "perform_destroy", fail_second):
            response = self.post("remove", ids)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Membership.objects.filter(pk__in=ids).count(), 2)
        self.assertFalse(AuditLog.objects.filter(
            organization=self.org, action="membership.remove"
        ).exists())

    def test_authority_is_rechecked_after_acquiring_the_lock(self):
        with patch(
            "apps.iam.views.CanManageMembershipOperation.has_permission",
            side_effect=[True, False],
        ):
            response = self.post("remove", [self.targets[0].pk])
        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(Membership.objects.filter(pk=self.targets[0].pk).exists())

    def test_unaffiliated_actor_cannot_use_bulk_actions(self):
        self.client.force_authenticate(user=self.foreign.user)
        response = self.post("remove", [self.targets[0].pk])
        self.assertEqual(response.status_code, 403, response.data)

    def test_globally_disabled_actor_is_rechecked_even_with_a_stale_request_user(self):
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        response = self.post("remove", [self.targets[0].pk])
        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(Membership.objects.filter(pk=self.targets[0].pk).exists())

    def test_last_affiliations_cannot_be_removed_together(self):
        Membership.objects.filter(
            organization=self.org
        ).exclude(pk__in=[self.actor_membership.pk, self.targets[0].pk]).delete()
        response = self.post(
            "remove", [self.actor_membership.pk, self.targets[0].pk]
        )
        self.assertIn(response.status_code, (400, 403), response.data)
        self.assertEqual(Membership.objects.filter(organization=self.org).count(), 2)
