from unittest import mock

from django.db import connection
from django.test import TransactionTestCase

from apps.iam.models import Organization
from apps.node.models import Node
from apps.source.models import SourceResource
from apps.source.services.interface import create_source_resource
from apps.source.services.internal.backup_source_delete import delete_backup_sources


class SourceRemoteTransactionBoundaryTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.org = Organization.objects.create(
            key="source-remote-transaction-org",
            name="Source Remote Transaction Org",
        )
        self.proxy = Node.objects.create(
            organization=self.org,
            name="source-remote-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )

    @mock.patch(
        "apps.source.tasks.connection_probe.queue_source_resource_capacity_probe"
    )
    def test_create_source_probe_queues_after_resource_transaction_commits(
        self,
        queue_probe,
    ):
        def enqueue(**_kwargs):
            self.assertFalse(connection.in_atomic_block)
            return True

        queue_probe.side_effect = enqueue

        resource = create_source_resource(
            organization=self.org,
            user=None,
            name="post-commit-create-nas",
            resource_type="nas",
            config={
                "protocol": "nfs",
                "server": "192.0.2.10",
                "export_path": "/source",
            },
            bound_node_id=self.proxy.id,
        )

        self.assertTrue(SourceResource.objects.filter(pk=resource.id).exists())
        queue_probe.assert_called_once_with(
            resource_id=resource.id,
            probe_token=str(resource.connection_probe_token),
            expected_bound_node_id=self.proxy.id,
        )

    @mock.patch(
        "apps.source.tasks.connection_probe.probe_source_resource_capacity.apply_async",
        side_effect=RuntimeError("broker unavailable"),
    )
    def test_create_source_probe_enqueue_failure_keeps_committed_resource(
        self,
        apply_async,
    ):
        resource = create_source_resource(
            organization=self.org,
            user=None,
            name="post-commit-probe-failure-nas",
            resource_type="nas",
            config={
                "protocol": "nfs",
                "server": "192.0.2.12",
                "export_path": "/source",
            },
            bound_node_id=self.proxy.id,
        )

        self.assertTrue(SourceResource.objects.filter(pk=resource.id).exists())
        apply_async.assert_called_once()
        resource.refresh_from_db()
        self.assertEqual(resource.connection_test_status, "failed")
        self.assertIsNone(resource.connection_probe_token)

    @mock.patch(
        "apps.source.services.internal.backup_source_delete.unmount_resource"
    )
    def test_unregister_unmount_runs_outside_database_cleanup_transaction(self, unmount):
        resource = SourceResource.objects.create(
            organization=self.org,
            name="outside-transaction-unmount-nas",
            resource_type="nas",
            config={
                "protocol": "nfs",
                "server": "192.0.2.11",
                "export_path": "/source",
            },
            bound_node=self.proxy,
            mount_status="mounted",
        )
        resource_id = resource.id

        def perform_unmount(*, resource, force):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(resource.id, resource_id)
            return {"success": True, "message": "Unmounted"}

        unmount.side_effect = perform_unmount

        result = delete_backup_sources(
            org=self.org,
            ids=[f"nas:{resource.id}"],
        )

        self.assertEqual(result["result"], "success")
        self.assertTrue(SourceResource.all_objects.get(pk=resource.id).is_deleted)
        unmount.assert_called_once_with(resource=resource, force=False)
