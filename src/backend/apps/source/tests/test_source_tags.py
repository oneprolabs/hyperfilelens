from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.source.constants import PipelineStep, ResourceType
from apps.source.models import SourceResource, SourceTagAssignment
from apps.source.services.internal.source_pipeline import ensure_pipeline_entry
from apps.source.services.internal.backup_selectable import list_backup_selectable_sources


@override_settings(SECURE_SSL_REDIRECT=False)
class SourceTagsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="tags-org", name="Tags")
        self.other = Organization.objects.create(key="tags-other", name="Other")
        user = get_user_model().objects.create_user(username="tags@test.local", password="password")
        Membership.objects.create(user=user, organization=self.org, role=Membership.Role.ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.headers = {"HTTP_X_ORG_KEY": self.org.key}
        self.agent = Node.objects.create(organization=self.org, name="host", role=Node.Role.AGENT)
        self.nas = SourceResource.objects.create(organization=self.org, name="nas", resource_type=ResourceType.NAS)
        self.foreign = Node.objects.create(organization=self.other, name="foreign", role=Node.Role.AGENT)

    def test_assignments_are_organization_scoped_and_delete_only_removes_tag(self):
        base = "/api/v1/source/tags/"
        created = self.client.post(base, {"name": "Production"}, format="json", **self.headers)
        self.assertEqual(created.status_code, 201, created.content[:500])
        tag_id = created.data["id"]
        resource = f"{base}sources/agent/{self.agent.id}/"
        response = self.client.put(resource, {"tag_ids": [tag_id]}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(resource, **self.headers).data["results"][0]["name"], "Production")
        self.assertEqual(self.client.get(base, **self.headers).data["results"][0]["source_count"], 1)
        with override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE="legacy"):
            rows, count = list_backup_selectable_sources(
                organization_id=self.org.id, pipeline_step=1, tag_id=tag_id,
            )
            self.assertEqual(count, 1)
            self.assertEqual(rows[0]["id"], f"agent:{self.agent.id}")
        foreign = self.client.put(
            f"{base}sources/agent/{self.foreign.id}/", {"tag_ids": [tag_id]}, format="json", **self.headers,
        )
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(self.client.put(
            f"{base}sources/nas/{self.nas.id}/", {"tag_ids": [tag_id, 999999]}, format="json", **self.headers,
        ).status_code, 400)
        self.assertEqual(self.client.delete(f"{base}{tag_id}/", **self.headers).status_code, 204)
        self.assertTrue(Node.objects.filter(pk=self.agent.pk).exists())
        self.assertEqual(self.client.get(resource, **self.headers).data["results"], [])

    @override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE="pipeline")
    def test_tag_filter_is_applied_before_pagination(self):
        ensure_pipeline_entry(organization_id=self.org.id, source_kind="agent", ref_id=self.agent.id, step=PipelineStep.READY)
        ensure_pipeline_entry(organization_id=self.org.id, source_kind="nas", ref_id=self.nas.id, step=PipelineStep.READY)
        base = "/api/v1/source/tags/"
        created = self.client.post(base, {"name": "Production"}, format="json", **self.headers)
        self.assertEqual(created.status_code, 201, created.content[:500])
        tag_id = created.data["id"]
        self.client.put(f"{base}sources/agent/{self.agent.id}/", {"tag_ids": [tag_id]}, format="json", **self.headers)
        result = self.client.get(
            f"/api/v1/source/backup-selectable/?step=3&tag_id={tag_id}&page_size=1", **self.headers,
        )
        self.assertEqual(result.status_code, 200)
        payload = result.data
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["id"], f"agent:{self.agent.id}")
        self.assertEqual(payload["results"][0]["tags"][0]["id"], tag_id)
        for mode in ("legacy", "shadow"):
            with self.subTest(mode=mode), override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE=mode):
                rows, count = list_backup_selectable_sources(
                    organization_id=self.org.id, page_size=1, pipeline_step=3, tag_id=tag_id,
                )
                self.assertEqual(count, 1)
                self.assertEqual(rows[0]["id"], f"agent:{self.agent.id}")
        rows, count = list_backup_selectable_sources(
            organization_id=self.org.id, page_size=1, pipeline_step=3,
            tag_id=tag_id, allowed_source_ids={"agent": [], "nas": []},
        )
        self.assertEqual((rows, count), ([], 0))

    def test_multiple_tag_filter_matches_any_selected_tag_and_can_include_untagged(self):
        untagged_agent = Node.objects.create(
            organization=self.org, name="untagged", role=Node.Role.AGENT,
        )
        for source in (self.agent, self.nas, untagged_agent):
            ensure_pipeline_entry(
                organization_id=self.org.id, source_kind="agent" if isinstance(source, Node) else "nas",
                ref_id=source.id, step=PipelineStep.READY,
            )
        base = "/api/v1/source/tags/"
        tag_ids = [
            self.client.post(base, {"name": name}, format="json", **self.headers).data["id"]
            for name in ("Production", "Critical")
        ]
        self.client.put(
            f"{base}sources/agent/{self.agent.id}/", {"tag_ids": tag_ids},
            format="json", **self.headers,
        )
        self.client.put(
            f"{base}sources/nas/{self.nas.id}/", {"tag_ids": [tag_ids[0]]},
            format="json", **self.headers,
        )
        tag_options = self.client.get(base, **self.headers)
        self.assertEqual(tag_options.status_code, 200)
        self.assertEqual(tag_options.data["untagged_source_count"], 1)
        self.assertEqual(
            {tag["name"]: tag["source_count"] for tag in tag_options.data["results"]},
            {"Production": 2, "Critical": 1},
        )

        tagged = self.client.get(
            f"/api/v1/source/backup-selectable/?step=3&tag_ids={tag_ids[0]},{tag_ids[1]}",
            **self.headers,
        )
        self.assertEqual(tagged.status_code, 200, tagged.content[:500])
        self.assertEqual(
            {row["id"] for row in tagged.data["results"]},
            {f"agent:{self.agent.id}", f"nas:{self.nas.id}"},
        )

        untagged = self.client.get(
            "/api/v1/source/backup-selectable/?step=3&untagged=true",
            **self.headers,
        )
        self.assertEqual(untagged.status_code, 200, untagged.content[:500])
        self.assertEqual([row["id"] for row in untagged.data["results"]], [f"agent:{untagged_agent.id}"])

        for mode in ("legacy", "pipeline"):
            with self.subTest(mode=mode), override_settings(SOURCE_BACKUP_SELECTABLE_QUERY_MODE=mode):
                rows, count = list_backup_selectable_sources(
                    organization_id=self.org.id, page_size=10, pipeline_step=3,
                    tag_ids=tag_ids,
                )
                self.assertEqual(count, 2)
                self.assertEqual(
                    {row["id"] for row in rows},
                    {f"agent:{self.agent.id}", f"nas:{self.nas.id}"},
                )
                untagged_rows, untagged_count = list_backup_selectable_sources(
                    organization_id=self.org.id, page_size=10, pipeline_step=3,
                    untagged=True,
                )
                self.assertEqual(untagged_count, 1)
                self.assertEqual(untagged_rows[0]["id"], f"agent:{untagged_agent.id}")

        mixed = self.client.get(
            f"/api/v1/source/backup-selectable/?step=3&untagged=true&tag_ids={tag_ids[0]}",
            **self.headers,
        )
        self.assertEqual(mixed.status_code, 200, mixed.content[:500])
        self.assertEqual(
            {row["id"] for row in mixed.data["results"]},
            {f"agent:{self.agent.id}", f"nas:{self.nas.id}", f"agent:{untagged_agent.id}"},
        )
        duplicates = self.client.get(
            f"/api/v1/source/backup-selectable/?step=3&tag_ids={tag_ids[0]},{tag_ids[0]}",
            **self.headers,
        )
        self.assertEqual(duplicates.status_code, 400)

    def test_source_deletion_clears_assignments(self):
        from apps.source.models import SourceTagAssignment

        tag_id = self.client.post(
            "/api/v1/source/tags/", {"name": "Production"}, format="json", **self.headers,
        ).data["id"]
        for kind, source in (("agent", self.agent), ("nas", self.nas)):
            self.client.put(
                f"/api/v1/source/tags/sources/{kind}/{source.id}/",
                {"tag_ids": [tag_id]}, format="json", **self.headers,
            )
        self.agent.soft_delete()
        self.nas.soft_delete()
        self.assertFalse(SourceTagAssignment.objects.filter(organization_id=self.org.id).exists())
        result = self.client.get("/api/v1/source/tags/", **self.headers)
        self.assertEqual(result.data["results"][0]["source_count"], 0)
        self.assertEqual(result.data["untagged_source_count"], 0)

    def test_counts_ignore_deleted_and_orphaned_sources_even_when_assignments_remain(self):
        base = "/api/v1/source/tags/"
        tag_id = self.client.post(
            base, {"name": "Production"}, format="json", **self.headers,
        ).data["id"]
        for kind, source in (("agent", self.agent), ("nas", self.nas)):
            self.client.put(
                f"{base}sources/{kind}/{source.id}/",
                {"tag_ids": [tag_id]}, format="json", **self.headers,
            )
        self.assertEqual(self.client.get(base, **self.headers).data["results"][0]["source_count"], 2)
        # Simulate legacy/bulk deletion that bypasses the cleanup signals.
        Node.objects.filter(pk=self.agent.id).update(is_deleted=True)
        SourceResource.objects.filter(pk=self.nas.id).update(is_deleted=True)
        proxy = Node.objects.create(organization=self.org, name="proxy", role=Node.Role.PROXY)
        live_untagged = Node.objects.create(
            organization=self.org, name="untagged", role=Node.Role.AGENT,
        )
        SourceTagAssignment.objects.bulk_create([
            SourceTagAssignment(
                organization=self.org, tag_id=tag_id, source_kind="agent", ref_id=ref_id,
            )
            for ref_id in (999999, proxy.id, self.foreign.id)
        ])
        self.assertEqual(SourceTagAssignment.objects.filter(tag_id=tag_id).count(), 5)
        for restricted in (False, True):
            with self.subTest(restricted=restricted):
                provider = SimpleNamespace(visible_resource_refs=True) if restricted else None
                with patch("apps.source.api.views.source_tag.get_authz_provider", return_value=provider), patch(
                    "apps.source.api.views.source_tag.visible_resource_refs",
                    side_effect=lambda request, refs: set(refs),
                ):
                    result = self.client.get(base, **self.headers)
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.data["results"][0]["source_count"], 0)
                    self.assertEqual(result.data["untagged_source_count"], 1)
                    updated = self.client.patch(
                        f"{base}{tag_id}/", {"color": "blue"},
                        format="json", **self.headers,
                    )
                    self.assertEqual(updated.status_code, 200)
                    self.assertEqual(updated.data["source_count"], 0)
        # Counts agree with the read-only usage list without deleting tag definitions.
        usage = self.client.get(f"{base}{tag_id}/sources/", **self.headers)
        self.assertEqual(usage.data["count"], 0)
        self.assertTrue(Node.objects.filter(pk=live_untagged.id).exists())

    def test_tag_filter_rejects_more_than_fifty_selected_tags(self):
        response = self.client.get(
            "/api/v1/source/backup-selectable/",
            {"tag_ids": ",".join(str(value) for value in range(1, 52))},
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Select at most 50", str(response.data))

    def test_tag_details_are_editable_and_shared_across_source_endpoints(self):
        base = "/api/v1/source/tags/"
        response = self.client.post(
            base,
            {"name": "Production", "description": "Critical hosts", "color": "blue"},
            format="json", **self.headers,
        )
        self.assertEqual(response.status_code, 201)
        tag_id = response.data["id"]
        self.assertEqual(response.data["description"], "Critical hosts")
        self.assertEqual(response.data["color"], "blue")
        resource = f"{base}sources/agent/{self.agent.id}/"
        self.assertEqual(self.client.put(
            resource, {"tag_ids": [tag_id]}, format="json", **self.headers,
        ).data["results"][0]["color"], "blue")

        updated = self.client.patch(
            f"{base}{tag_id}/",
            {"description": "Production services", "color": "purple"},
            format="json", **self.headers,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["name"], "Production")
        self.assertEqual(updated.data["description"], "Production services")
        self.assertEqual(self.client.get(base, **self.headers).data["results"][0]["color"], "purple")
        self.assertEqual(self.client.get(resource, **self.headers).data["results"][0]["color"], "purple")
        self.assertEqual(
            self.client.get(f"{base}assignments/?ids=agent:{self.agent.id}", **self.headers)
            .data["results"][f"agent:{self.agent.id}"][0]["description"],
            "Production services",
        )
        source = self.client.get(
            f"/api/v1/source/backup-selectable/?ids=agent:{self.agent.id}", **self.headers,
        )
        self.assertEqual(source.data["results"][0]["tags"][0]["color"], "purple")

    def test_tag_details_validate_inputs_and_old_clients_get_defaults(self):
        base = "/api/v1/source/tags/"
        for payload in (
            {"name": "Invalid", "color": "#abcdef"},
            {"name": "Invalid", "color": "red; background: black"},
            {"name": "Invalid", "description": "x" * 256},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    self.client.post(base, payload, format="json", **self.headers).status_code,
                    400,
                )
        created = self.client.post(base, {"name": "Old client"}, format="json", **self.headers)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["color"], "neutral")
        self.assertEqual(created.data["description"], "")
        tag_id = created.data["id"]
        self.assertEqual(self.client.post(
            base, {"name": "    "}, format="json", **self.headers,
        ).status_code, 400)
        self.assertEqual(self.client.post(
            base, {"name": "  OLD CLIENT  "}, format="json", **self.headers,
        ).status_code, 400)
        self.assertEqual(self.client.post(
            base, {"name": "OLD CLIENT"}, format="json", **self.headers,
        ).status_code, 400)
        self.assertEqual(self.client.patch(
            f"{base}{tag_id}/", {"name": "  Renamed  "}, format="json", **self.headers,
        ).status_code, 400)
        self.assertEqual(self.client.patch(
            f"{base}{tag_id}/", {"name": "Renamed"}, format="json", **self.headers,
        ).status_code, 200)
        self.assertEqual(self.client.patch(
            f"{base}{tag_id}/", {"color": "unknown"}, format="json", **self.headers,
        ).status_code, 400)
        self.assertEqual(self.client.get(base, **self.headers).data["results"][0]["name"], "Renamed")

    def test_bulk_tag_assignment_only_changes_selected_tags(self):
        base = "/api/v1/source/tags/"
        keep = self.client.post(base, {"name": "Keep"}, format="json", **self.headers).data["id"]
        change = self.client.post(base, {"name": "Change"}, format="json", **self.headers).data["id"]
        self.client.put(
            f"{base}sources/agent/{self.agent.id}/", {"tag_ids": [keep]},
            format="json", **self.headers,
        )
        payload = {
            "operation": "add",
            "source_ids": [f"agent:{self.agent.id}", f"nas:{self.nas.id}"],
            "tag_ids": [change],
        }
        endpoint = f"{base}assignments/bulk/"
        added = self.client.post(endpoint, payload, format="json", **self.headers)
        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.data["added"], 2)
        self.assertEqual(self.client.post(endpoint, payload, format="json", **self.headers).data["added"], 0)
        self.assertEqual(
            set(SourceTagAssignment.objects.filter(
                source_kind="agent", ref_id=self.agent.id,
            ).values_list("tag_id", flat=True)),
            {keep, change},
        )
        payload["operation"] = "remove"
        removed = self.client.post(endpoint, payload, format="json", **self.headers)
        self.assertEqual(removed.data["removed"], 2)
        self.assertEqual(self.client.post(endpoint, payload, format="json", **self.headers).data["removed"], 0)
        self.assertEqual(
            list(SourceTagAssignment.objects.filter(
                source_kind="agent", ref_id=self.agent.id,
            ).values_list("tag_id", flat=True)),
            [keep],
        )
        self.assertFalse(SourceTagAssignment.objects.filter(source_kind="nas", ref_id=self.nas.id).exists())

    def test_bulk_assignment_rejects_invalid_or_foreign_sources_atomically(self):
        tag_id = self.client.post(
            "/api/v1/source/tags/", {"name": "Safe"}, format="json", **self.headers,
        ).data["id"]
        endpoint = "/api/v1/source/tags/assignments/bulk/"
        for payload, expected in (
            ({"source_ids": [f"agent:{self.agent.id}", f"agent:{self.foreign.id}"], "tag_ids": [tag_id]}, 404),
            ({"source_ids": [f"agent:{self.agent.id}"], "tag_ids": [tag_id, 999999]}, 400),
            ({"source_ids": [f"agent:{self.agent.id}", f"agent:{self.agent.id}"], "tag_ids": [tag_id]}, 400),
            ({"source_ids": ["proxy:123"], "tag_ids": [tag_id]}, 400),
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    endpoint, {"operation": "add", **payload},
                    format="json", **self.headers,
                )
                self.assertEqual(response.status_code, expected)
                self.assertFalse(SourceTagAssignment.objects.filter(
                    organization_id=self.org.id,
                ).exists())
        with patch("apps.source.api.views.source_tag.visible_resource_refs", return_value=set()):
            denied = self.client.post(
                endpoint,
                {
                    "operation": "add",
                    "source_ids": [f"agent:{self.agent.id}"],
                    "tag_ids": [tag_id],
                }, format="json", **self.headers,
            )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(SourceTagAssignment.objects.filter(organization_id=self.org.id).exists())

    def test_tag_source_list_is_searchable_paginated_and_org_scoped(self):
        tag_id = self.client.post(
            "/api/v1/source/tags/", {"name": "Catalog"}, format="json", **self.headers,
        ).data["id"]
        endpoint = f"/api/v1/source/tags/{tag_id}/sources/"
        self.assertEqual(self.client.post(
            "/api/v1/source/tags/assignments/bulk/",
            {
                "operation": "add",
                "source_ids": [f"agent:{self.agent.id}", f"nas:{self.nas.id}"],
                "tag_ids": [tag_id],
            }, format="json", **self.headers,
        ).status_code, 200)
        page = self.client.get(f"{endpoint}?page_size=1", **self.headers)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data["count"], 2)
        self.assertEqual(len(page.data["results"]), 1)
        self.assertEqual(
            self.client.get(f"{endpoint}?page_size=1&page=2", **self.headers)
            .data["results"][0]["id"],
            f"nas:{self.nas.id}",
        )
        searched = self.client.get(f"{endpoint}?search=nas", **self.headers)
        self.assertEqual(searched.data["count"], 1)
        self.assertEqual(searched.data["results"][0]["id"], f"nas:{self.nas.id}")
        other_user = get_user_model().objects.create_user(username="tag-other@test.local")
        Membership.objects.create(user=other_user, organization=self.other, role=Membership.Role.ADMIN)
        self.client.force_authenticate(user=other_user)
        self.assertEqual(self.client.get(
            endpoint, HTTP_X_ORG_KEY=self.other.key,
        ).status_code, 404)
