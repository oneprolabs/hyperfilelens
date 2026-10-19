"""Lifecycle notifications are optional and contain only typed identities."""

from unittest.mock import patch

from django.dispatch import Signal
from django.test import SimpleTestCase

from common.resource_lifecycle import notify_resource_lifecycle_changed


class ResourceLifecycleNotificationTests(SimpleTestCase):
    def test_no_subscriber_is_a_database_free_noop(self):
        with patch("common.resource_lifecycle.resource_lifecycle_changed", Signal()):
            notify_resource_lifecycle_changed(
                organization_id=1, resource_type="repository", resource_id=7,
            )

    def test_extensions_receive_the_exact_organization_and_resource_identity(self):
        signal = Signal()
        received = []

        def observer(sender, **kwargs):
            received.append({key: value for key, value in kwargs.items() if key != "signal"})

        signal.connect(observer, weak=False)
        with patch("common.resource_lifecycle.resource_lifecycle_changed", signal):
            notify_resource_lifecycle_changed(
                organization_id=2, resource_type="repository", resource_id=17,
            )
        self.assertEqual(received, [{
            "organization_id": 2, "resource_type": "repository", "resource_id": 17,
        }])
