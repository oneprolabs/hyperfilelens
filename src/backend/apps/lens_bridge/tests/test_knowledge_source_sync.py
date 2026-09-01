from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.db import transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import knowledge_source_sync
from apps.lens_bridge.services.managed_datasource import ManagedDatasourcePending
from apps.lens_bridge.services.knowledge_source_sync import (
    _run_phase_push_assistant,
    _restore_selected_paths,
    map_scope_to_workspace,
)
from apps.node.models import Node


class KnowledgeSourceSyncLeaseTests(TransactionTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="sync-lease",
            name="Sync Lease",
        )
        gateway = Node.objects.create(
            organization=self.organization,
            name="sync-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=self.organization,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            sidecar_status=LensGatewayLink.SidecarStatus.ONLINE,
        )
        self.knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.organization,
            name="Lease source",
            gateway=gateway,
            gateway_link=gateway_link,
            source_path="/data",
            status=LensKnowledgeSource.Status.SYNCING,
            sync_next_poll_at=timezone.now(),
        )

    @patch("apps.lens_bridge.services.knowledge_source_sync._run_sync_pipeline")
    def test_duplicate_delivery_does_not_enter_pipeline(self, run_pipeline):
        claim_token, status = knowledge_source_sync._claim_sync(
            organization_id=self.organization.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.assertIsNotNone(claim_token)
        self.assertEqual(status, "claimed")
        result = knowledge_source_sync.run_knowledge_source_sync(
            organization_id=self.organization.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.assertEqual(result["status"], "busy")
        run_pipeline.assert_not_called()

    @patch(
        "apps.lens_bridge.services.knowledge_source_sync._run_sync_pipeline",
        side_effect=ManagedDatasourcePending("conversion pending"),
    )
    def test_pending_conversion_releases_claim_with_durable_poll(self, _run):
        before = timezone.now()

        result = knowledge_source_sync.run_knowledge_source_sync(
            organization_id=self.organization.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.knowledge_source.refresh_from_db()
        self.assertEqual(result["status"], "waiting")
        self.assertIsNone(self.knowledge_source.sync_claim_token)
        self.assertIsNone(self.knowledge_source.sync_claimed_at)
        self.assertGreater(self.knowledge_source.sync_next_poll_at, before)

    @patch(
        "apps.lens_bridge.services.knowledge_source_sync._run_sync_pipeline",
        side_effect=knowledge_source_sync.KnowledgeSourceSyncPending(
            "restore pending",
            retry_after_seconds=7,
        ),
    )
    def test_pending_restore_releases_claim_with_durable_poll(self, _run):
        before = timezone.now()

        result = knowledge_source_sync.run_knowledge_source_sync(
            organization_id=self.organization.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.knowledge_source.refresh_from_db()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["retry_after_seconds"], 7)
        self.assertIsNone(self.knowledge_source.sync_claim_token)
        self.assertIsNone(self.knowledge_source.sync_claimed_at)
        self.assertGreater(self.knowledge_source.sync_next_poll_at, before)

    @patch("apps.lens_bridge.services.knowledge_source_sync._run_sync_pipeline")
    def test_success_releases_claim_and_poll_marker(self, run_pipeline):
        run_pipeline.return_value = {
            "knowledge_source_id": self.knowledge_source.id,
            "status": LensKnowledgeSource.Status.READY,
        }

        result = knowledge_source_sync.run_knowledge_source_sync(
            organization_id=self.organization.id,
            knowledge_source_id=self.knowledge_source.id,
        )

        self.knowledge_source.refresh_from_db()
        self.assertEqual(result["status"], LensKnowledgeSource.Status.READY)
        self.assertIsNone(self.knowledge_source.sync_claim_token)
        self.assertIsNone(self.knowledge_source.sync_claimed_at)
        self.assertIsNone(self.knowledge_source.sync_next_poll_at)

    def test_reconciler_query_includes_only_due_or_stale_work(self):
        from apps.lens_bridge.tasks.knowledge_source_sync import (
            due_knowledge_source_sync_ids,
        )

        now = timezone.now()
        self.knowledge_source.sync_next_poll_at = now - timedelta(seconds=1)
        self.knowledge_source.save(update_fields=["sync_next_poll_at", "updated_at"])
        future = LensKnowledgeSource.objects.create(
            organization=self.organization,
            name="Future source",
            gateway=self.knowledge_source.gateway,
            gateway_link=self.knowledge_source.gateway_link,
            source_path="/future",
            status=LensKnowledgeSource.Status.SYNCING,
            sync_next_poll_at=now + timedelta(minutes=5),
        )
        live_claim = LensKnowledgeSource.objects.create(
            organization=self.organization,
            name="Live source",
            gateway=self.knowledge_source.gateway,
            gateway_link=self.knowledge_source.gateway_link,
            source_path="/live",
            status=LensKnowledgeSource.Status.SYNCING,
            sync_claim_token="5f58b37b-c065-416d-8cd0-c75a989e436c",
            sync_claimed_at=now,
        )
        stale_claim = LensKnowledgeSource.objects.create(
            organization=self.organization,
            name="Stale source",
            gateway=self.knowledge_source.gateway,
            gateway_link=self.knowledge_source.gateway_link,
            source_path="/stale",
            status=LensKnowledgeSource.Status.SYNCING,
            sync_claim_token="606610e7-9ec5-4405-bba6-06ee74b3864b",
            sync_claimed_at=(
                now
                - timedelta(seconds=(knowledge_source_sync.SYNC_CLAIM_TTL_SECONDS + 1))
            ),
        )

        rows = due_knowledge_source_sync_ids(limit=20, now=now)
        due_ids = {knowledge_source_id for _, knowledge_source_id in rows}

        self.assertIn(self.knowledge_source.id, due_ids)
        self.assertIn(stale_claim.id, due_ids)
        self.assertNotIn(future.id, due_ids)
        self.assertNotIn(live_claim.id, due_ids)

    @patch(
        "apps.lens_bridge.tasks.knowledge_source_sync."
        "execute_knowledge_source_sync_task.delay"
    )
    def test_reconciler_dispatches_due_work(self, delay):
        from apps.lens_bridge.tasks.knowledge_source_sync import (
            reconcile_knowledge_source_syncs_task,
        )

        self.knowledge_source.sync_next_poll_at = timezone.now() - timedelta(seconds=1)
        self.knowledge_source.save(update_fields=["sync_next_poll_at", "updated_at"])

        result = reconcile_knowledge_source_syncs_task(limit=10)

        self.assertEqual(result["queued"], 1)
        self.assertEqual(
            result["knowledge_source_ids"],
            [self.knowledge_source.id],
        )
        delay.assert_called_once_with(
            organization_id=self.organization.id,
            knowledge_source_id=self.knowledge_source.id,
            mode="resume",
        )


class MapScopeToWorkspaceTests(SimpleTestCase):
    def test_maps_relative_paths_under_common_prefix(self):
        workspace = "/workspace/org-1/ks-42"
        scopes = ["/data/docs", "/data/images"]
        self.assertEqual(
            map_scope_to_workspace(
                workspace_root=workspace,
                scope_paths=scopes,
                scope_path="/data/docs",
            ),
            "/workspace/org-1/ks-42/docs",
        )
        self.assertEqual(
            map_scope_to_workspace(
                workspace_root=workspace,
                scope_paths=scopes,
                scope_path="/data/images",
            ),
            "/workspace/org-1/ks-42/images",
        )

    def test_single_scope_uses_basename_when_equal_to_common(self):
        workspace = "/workspace/org-1/ks-7"
        self.assertEqual(
            map_scope_to_workspace(
                workspace_root=workspace,
                scope_paths=["/backup/root"],
                scope_path="/backup/root",
            ),
            "/workspace/org-1/ks-7/root",
        )

    def test_windows_scope_maps_relative_subpath(self):
        workspace = "/workspace/org-1/ks-7"
        scope = r"D:\AndroidStudioProjects\VidLingo\app\src\main"
        self.assertEqual(
            map_scope_to_workspace(
                workspace_root=workspace,
                scope_paths=[scope],
                scope_path=scope,
            ),
            "/workspace/org-1/ks-7/main",
        )

    def test_restore_selected_paths_relative_to_directory(self):
        self.assertEqual(
            _restore_selected_paths(
                directory_source_path=r"D:\AndroidStudioProjects",
                scope_path=r"D:\AndroidStudioProjects\VidLingo\app\src\main",
            ),
            ["VidLingo/app/src/main"],
        )
        self.assertEqual(
            _restore_selected_paths(
                directory_source_path="/data",
                scope_path="/data/docs",
            ),
            ["docs"],
        )
        self.assertEqual(
            _restore_selected_paths(
                directory_source_path="/data",
                scope_path="/data",
            ),
            [],
        )


class RestoreSnapshotPollingTests(SimpleTestCase):
    def test_failed_restore_ends_current_sync_without_automatic_recreation(self):
        org = MagicMock(id=11)
        knowledge_source = MagicMock()
        sync_state = {
            "restore_record_id": 31,
            "restore_generation": 2,
            "snapshot_id_used": 17,
        }
        record = MagicMock(task_uuid="restore-task")
        task = MagicMock(error_message="Agent restore failed.")

        with (
            patch.object(knowledge_source_sync, "_update_sync_phase"),
            patch.object(
                knowledge_source_sync,
                "_restore_record_failed",
                return_value=True,
            ),
            patch.object(
                knowledge_source_sync.RestoreRecord.objects,
                "filter",
            ) as filter_records,
            patch.object(
                knowledge_source_sync.Task.objects,
                "filter",
            ) as filter_tasks,
            patch.object(
                knowledge_source_sync.restore_services,
                "create_lens_workspace_restore_record",
            ) as create_restore,
        ):
            filter_records.return_value.first.return_value = record
            filter_tasks.return_value.first.return_value = task
            with self.assertRaisesRegex(
                knowledge_source_sync.KnowledgeSourceSyncError,
                "Agent restore failed",
            ):
                knowledge_source_sync._run_phase_restore_snapshot(
                    org=org,
                    ks=knowledge_source,
                    sync_state=sync_state,
                )

        create_restore.assert_not_called()
        self.assertEqual(sync_state["restore_generation"], 2)

    def test_active_restore_keeps_the_snapshot_selected_for_its_sync_cycle(self):
        org = MagicMock(id=11)
        knowledge_source = MagicMock()
        sync_state = {
            "restore_record_id": 31,
            "restore_generation": 2,
            "snapshot_id_used": 17,
        }

        with (
            patch.object(knowledge_source_sync, "_update_sync_phase"),
            patch.object(
                knowledge_source_sync,
                "_restore_record_failed",
                return_value=False,
            ),
            patch.object(
                knowledge_source_sync,
                "resolve_snapshot_id_for_sync",
            ) as resolve_snapshot,
            patch.object(
                knowledge_source_sync.BackupSourceSnapshot.objects,
                "filter",
            ) as filter_snapshots,
        ):
            filter_snapshots.return_value.first.return_value = None
            with self.assertRaisesRegex(
                knowledge_source_sync.KnowledgeSourceSyncError,
                "No restorable snapshot",
            ):
                knowledge_source_sync._run_phase_restore_snapshot(
                    org=org,
                    ks=knowledge_source,
                    sync_state=sync_state,
                )

        resolve_snapshot.assert_not_called()
        filter_snapshots.assert_called_once_with(
            organization_id=org.id,
            pk=17,
            status__in=knowledge_source_sync.restore_services.RESTORABLE_SNAPSHOT_STATUSES,
        )


class PushAssistantPhaseTests(SimpleTestCase):
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync."
        "provisioning.sync_linked_assistant_for_ks"
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync."
        "provisioning.wait_for_lensnode_ready"
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.context_for_knowledge_source"
    )
    @patch("apps.lens_bridge.services.knowledge_source_sync._update_sync_phase")
    def test_waits_for_authoritative_gateway_workspace_root(
        self,
        _update_phase,
        context_for_source,
        wait_for_ready,
        sync_assistant,
    ):
        organization = MagicMock()
        knowledge_source = MagicMock(
            backup_source_snapshot_id=1,
            backup_snapshot_directory_id=1,
            workspace_path_on_lensnode="/workspace/org-34/data/hfl-ks-ready",
        )
        gateway_link = MagicMock()
        gateway_link.sl_lensnode_uuid = "de240f46-eccd-4e4b-868f-b1f504fbe67b"
        gateway_link.resolved_workspace_root.return_value = "/workspace/org-34/data"
        context_for_source.return_value = MagicMock(gateway_link=gateway_link)

        _run_phase_push_assistant(
            org=organization,
            ks=knowledge_source,
            sync_state={},
        )

        wait_for_ready.assert_called_once_with(
            lensnode_uuid=gateway_link.sl_lensnode_uuid,
            workspace_root="/workspace/org-34/data",
            selected_dir="/workspace/org-34/data/hfl-ks-ready",
        )
        sync_assistant.assert_called_once_with(
            org=organization,
            ks=knowledge_source,
            gateway_link=gateway_link,
        )


class ManagedRestorePipelineOrderTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="managed-restore-order",
            name="Managed Restore Order",
        )
        gateway_org = Organization.objects.create(
            key="managed-restore-gateway",
            name="Managed Restore Gateway",
        )
        gateway = Node.objects.create(
            organization=gateway_org,
            name="gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        gateway_link = LensGatewayLink.objects.create(
            organization=gateway_org,
            gateway=gateway,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            sidecar_status=LensGatewayLink.SidecarStatus.ONLINE,
        )
        self.gateway = gateway
        self.gateway_link = gateway_link
        self.knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.organization,
            name="Restored documents",
            gateway=gateway,
            gateway_link=gateway_link,
            backup_source_snapshot_id=11,
            backup_snapshot_directory_id=12,
            source_path="/source/documents",
            ingest_policy_json={"document": True},
            scan_enabled=True,
        )
        self.workspace_binding = LensWorkspaceBinding.objects.create(
            organization=self.organization,
            knowledge_source=self.knowledge_source,
            gateway_link=self.gateway_link,
            execution_organization_id=gateway_org.id,
            execution_node_id=self.gateway.id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
            workspace_root="/workspace/platform/data",
            relative_path="tenants/1/knowledge-sources/1",
            state=LensWorkspaceBinding.State.READY,
            identity_status=LensWorkspaceBinding.IdentityStatus.READY,
        )

    def test_restore_identity_is_durable_before_agent_delivery_callback(self):
        record = MagicMock(id=91)
        observed_record_ids = []

        def create_restore(**_kwargs):
            transaction.on_commit(
                lambda: observed_record_ids.append(
                    LensKnowledgeSource.all_objects.values_list(
                        "last_restore_record_id",
                        flat=True,
                    ).get(pk=self.knowledge_source.id)
                )
            )
            return record

        with (
            patch.object(
                knowledge_source_sync.restore_services,
                "create_lens_workspace_restore_record",
                side_effect=create_restore,
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            published = knowledge_source_sync._create_and_publish_workspace_restore(
                org=self.organization,
                ks=self.knowledge_source,
                restore_data={"items": []},
                sync_state={},
                snapshot_id=17,
                restore_scope_status={"0": "pending"},
            )

        self.assertEqual(published.id, record.id)
        self.assertEqual(observed_record_ids, [record.id])
        self.knowledge_source.refresh_from_db()
        self.assertEqual(self.knowledge_source.last_restore_record_id, record.id)

    def test_deleting_knowledge_source_cannot_publish_restore(self):
        self.knowledge_source.lifecycle_status = (
            LensKnowledgeSource.LifecycleStatus.DELETING
        )
        self.knowledge_source.save(update_fields=["lifecycle_status", "updated_at"])

        with (
            patch.object(
                knowledge_source_sync.restore_services,
                "create_lens_workspace_restore_record",
            ) as create_restore,
            self.assertRaisesRegex(
                knowledge_source_sync.KnowledgeSourceSyncError,
                "deletion was requested",
            ),
        ):
            knowledge_source_sync._create_and_publish_workspace_restore(
                org=self.organization,
                ks=self.knowledge_source,
                restore_data={"items": []},
                sync_state={},
                snapshot_id=17,
                restore_scope_status={"0": "pending"},
            )

        create_restore.assert_not_called()

    def test_conversion_finishes_before_assistant_push(self):
        phase_calls = []

        def record(name):
            return lambda **_kwargs: phase_calls.append(name)

        with (
            patch.object(
                knowledge_source_sync,
                "should_run_restore_phase",
                return_value=True,
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_prepare_workspace",
                side_effect=record("prepare_workspace"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_restore_snapshot",
                side_effect=record("restore_snapshot"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_ensure_managed_datasource",
                side_effect=record("ensure_managed_datasource"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_convert_documents",
                side_effect=record("convert_documents"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_push_assistant",
                side_effect=record("push_assistant"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_finalize",
                side_effect=record("finalize"),
            ),
        ):
            knowledge_source_sync._run_sync_pipeline(
                org=self.organization,
                ks=self.knowledge_source,
            )

        self.assertEqual(
            phase_calls,
            list(knowledge_source_sync.SYNC_PHASES),
        )

    def test_explicitly_disabled_conversion_skips_datasource_phases(self):
        self.knowledge_source.ingest_policy_json = {
            "document": False,
            "image": False,
            "embedded_image": False,
        }
        self.knowledge_source.save(update_fields=["ingest_policy_json", "updated_at"])
        phase_calls = []

        def record(name):
            return lambda **_kwargs: phase_calls.append(name)

        with (
            patch.object(
                knowledge_source_sync,
                "should_run_restore_phase",
                return_value=True,
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_prepare_workspace",
                side_effect=record("prepare_workspace"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_restore_snapshot",
                side_effect=record("restore_snapshot"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_ensure_managed_datasource",
            ) as ensure_datasource,
            patch.object(
                knowledge_source_sync,
                "_run_phase_convert_documents",
            ) as convert_documents,
            patch.object(
                knowledge_source_sync,
                "_run_phase_push_assistant",
                side_effect=record("push_assistant"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_finalize",
                side_effect=record("finalize"),
            ),
        ):
            knowledge_source_sync._run_sync_pipeline(
                org=self.organization,
                ks=self.knowledge_source,
            )

        ensure_datasource.assert_not_called()
        convert_documents.assert_not_called()
        self.assertEqual(
            phase_calls,
            [
                "prepare_workspace",
                "restore_snapshot",
                "push_assistant",
                "finalize",
            ],
        )

    def test_new_snapshot_invalidates_downstream_conversion_journal(self):
        self.knowledge_source.sync_state_json = {
            "completed_phases": list(knowledge_source_sync.SYNC_PHASES),
            "conversion": {
                "task_id": "old-conversion",
                "status": "SUCCESS",
            },
        }
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])
        phase_calls = []

        def record(name):
            return lambda **_kwargs: phase_calls.append(name)

        def restore_phase(**kwargs):
            self.assertNotIn("conversion", kwargs["sync_state"])
            phase_calls.append("restore_snapshot")

        with (
            patch.object(
                knowledge_source_sync,
                "should_run_restore_phase",
                return_value=True,
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_prepare_workspace",
            ) as prepare_workspace,
            patch.object(
                knowledge_source_sync,
                "_run_phase_restore_snapshot",
                side_effect=restore_phase,
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_ensure_managed_datasource",
                side_effect=record("ensure_managed_datasource"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_convert_documents",
                side_effect=record("convert_documents"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_push_assistant",
                side_effect=record("push_assistant"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_finalize",
                side_effect=record("finalize"),
            ),
        ):
            knowledge_source_sync._run_sync_pipeline(
                org=self.organization,
                ks=self.knowledge_source,
            )

        prepare_workspace.assert_not_called()
        self.assertEqual(
            phase_calls,
            [
                "restore_snapshot",
                "ensure_managed_datasource",
                "convert_documents",
                "push_assistant",
                "finalize",
            ],
        )

    def test_new_snapshot_waits_for_current_conversion_to_finish(self):
        self.knowledge_source.sync_state_json = {
            "completed_phases": [
                "prepare_workspace",
                "restore_snapshot",
                "ensure_managed_datasource",
            ],
            "conversion": {
                "task_id": "active-conversion",
                "status": "STARTED",
                "policy_fingerprint": "current-policy",
            },
        }
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])

        with (
            patch.object(
                knowledge_source_sync,
                "should_run_restore_phase",
                return_value=True,
            ) as should_restore,
            patch.object(
                knowledge_source_sync,
                "_run_phase_restore_snapshot",
            ) as restore_snapshot,
            patch.object(
                knowledge_source_sync,
                "_run_phase_convert_documents",
                side_effect=ManagedDatasourcePending("still converting"),
            ),
        ):
            with self.assertRaises(ManagedDatasourcePending):
                knowledge_source_sync._run_sync_pipeline(
                    org=self.organization,
                    ks=self.knowledge_source,
                )

        should_restore.assert_not_called()
        restore_snapshot.assert_not_called()

    def test_changed_policy_invalidates_completed_conversion_phase(self):
        self.knowledge_source.sync_state_json = {
            "completed_phases": [
                "prepare_workspace",
                "restore_snapshot",
                "ensure_managed_datasource",
                "convert_documents",
            ],
            "conversion": {
                "task_id": "old-conversion",
                "status": "SUCCESS",
                "policy_fingerprint": "stale-policy",
            },
        }
        self.knowledge_source.save(update_fields=["sync_state_json", "updated_at"])
        phase_calls = []

        def record(name):
            return lambda **_kwargs: phase_calls.append(name)

        with (
            patch.object(
                knowledge_source_sync,
                "should_run_restore_phase",
                return_value=False,
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_prepare_workspace",
            ) as prepare_workspace,
            patch.object(
                knowledge_source_sync,
                "_run_phase_restore_snapshot",
            ) as restore_snapshot,
            patch.object(
                knowledge_source_sync,
                "_run_phase_ensure_managed_datasource",
            ) as ensure_datasource,
            patch.object(
                knowledge_source_sync,
                "_run_phase_convert_documents",
                side_effect=record("convert_documents"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_push_assistant",
                side_effect=record("push_assistant"),
            ),
            patch.object(
                knowledge_source_sync,
                "_run_phase_finalize",
                side_effect=record("finalize"),
            ),
        ):
            knowledge_source_sync._run_sync_pipeline(
                org=self.organization,
                ks=self.knowledge_source,
            )

        prepare_workspace.assert_not_called()
        restore_snapshot.assert_not_called()
        ensure_datasource.assert_not_called()
        self.assertEqual(
            phase_calls,
            ["convert_documents", "push_assistant", "finalize"],
        )

    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.enqueue_knowledge_source_sync"
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync."
        "gateway_readiness.require_hfl_usable_gateway"
    )
    @patch(
        "apps.node.services.internal.node_lifecycle._active_lifecycle_task",
        return_value=None,
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.get_node_workload_blockers",
        return_value=[],
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.context_for_knowledge_source"
    )
    def test_manual_sync_after_completion_starts_new_generation(
        self,
        execution_context,
        _blockers,
        _active_lifecycle,
        _require_gateway,
        enqueue_sync,
    ):
        execution_context.return_value = MagicMock(
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            execution_organization=self.gateway.organization,
        )
        self.knowledge_source.status = LensKnowledgeSource.Status.READY
        self.knowledge_source.sync_state_json = {
            "completed_phases": list(knowledge_source_sync.SYNC_PHASES),
            "restore_generation": 4,
            "snapshot_id_used": 11,
            "conversion": {"task_id": "old-conversion"},
        }
        self.knowledge_source.save(
            update_fields=["status", "sync_state_json", "updated_at"]
        )

        updated = knowledge_source_sync.request_knowledge_source_sync(
            org=self.organization,
            ks=self.knowledge_source,
            mode="resume",
        )

        self.assertEqual(updated.sync_state_json["mode"], "full")
        self.assertEqual(
            updated.sync_state_json["restore_generation"],
            5,
        )
        self.assertEqual(updated.sync_state_json["completed_phases"], [])
        self.assertNotIn("conversion", updated.sync_state_json)
        enqueue_sync.assert_called_once_with(
            organization_id=self.organization.id,
            knowledge_source_id=self.knowledge_source.id,
            mode="full",
        )

    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.enqueue_knowledge_source_sync"
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync._restore_record_failed",
        return_value=True,
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync."
        "gateway_readiness.require_hfl_usable_gateway"
    )
    @patch(
        "apps.node.services.internal.node_lifecycle._active_lifecycle_task",
        return_value=None,
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.get_node_workload_blockers",
        return_value=[],
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.context_for_knowledge_source"
    )
    def test_manual_retry_starts_one_new_restore_generation(
        self,
        execution_context,
        _blockers,
        _active_lifecycle,
        _require_gateway,
        _restore_failed,
        enqueue_sync,
    ):
        execution_context.return_value = MagicMock(
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            execution_organization=self.gateway.organization,
        )
        self.knowledge_source.status = LensKnowledgeSource.Status.ERROR
        self.knowledge_source.sync_state_json = {
            "completed_phases": ["prepare_workspace"],
            "restore_record_id": 31,
            "restore_generation": 4,
            "snapshot_id_used": 11,
            "restore_scope_status": {"0": "pending"},
            "last_error": "Agent restore failed.",
        }
        self.knowledge_source.save(
            update_fields=["status", "sync_state_json", "updated_at"]
        )

        updated = knowledge_source_sync.request_knowledge_source_sync(
            org=self.organization,
            ks=self.knowledge_source,
            mode="resume",
        )

        self.assertEqual(updated.sync_state_json["restore_generation"], 5)
        self.assertEqual(updated.sync_state_json["restore_scope_status"], {})
        self.assertNotIn("restore_record_id", updated.sync_state_json)
        self.assertNotIn("snapshot_id_used", updated.sync_state_json)
        self.assertEqual(updated.sync_state_json["phase"], "restore_snapshot")
        enqueue_sync.assert_called_once_with(
            organization_id=self.organization.id,
            knowledge_source_id=self.knowledge_source.id,
            mode="resume",
        )

    @patch(
        "apps.lens_bridge.services.knowledge_source_sync."
        "managed_datasource.conversion_stop_confirmed",
        return_value=False,
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync."
        "gateway_readiness.require_hfl_usable_gateway"
    )
    @patch(
        "apps.node.services.internal.node_lifecycle._active_lifecycle_task",
        return_value=None,
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.get_node_workload_blockers",
        return_value=[],
    )
    @patch(
        "apps.lens_bridge.services.knowledge_source_sync.context_for_knowledge_source"
    )
    def test_manual_retry_waits_for_final_conversion_stop_acknowledgement(
        self,
        execution_context,
        _blockers,
        _active_lifecycle,
        _require_gateway,
        _stop_confirmed,
    ):
        execution_context.return_value = MagicMock(
            gateway=self.gateway,
            gateway_link=self.gateway_link,
            execution_organization=self.gateway.organization,
        )
        self.knowledge_source.status = LensKnowledgeSource.Status.ERROR
        self.knowledge_source.sync_state_json = {
            "completed_phases": ["prepare_workspace", "restore_snapshot"],
            "conversion": {
                "task_id": "cancelled-conversion",
                "status": "REVOKED",
            },
        }
        self.knowledge_source.save(
            update_fields=["status", "sync_state_json", "updated_at"]
        )

        with self.assertRaisesRegex(
            ValidationError,
            "previous document conversion is still stopping",
        ):
            knowledge_source_sync.request_knowledge_source_sync(
                org=self.organization,
                ks=self.knowledge_source,
            )
