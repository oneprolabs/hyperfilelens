"""Tests for optional node list pagination."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.node.models import Node
from apps.node.models.base import NodeRole

User = get_user_model()


class NodeListPaginationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="node-page-user@test.local",
            email="node-page-user@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        self.client.force_authenticate(user=self.user)
        registered_at = timezone.now() - timedelta(days=1)
        for idx in range(35):
            node = Node.objects.create(
                organization=self.org,
                name=f"agent-{idx:02d}",
                role=NodeRole.AGENT,
                ip_address=f"10.0.0.{idx + 1}",
            )
            Node.objects.filter(pk=node.pk).update(
                created_at=registered_at + timedelta(minutes=idx)
            )

    def test_list_without_page_size_returns_all(self):
        response = self.client.get(
            reverse("node-list"),
            {"role": "agent"},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 35)
        self.assertEqual(response.data[0]["name"], "agent-34")
        self.assertEqual(response.data[-1]["name"], "agent-00")

    def test_list_with_page_size_returns_paginated_payload(self):
        response = self.client.get(
            reverse("node-list"),
            {"role": "agent", "page": 1, "page_size": 30},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 35)
        self.assertEqual(len(response.data["results"]), 30)
        self.assertEqual(response.data["results"][0]["name"], "agent-34")
        self.assertEqual(response.data["results"][0]["availability"], "offline")
        self.assertIsNotNone(
            response.data["results"][0]["availability_updated_at"]
        )

        page_two = self.client.get(
            reverse("node-list"),
            {"role": "agent", "page": 2, "page_size": 30},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(page_two.status_code, status.HTTP_200_OK)
        self.assertEqual(len(page_two.data["results"]), 5)
        self.assertEqual(page_two.data["results"][-1]["name"], "agent-00")

    def test_list_uses_descending_id_to_break_registration_time_ties(self):
        tied_nodes = list(Node.objects.filter(organization=self.org).order_by("id")[:2])
        tied_at = timezone.now() + timedelta(days=1)
        Node.objects.filter(id__in=[node.id for node in tied_nodes]).update(
            created_at=tied_at
        )

        response = self.client.get(
            reverse("node-list"),
            {"role": "agent", "page": 1, "page_size": 30},
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["id"] for row in response.data["results"][:2]],
            sorted((node.id for node in tied_nodes), reverse=True),
        )

    def test_source_host_order_does_not_change_other_node_roles(self):
        Node.objects.create(
            organization=self.org,
            name="proxy-z",
            role=NodeRole.PROXY,
        )
        Node.objects.create(
            organization=self.org,
            name="proxy-a",
            role=NodeRole.PROXY,
        )

        response = self.client.get(
            reverse("node-list"),
            {"role": "proxy", "page": 1, "page_size": 30},
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["name"] for row in response.data["results"]],
            ["proxy-a", "proxy-z"],
        )

    def test_list_search_filters_by_name(self):
        response = self.client.get(
            reverse("node-list"),
            {"role": "agent", "search": "agent-03", "page": 1, "page_size": 30},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "agent-03")

    def test_list_field_search_filters_only_selected_field(self):
        response = self.client.get(
            reverse("node-list"),
            {"role": "agent", "search": "10.0.0.1", "search_field": "name", "page": 1, "page_size": 30},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

        by_ip = self.client.get(
            reverse("node-list"),
            {"role": "agent", "search": "10.0.0.1", "search_field": "ip", "page": 1, "page_size": 30},
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(by_ip.status_code, status.HTTP_200_OK)
        self.assertEqual(by_ip.data["count"], 11)
        self.assertEqual(by_ip.data["results"][0]["name"], "agent-18")
