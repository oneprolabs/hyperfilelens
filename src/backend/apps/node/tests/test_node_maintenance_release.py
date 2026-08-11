"""Authenticated manual-maintenance release URLs for existing nodes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.node.models import Node, NodeToken


User = get_user_model()


class NodeMaintenanceReleaseTests(APITestCase):
    def setUp(self):
        self.media_tmp = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_tmp.name)
        self.settings_override.enable()
        self.env_override = patch.dict(os.environ, {"AGENT_VERSION": "1.0.1"})
        self.env_override.start()

        release_dir = Path(self.media_tmp.name) / "agent-releases" / "1.0.1"
        release_dir.mkdir(parents=True)
        (release_dir / "hfl-agent-1.0.1-linux-amd64.tar.gz").write_bytes(b"release")

        self.user = User.objects.create_user(
            username="maintenance-release@test.local",
            email="maintenance-release@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org, _ = provision_registered_user_tenant(self.user)
        self.node = Node.objects.create(
            organization=self.org,
            name="offline-agent",
            role=Node.Role.AGENT,
            metadata={"inventory": {"os": "linux", "arch": "amd64"}},
        )
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        self.env_override.stop()
        self.settings_override.disable()
        self.media_tmp.cleanup()

    def test_existing_node_gets_signed_release_without_enrollment_token(self):
        before = NodeToken.objects.count()
        response = self.client.post(
            reverse("node-maintenance-release", args=[self.node.id]),
            {},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["version"], "1.0.1")
        self.assertEqual(response.data["platform"], "linux")
        self.assertIn("/media/agent-releases/1.0.1/", response.data["download_url"])
        self.assertEqual(NodeToken.objects.count(), before)

        parsed = urlparse(response.data["download_url"])
        signed = parse_qs(parsed.query)["t"][0]
        auth = self.client.get(
            reverse("enrollment-agent-releases-auth"),
            {"t": signed},
            HTTP_X_ORIGINAL_URI=f"{parsed.path}?t={signed}",
        )
        self.assertEqual(auth.status_code, status.HTTP_204_NO_CONTENT)

    def test_other_organization_cannot_issue_release_for_node(self):
        other = User.objects.create_user(
            username="other-maintenance@test.local",
            email="other-maintenance@test.local",
            password="Pass1234",
            is_active=True,
        )
        other_org, _ = provision_registered_user_tenant(other)
        self.client.force_authenticate(user=other)

        response = self.client.post(
            reverse("node-maintenance-release", args=[self.node.id]),
            {},
            format="json",
            HTTP_X_ORG_KEY=other_org.key,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
