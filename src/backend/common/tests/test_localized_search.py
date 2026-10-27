"""Localized search aliases must not bypass pagination or organization filters."""

import json

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.alert.models import AlertRecord
from apps.iam.models import Membership, Organization
from apps.monitor.services.events import record_operational_event
from apps.notification.models import NotificationChannel, NotificationLog
from common.drf.search import parse_search_aliases, text_search_query


class SearchAliasValidationTests(SimpleTestCase):
    def test_optional_deduplicated_aliases_and_empty_search(self):
        self.assertEqual(parse_search_aliases({}), ())
        self.assertEqual(
            parse_search_aliases({"search_aliases": '[" Backup failed: ", "Backup failed:"]'}),
            ("Backup failed:",),
        )
        self.assertFalse(text_search_query("", ("title",), ("Backup failed:",)))

    def test_invalid_and_unbounded_aliases_are_rejected(self):
        for value in ["invalid", "{}", '"text"', '[1]', '[""]', '[" "]',
                      json.dumps(["x"] * 65), json.dumps(["x" * 201]), "[" * 1500]:
            with self.subTest(value=value[:50]):
                with self.assertRaises(ValidationError):
                    parse_search_aliases({"search_aliases": value})


class LocalizedSearchApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(key="localized-search", name="Search")
        self.other = Organization.objects.create(key="other-localized-search", name="Other")
        user = get_user_model().objects.create_user(username="localized-search@test.local", password="test-pass")
        Membership.objects.create(user=user, organization=self.org, role=Membership.Role.ADMIN)
        self.client.force_authenticate(user=user)

    def alert(self, organization, title, **values):
        return AlertRecord.objects.create(
            organization=organization, title=title, type="task", severity="warning",
            status="firing", resource_type="task", fingerprint=title, **values,
        )

    def event(self, organization, title):
        return record_operational_event(
            organization_id=organization.id, title=title, event_type="task.failed",
            category="protection", severity="warning", resource_type="task",
        )

    def get(self, path, params):
        return self.client.get(path, params, HTTP_X_ORG_KEY=self.org.key, secure=True)

    def test_alert_aliases_are_additive_scoped_and_applied_before_pagination_and_stats(self):
        self.alert(self.org, "Task alert: task_failed")
        self.alert(self.org, "localized failure")
        self.alert(self.org, "Unrelated alert")
        self.alert(self.other, "Task alert: task_failed")
        params = {"search": "localized failure", "search_aliases": '["Task alert: task_failed"]', "page_size": 1}
        response = self.get("/api/v1/alerts/records/", params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(len(response.data["results"]), 1)
        params["page"] = 2
        second = self.get("/api/v1/alerts/records/", params)
        self.assertNotEqual(response.data["results"][0]["id"], second.data["results"][0]["id"])
        stats = self.get("/api/v1/alerts/records/stats/", params)
        self.assertEqual(stats.data["total"], 2)
        self.assertEqual(stats.data["firing"], 2)
        self.assertEqual(stats.data["warning"], 2)
        plain = self.get("/api/v1/alerts/records/", {"search": "localized failure"})
        self.assertEqual(plain.data["count"], 1)
        params["status"] = "resolved"
        params["page"] = 1
        self.assertEqual(self.get("/api/v1/alerts/records/", params).data["count"], 0)

    def test_event_aliases_preserve_literal_matches_and_scoped_severity_counts(self):
        self.event(self.org, "Backup failed: daily")
        self.event(self.org, "localized failure")
        self.event(self.org, "Unrelated event")
        self.event(self.other, "Backup failed: private")
        params = {"search": "localized failure", "search_aliases": '["Backup failed:"]', "page_size": 1}
        response = self.get("/api/v1/monitors/events/", params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["stats"]["total"], 2)
        self.assertEqual(response.data["stats"]["warning"], 2)
        self.assertEqual(len(response.data["results"]), 1)
        params["page"] = 2
        second = self.get("/api/v1/monitors/events/", params)
        self.assertNotEqual(response.data["results"][0]["id"], second.data["results"][0]["id"])
        params["severity"] = "critical"
        self.assertEqual(self.get("/api/v1/monitors/events/", params).data["count"], 0)

    def test_aliases_do_not_replace_scope_and_are_validated_on_all_endpoints(self):
        for path in ["/api/v1/alerts/records/", "/api/v1/alerts/records/stats/", "/api/v1/monitors/events/", "/api/v1/notifications/logs/"]:
            with self.subTest(path=path):
                response = self.get(path, {"search": "label", "search_aliases": '[" "]'})
                self.assertEqual(response.status_code, 400, response.data)

    def test_global_alert_search_retains_id_status_and_severity_matching(self):
        alert = self.alert(self.org, "Task alert: task_failed")
        for search in [str(alert.id), "firing", "warning"]:
            with self.subTest(search=search):
                response = self.get("/api/v1/alerts/records/", {"search": search, "search_context": "global"})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["count"], 1)
        self.assertEqual(self.get("/api/v1/alerts/records/", {"search": "warning"}).data["count"], 0)

    def test_delivery_aliases_are_scoped_and_shared_by_list_and_statistics(self):
        channel = NotificationChannel.objects.create(organization=self.org, name="Email", channel_type="email")
        other_channel = NotificationChannel.objects.create(organization=self.other, name="Other", channel_type="email")
        for organization, current_channel in [(self.org, channel), (self.other, other_channel)]:
            alert = self.alert(organization, "Task alert: task_failed")
            NotificationLog.objects.create(
                organization=organization, channel=current_channel, alert_record_id=alert.id,
                status="success", event_type="alert.firing",
            )
        params = {"search": "localized failure", "search_aliases": '["Task alert: task_failed"]'}
        response = self.get("/api/v1/notifications/logs/", params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        stats = self.get("/api/v1/notifications/logs/stats/", params)
        self.assertEqual(stats.status_code, 200, stats.data)
        self.assertEqual(stats.data["total"], 1)
