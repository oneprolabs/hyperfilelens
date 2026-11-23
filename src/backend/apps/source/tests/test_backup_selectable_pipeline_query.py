from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext

from apps.iam.models import Organization
from apps.node.models import Node
from apps.protection.models import BackupConfig
from apps.source.constants import PipelineStep, ResourceType
from apps.source.models import SourceBackupPipelineEntry, SourceResource
from apps.source.services.internal.backup_selectable import list_backup_selectable_sources
from apps.source.services.internal.source_pipeline import ensure_pipeline_entry


@override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE="pipeline")
class BackupSelectablePipelineQueryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="selectable-query", name="Selectable Query")
        self.other_org = Organization.objects.create(key="selectable-other", name="Selectable Other")
        self.agent = Node.objects.create(
            organization=self.org,
            name="Alpha Host",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="198.51.100.10",
            metadata={"inventory": {"hostname": "alpha-executor"}},
        )
        self.proxy = Node.objects.create(
            organization=self.org,
            name="NAS Proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="203.0.113.20",
        )
        self.nas = SourceResource.objects.create(
            organization=self.org,
            name="Finance NAS",
            resource_type=ResourceType.NAS,
            bound_node=self.proxy,
            status="active",
            availability="online",
            config={"server": "storage.internal", "export_path": "/finance"},
        )
        for source_kind, ref_id in (("agent", self.agent.id), ("nas", self.nas.id)):
            entry = ensure_pipeline_entry(
                organization_id=self.org.id,
                source_kind=source_kind,
                ref_id=ref_id,
                step=PipelineStep.READY,
            )
            entry.step = PipelineStep.READY
            entry.save(update_fields=["step", "updated_at"])

    @override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE="auto")
    def test_auto_uses_sql_pagination_for_a_complete_projection(self):
        with patch("apps.source.services.internal.backup_selectable._build_catalog", side_effect=AssertionError):
            rows, count = list_backup_selectable_sources(organization_id=self.org.id, page=1, page_size=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(count, 2)

    def ids(self, **params):
        results, count = list_backup_selectable_sources(
            organization_id=self.org.id,
            page=1,
            page_size=20,
            **params,
        )
        return [row["id"] for row in results], count

    def test_ready_page_includes_configured_source_with_stale_step(self):
        SourceBackupPipelineEntry.objects.filter(
            organization=self.org, source_kind="agent", ref_id=self.agent.id,
        ).update(step=PipelineStep.SOURCE_POOL)
        BackupConfig.objects.create(
            organization_id=self.org.id, source_type="agent", source_ref_id=self.agent.id,
            name="Already configured", repository_id=1,
        )
        for mode in ("legacy", "pipeline", "auto"):
            with self.subTest(mode=mode), override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE=mode):
                rows, total = list_backup_selectable_sources(
                    organization_id=self.org.id, pipeline_step=PipelineStep.READY,
                    source_type="host", page=1, page_size=1,
                )
                self.assertEqual(total, 1)
                self.assertEqual([row["id"] for row in rows], [f"agent:{self.agent.id}"])
                self.assertEqual(rows[0]["pipeline_step"], PipelineStep.READY)

    def test_selected_search_field_uses_projected_proxy_identity_for_nas(self):
        ids, count = self.ids(search="NAS Proxy", search_field="source_hostname", pipeline_step=3)
        self.assertEqual(ids, [f"nas:{self.nas.id}"])
        self.assertEqual(count, 1)

        ids, count = self.ids(search="203.0.113", search_field="source_ip", pipeline_step=3)
        self.assertEqual(ids, [f"nas:{self.nas.id}"])
        self.assertEqual(count, 1)

        ids, count = self.ids(search="198.51.100.10", search_field="source_ip", pipeline_step=3)
        self.assertEqual(ids, [f"agent:{self.agent.id}"])
        self.assertEqual(count, 1)

    def test_materialized_rows_include_source_availability(self):
        self.nas.availability = "offline"
        self.nas.save(update_fields=["availability", "updated_at"])

        results, count = list_backup_selectable_sources(
            organization_id=self.org.id,
            page=1,
            page_size=20,
            pipeline_step=3,
        )

        rows = {row["id"]: row for row in results}
        self.assertEqual(count, 2)
        self.assertEqual(rows[f"agent:{self.agent.id}"]["availability"], "online")
        self.assertEqual(rows[f"nas:{self.nas.id}"]["availability"], "offline")

    def test_filters_are_combined_before_count_and_pagination(self):
        config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="alpha-config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=31,
            backup_policy_id=41,
            file_filter_rule_id=51,
        )
        SourceBackupPipelineEntry.objects.filter(
            organization_id=self.org.id,
            source_kind="agent",
            ref_id=self.agent.id,
        ).update(last_backup_status="running")

        ids, count = self.ids(
            pipeline_step=3,
            availability="online",
            running_task="backup",
            backup_policy_id=config.backup_policy_id,
            file_filter_rule_id=config.file_filter_rule_id,
            repository_id=config.repository_id,
        )

        self.assertEqual(ids, [f"agent:{self.agent.id}"])
        self.assertEqual(count, 1)

    def test_filters_backup_task_status_categories(self):
        entry = SourceBackupPipelineEntry.objects.get(source_kind="agent", ref_id=self.agent.id)
        SourceBackupPipelineEntry.objects.exclude(pk=entry.pk).update(last_backup_status="none")
        cases = {
            "success": "success",
            "failed": "failed",
            "running": "running",
            "none": "none",
        }
        for status_filter, stored_status in cases.items():
            entry.last_backup_status = stored_status
            entry.save(update_fields=["last_backup_status", "updated_at"])
            ids, count = self.ids(pipeline_step=3, backup_task_status=status_filter)
            expected = [f"nas:{self.nas.id}", f"agent:{self.agent.id}"] if status_filter == "none" else [f"agent:{self.agent.id}"]
            self.assertEqual(ids, expected)
            self.assertEqual(count, len(expected))

    def test_running_and_failed_task_statuses_include_normalized_terminal_states(self):
        entry = SourceBackupPipelineEntry.objects.get(source_kind="agent", ref_id=self.agent.id)
        for stored_status in ("queued", "running", "stopping"):
            entry.last_backup_status = stored_status
            entry.save(update_fields=["last_backup_status", "updated_at"])
            ids, count = self.ids(pipeline_step=3, backup_task_status="running")
            self.assertEqual(ids, [f"agent:{self.agent.id}"])
            self.assertEqual(count, 1)
        for stored_status in ("failed", "timeout", "cancelled"):
            entry.last_backup_status = stored_status
            entry.save(update_fields=["last_backup_status", "updated_at"])
            ids, count = self.ids(pipeline_step=3, backup_task_status="failed")
            self.assertEqual(ids, [f"agent:{self.agent.id}"])
            self.assertEqual(count, 1)

    def test_backup_and_restore_task_status_filters_use_and_semantics(self):
        agent_entry = SourceBackupPipelineEntry.objects.get(source_kind="agent", ref_id=self.agent.id)
        nas_entry = SourceBackupPipelineEntry.objects.get(source_kind="nas", ref_id=self.nas.id)
        agent_entry.last_backup_status = "success"
        agent_entry.last_restore_status = "failed"
        agent_entry.save(update_fields=["last_backup_status", "last_restore_status", "updated_at"])
        nas_entry.last_backup_status = "success"
        nas_entry.last_restore_status = "success"
        nas_entry.save(update_fields=["last_backup_status", "last_restore_status", "updated_at"])

        ids, count = self.ids(
            pipeline_step=3,
            backup_task_status="success",
            restore_task_status="failed",
        )
        self.assertEqual(ids, [f"agent:{self.agent.id}"])
        self.assertEqual(count, 1)

    def test_backup_config_exists_is_tenant_scoped(self):
        SourceBackupPipelineEntry.objects.filter(
            organization=self.org, source_kind="agent", ref_id=self.agent.id,
        ).update(step=PipelineStep.CONFIG)
        BackupConfig.objects.create(
            organization_id=self.other_org.id,
            name="foreign-config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=99,
        )

        ids, count = self.ids(pipeline_step=3, repository_id=99)

        self.assertEqual(ids, [])
        self.assertEqual(count, 0)

        ids, count = self.ids(pipeline_step=3)

        self.assertEqual(ids, [f"nas:{self.nas.id}"])
        self.assertEqual(count, 1)

    @override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE="legacy")
    def test_legacy_rollout_mode_honors_new_filters_and_keeps_step_three_fallback(self):
        entry = SourceBackupPipelineEntry.objects.get(
            organization_id=self.org.id,
            source_kind="agent",
            ref_id=self.agent.id,
        )
        entry.step = PipelineStep.CONFIG
        entry.save(update_fields=["step", "updated_at"])
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="legacy-fallback",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=88,
        )

        ids, count = self.ids(
            pipeline_step=3,
            search="Alpha",
            search_field="source_name",
            repository_id=88,
        )

        self.assertEqual(ids, [f"agent:{self.agent.id}"])
        self.assertEqual(count, 1)

    def test_page_order_is_stable_and_count_is_unfiltered_by_page(self):
        ids, count = self.ids(pipeline_step=3)
        first_page, first_count = list_backup_selectable_sources(
            organization_id=self.org.id,
            page=1,
            page_size=1,
            pipeline_step=3,
        )
        second_page, second_count = list_backup_selectable_sources(
            organization_id=self.org.id,
            page=2,
            page_size=1,
            pipeline_step=3,
        )

        self.assertEqual(first_count, count)
        self.assertEqual(second_count, count)
        self.assertEqual([first_page[0]["id"], second_page[0]["id"]], ids)

    def test_current_page_expansion_has_a_bounded_query_count(self):
        for index in range(12):
            agent = Node.objects.create(
                organization=self.org,
                name=f"Scale Agent {index}",
                role=Node.Role.AGENT,
            )
            entry = ensure_pipeline_entry(
                organization_id=self.org.id,
                source_kind="agent",
                ref_id=agent.id,
                step=PipelineStep.READY,
            )
            entry.step = PipelineStep.READY
            entry.save(update_fields=["step", "updated_at"])

        with CaptureQueriesContext(connection) as queries:
            results, count = list_backup_selectable_sources(
                organization_id=self.org.id,
                page=1,
                page_size=2,
                pipeline_step=3,
                expand="backup_configs,policies,runtime",
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(count, 14)
        self.assertLessEqual(len(queries), 20)


class BackupSelectableShadowQueryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="selectable-shadow", name="Selectable Shadow")
        self.agent = Node.objects.create(
            organization=self.org,
            name="Shadow Agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        entry = ensure_pipeline_entry(
            organization_id=self.org.id,
            source_kind="agent",
            ref_id=self.agent.id,
            step=PipelineStep.READY,
        )
        entry.step = PipelineStep.READY
        entry.save(update_fields=["step", "updated_at"])

    @override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE="shadow")
    def test_shadow_mode_serves_legacy_response(self):
        results, count = list_backup_selectable_sources(
            organization_id=self.org.id,
            page=1,
            page_size=20,
            pipeline_step=3,
            search="Shadow",
            search_field="source_name",
        )

        self.assertEqual([row["id"] for row in results], [f"agent:{self.agent.id}"])
        self.assertEqual(count, 1)


@tag("performance")
class BackupSelectablePipelineScaleTests(TestCase):
    def test_ten_thousand_source_benchmark_records_plan_latency_and_query_count(self):
        org = Organization.objects.create(key="selectable-scale", name="Selectable Scale")
        agents = Node.objects.bulk_create([
            Node(
                organization=org,
                name=f"Scale Agent {index:05d}",
                role=Node.Role.AGENT,
                status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            )
            for index in range(10_000)
        ])
        SourceBackupPipelineEntry.objects.bulk_create([
            SourceBackupPipelineEntry(
                organization=org,
                source_kind="agent",
                ref_id=agent.id,
                step=PipelineStep.READY,
                created_at=agent.created_at,
                source_name=agent.name,
                source_hostname=agent.name,
                source_status="online",
                source_availability="online",
            )
            for agent in agents
        ], batch_size=1_000)
        output = StringIO()

        call_command(
            "benchmark_backup_selectable_query",
            organization_id=org.id,
            iterations=2,
            page_size=100,
            minimum_sources=10_000,
            stdout=output,
        )

        report = output.getvalue()
        self.assertIn("sources=10000", report)
        query_count = next(field for field in report.split() if field.startswith("query_count_max="))
        self.assertLessEqual(int(query_count.split("=")[1]), 3)
        self.assertIn("latency_ms_avg=", report)
        self.assertIn("Execution Time:", report)
