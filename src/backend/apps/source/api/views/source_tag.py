"""Source tag management and per-source assignment."""

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Subquery
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator, IsOrgStaffReader
from apps.iam.resource_access import assert_resource_access, visible_resource_refs
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.source.constants import ResourceType
from apps.source.models import SourceResource, SourceTag, SourceTagAssignment
from apps.source.services.internal.selectable_ids import parse_selectable_id
from common.extension_spi import get_authz_provider


def _tag_payload(tag):
    return {
        "id": tag.id,
        "name": tag.name,
        "description": tag.description,
        "color": tag.color,
        "created_at": tag.created_at,
        "source_count": tag.source_count,
    }

def _tag_summary(tag):
    return {
        "id": tag.id,
        "name": tag.name,
        "description": tag.description,
        "color": tag.color,
    }


def _validate_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 64:
        raise ValidationError({"name": "Name must contain 1 to 64 characters."})
    if value != value.strip():
        raise ValidationError({"name": "Name must not start or end with whitespace."})
    return value

def _ensure_unique_name(organization_id, name, *, exclude_id=None):
    existing = SourceTag.objects.filter(
        organization_id=organization_id, name__iexact=name,
    )
    if exclude_id is not None:
        existing = existing.exclude(id=exclude_id)
    if existing.exists():
        raise ValidationError({"name": "Tag name already exists."})

def _validate_description(value):
    if not isinstance(value, str) or len(value.strip()) > 255:
        raise ValidationError({"description": "Description must be at most 255 characters."})
    return value.strip()


def _validate_color(value):
    if not isinstance(value, str) or value not in SourceTag.COLORS:
        raise ValidationError({"color": "Unknown tag color."})
    return value


def _live_tag_assignments(organization_id):
    """Do not count orphaned associations or soft-deleted source identities."""
    live_nodes = Node.objects.filter(
        organization_id=organization_id, role=NodeRole.AGENT, is_deleted=False,
    ).values("id")
    live_nas = SourceResource.objects.filter(
        organization_id=organization_id, resource_type=ResourceType.NAS, is_deleted=False,
    ).values("id")
    return SourceTagAssignment.objects.filter(
        organization_id=organization_id, tag__organization_id=organization_id,
    ).filter(
        Q(source_kind="agent", ref_id__in=Subquery(live_nodes))
        | Q(source_kind="nas", ref_id__in=Subquery(live_nas))
    )


def _tag_list(organization_id):
    return SourceTag.objects.filter(organization_id=organization_id).annotate(
        source_count=Count(
            "assignments",
            filter=Q(assignments__id__in=Subquery(
                _live_tag_assignments(organization_id).values("id")
            )),
        )
    ).order_by("name", "id")

def _visible_tag_count(request, tag):
    assignments = list(_live_tag_assignments(tag.organization_id).filter(
        tag_id=tag.id,
    ).values_list("source_kind", "ref_id"))
    provider = get_authz_provider()
    if provider is None or getattr(provider, "visible_resource_refs", None) is None:
        return len(assignments)
    refs = [
        ("node" if kind == "agent" else "source_resource", ref_id)
        for kind, ref_id in assignments if kind in {"agent", "nas"}
    ]
    allowed = visible_resource_refs(request, refs) or set()
    return sum(ref in allowed for ref in refs)


class SourceTagListView(APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrgOperator()]
        return super().get_permissions()

    def get(self, request):
        org = require_org(request)
        tags = list(_tag_list(org.id))
        provider = get_authz_provider()
        untagged_refs = [
            ("node", ref_id)
            for ref_id in Node.objects.filter(
                organization_id=org.id,
                role=NodeRole.AGENT,
                is_deleted=False,
            ).exclude(
                id__in=Subquery(SourceTagAssignment.objects.filter(
                    organization_id=org.id,
                    source_kind="agent",
                ).values("ref_id"))
            ).values_list("id", flat=True)
        ]
        untagged_refs.extend(
            ("source_resource", ref_id)
            for ref_id in SourceResource.objects.filter(
                organization_id=org.id,
                resource_type=ResourceType.NAS,
                is_deleted=False,
            ).exclude(
                id__in=Subquery(SourceTagAssignment.objects.filter(
                    organization_id=org.id,
                    source_kind="nas",
                ).values("ref_id"))
            ).values_list("id", flat=True)
        )
        untagged_visible_count = len(untagged_refs)
        if provider is not None and getattr(provider, "visible_resource_refs", None) is not None:
            visible_untagged = visible_resource_refs(request, untagged_refs) or set()
            untagged_visible_count = len(visible_untagged)
        if provider is not None and getattr(provider, "visible_resource_refs", None) is not None:
            assignments = list(_live_tag_assignments(org.id).values_list(
                "tag_id", "source_kind", "ref_id",
            ))
            refs = list({
                ("node" if kind == "agent" else "source_resource", ref_id)
                for _, kind, ref_id in assignments if kind in {"agent", "nas"}
            })
            visible = visible_resource_refs(request, refs) or set()
            counts = {}
            for tag_id, kind, ref_id in assignments:
                ref = ("node" if kind == "agent" else "source_resource", ref_id)
                if kind in {"agent", "nas"} and ref in visible:
                    counts[tag_id] = counts.get(tag_id, 0) + 1
            for tag in tags:
                tag.source_count = counts.get(tag.id, 0)
        return Response({
            "results": [_tag_payload(tag) for tag in tags],
            "untagged_source_count": untagged_visible_count,
        })

    def post(self, request):
        org = require_org(request)
        name = _validate_name(request.data.get("name"))
        _ensure_unique_name(org.id, name)
        description = _validate_description(request.data.get("description", ""))
        color = _validate_color(request.data.get("color", "neutral"))
        try:
            with transaction.atomic():
                tag = SourceTag.objects.create(
                    organization_id=org.id, name=name, description=description, color=color,
                )
        except IntegrityError as exc:
            raise ValidationError({"name": "Tag name already exists."}) from exc
        tag.source_count = 0
        return Response(_tag_payload(tag), status=status.HTTP_201_CREATED)


class SourceTagDetailView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def _get(self, request, pk):
        tag = SourceTag.objects.filter(organization_id=require_org(request).id, pk=pk).first()
        if tag is None:
            raise NotFound()
        return tag

    def patch(self, request, pk):
        tag = self._get(request, pk)
        fields = []
        if "name" in request.data:
            tag.name = _validate_name(request.data["name"])
            _ensure_unique_name(tag.organization_id, tag.name, exclude_id=tag.id)
            fields.append("name")
        if "description" in request.data:
            tag.description = _validate_description(request.data["description"])
            fields.append("description")
        if "color" in request.data:
            tag.color = _validate_color(request.data["color"])
            fields.append("color")
        if not fields:
            raise ValidationError({"detail": "At least one tag field is required."})
        try:
            with transaction.atomic():
                tag.save(update_fields=fields)
        except IntegrityError as exc:
            raise ValidationError({"name": "Tag name already exists."}) from exc
        tag.source_count = _visible_tag_count(request, tag)
        return Response(_tag_payload(tag))

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SourceTagAssignmentView(APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if self.request.method == "PUT":
            return [IsAuthenticated(), IsOrgOperator()]
        return super().get_permissions()

    def _source(self, request, kind, ref_id, action):
        org = require_org(request)
        if kind == "agent":
            exists = Node.objects.filter(
                organization_id=org.id, pk=ref_id, role=NodeRole.AGENT, is_deleted=False,
            ).exists()
            resource_type = "node"
        elif kind == "nas":
            exists = SourceResource.objects.filter(
                organization_id=org.id, pk=ref_id, resource_type=ResourceType.NAS, is_deleted=False,
            ).exists()
            resource_type = "source_resource"
        else:
            raise NotFound()
        if not exists:
            raise NotFound()
        assert_resource_access(request, resource_type, ref_id, action=action)
        return org

    def get(self, request, kind, ref_id):
        org = self._source(request, kind, ref_id, "resources.view")
        tags = SourceTag.objects.filter(
            organization_id=org.id,
            assignments__organization_id=org.id,
            assignments__source_kind=kind,
            assignments__ref_id=ref_id,
        ).order_by("name", "id")
        return Response({"results": [_tag_summary(tag) for tag in tags]})

    def put(self, request, kind, ref_id):
        org = self._source(request, kind, ref_id, "resources.manage")
        ids = request.data.get("tag_ids")
        if (not isinstance(ids, list) or len(ids) > 50
                or any(type(value) is not int or value < 1 for value in ids)
                or len(ids) != len(set(ids))):
            raise ValidationError({"tag_ids": "Expected up to 50 distinct positive tag IDs."})
        with transaction.atomic():
            tags = list(SourceTag.objects.filter(organization_id=org.id, id__in=ids))
            if len(tags) != len(ids):
                raise ValidationError({"tag_ids": "Unknown tag."})
            SourceTagAssignment.objects.filter(
                organization_id=org.id, source_kind=kind, ref_id=ref_id
            ).exclude(tag_id__in=ids).delete()
            SourceTagAssignment.objects.bulk_create(
                [SourceTagAssignment(organization_id=org.id, source_kind=kind, ref_id=ref_id, tag_id=tag_id)
                 for tag_id in ids],
                ignore_conflicts=True,
            )
        return Response({"results": [
            _tag_summary(tag) for tag in sorted(tags, key=lambda tag: (tag.name, tag.id))
        ]})


class SourceTagAssignmentListView(APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        raw = request.query_params.get("ids", "")
        ids = raw.split(",") if raw else []
        if len(ids) > 100 or any(
            (parsed := parse_selectable_id(value)) is None or parsed[0] not in {"agent", "nas"}
            for value in ids
        ):
            raise ValidationError({"ids": "Expected up to 100 agent or NAS source IDs."})
        refs = [
            ("node" if value.startswith("agent:") else "source_resource", int(value.split(":")[1]))
            for value in ids
        ]
        allowed = visible_resource_refs(request, refs, action="resources.view")
        if allowed is not None:
            refs = [ref for ref in refs if ref in allowed]
        kind_ids = {
            "agent": [ref for kind, ref in refs if kind == "node"],
            "nas": [ref for kind, ref in refs if kind == "source_resource"],
        }
        from django.db.models import Q
        assignments = SourceTagAssignment.objects.filter(
            organization_id=require_org(request).id, tag__organization_id=require_org(request).id
        ).filter(
            Q(source_kind="agent", ref_id__in=kind_ids["agent"])
            | Q(source_kind="nas", ref_id__in=kind_ids["nas"])
        ).select_related("tag").order_by("tag__name", "tag_id")
        results = {f"{'agent' if kind == 'node' else 'nas'}:{ref}": [] for kind, ref in refs}
        for assignment in assignments:
            results[f"{assignment.source_kind}:{assignment.ref_id}"].append(
                _tag_summary(assignment.tag)
            )
        return Response({"results": results})


class SourceTagBulkAssignmentView(APIView):
    """Add/remove selected associations without replacing sources' other tags."""

    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        operation = request.data.get("operation")
        if operation not in ("add", "remove"):
            raise ValidationError({"operation": "Must be add or remove."})
        ids = request.data.get("source_ids")
        tag_ids = request.data.get("tag_ids")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
                or any(not isinstance(value, str) or parse_selectable_id(value) is None
                       or not value.startswith(("agent:", "nas:")) for value in ids)
                or len(set(ids)) != len(ids)):
            raise ValidationError({"source_ids": "Expected 1 to 100 unique Agent/NAS source IDs."})
        if (not isinstance(tag_ids, list) or not 1 <= len(tag_ids) <= 50
                or any(type(value) is not int or value < 1 for value in tag_ids)
                or len(set(tag_ids)) != len(tag_ids)):
            raise ValidationError({"tag_ids": "Expected 1 to 50 unique positive tag IDs."})
        refs = [parse_selectable_id(value) for value in ids]
        node_ids = [ref_id for kind, ref_id in refs if kind == "agent"]
        nas_ids = [ref_id for kind, ref_id in refs if kind == "nas"]
        with transaction.atomic():
            if SourceTag.objects.filter(organization_id=org.id, id__in=tag_ids).count() != len(tag_ids):
                raise ValidationError({"tag_ids": "Unknown tag."})
            locked_nodes = list(Node.objects.select_for_update().filter(
                organization_id=org.id, id__in=node_ids, role=NodeRole.AGENT, is_deleted=False,
            ).values_list("id", flat=True))
            locked_nas = list(SourceResource.objects.select_for_update().filter(
                organization_id=org.id, id__in=nas_ids, resource_type=ResourceType.NAS, is_deleted=False,
            ).values_list("id", flat=True))
            if len(locked_nodes) != len(node_ids) or len(locked_nas) != len(nas_ids):
                raise NotFound("One or more backup sources do not exist.")
            resource_refs = [
                ("node" if kind == "agent" else "source_resource", ref_id)
                for kind, ref_id in refs
            ]
            visible = visible_resource_refs(
                request, resource_refs,
                action="resources.manage",
            )
            if visible is not None and not set(resource_refs).issubset(visible):
                raise PermissionDenied("You do not have access to one or more selected resources.")
            assignments = SourceTagAssignment.objects.filter(
                organization_id=org.id,
                tag_id__in=tag_ids,
            ).filter(
                Q(source_kind="agent", ref_id__in=node_ids)
                | Q(source_kind="nas", ref_id__in=nas_ids)
            )
            if operation == "remove":
                count, _ = assignments.delete()
                return Response({"sources": len(ids), "added": 0, "removed": count})
            existing = set(assignments.values_list("source_kind", "ref_id", "tag_id"))
            additions = [
                SourceTagAssignment(
                    organization_id=org.id, source_kind=kind, ref_id=ref_id, tag_id=tag_id,
                )
                for kind, ref_id in refs
                for tag_id in tag_ids
                if (kind, ref_id, tag_id) not in existing
            ]
            SourceTagAssignment.objects.bulk_create(additions, ignore_conflicts=True)
            return Response({"sources": len(ids), "added": len(additions), "removed": 0})


class SourceTagSourceListView(APIView):
    """List visible, live sources currently bound to one tag."""

    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request, pk):
        org = require_org(request)
        if not SourceTag.objects.filter(organization_id=org.id, id=pk).exists():
            raise NotFound()
        search = str(request.query_params.get("search") or "").strip()
        if len(search) > 255:
            raise ValidationError({"search": "Must be at most 255 characters."})
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = max(1, min(int(request.query_params.get("page_size") or 30), 100))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"page": "Must be a positive integer."}) from exc
        assignments = list(SourceTagAssignment.objects.filter(
            organization_id=org.id, tag_id=pk,
        ).values_list("source_kind", "ref_id"))
        node_ids = [ref_id for kind, ref_id in assignments if kind == "agent"]
        nas_ids = [ref_id for kind, ref_id in assignments if kind == "nas"]
        nodes = {node.id: node for node in Node.objects.filter(
            organization_id=org.id, id__in=node_ids, role=NodeRole.AGENT, is_deleted=False,
        )}
        nas = {resource.id: resource for resource in SourceResource.objects.filter(
            organization_id=org.id, id__in=nas_ids, resource_type=ResourceType.NAS, is_deleted=False,
        ).select_related("bound_node")}
        refs = [("node", ref_id) for ref_id in nodes] + [
            ("source_resource", ref_id) for ref_id in nas
        ]
        allowed = visible_resource_refs(request, refs, action="resources.view")
        results = []
        for kind, ref_id in assignments:
            source = nodes.get(ref_id) if kind == "agent" else nas.get(ref_id) if kind == "nas" else None
            ref = ("node" if kind == "agent" else "source_resource", ref_id)
            if source is None or (allowed is not None and ref not in allowed):
                continue
            if search and search.casefold() not in source.name.casefold():
                continue
            results.append({
                "id": f"{kind}:{ref_id}",
                "name": source.name,
                "type": "host" if kind == "agent" else "nas",
                "availability": (
                    source.bound_node.availability
                    if kind == "nas" and source.bound_node else source.availability
                ),
            })
        results.sort(key=lambda row: (row["name"].casefold(), row["id"]))
        offset = (page - 1) * page_size
        return Response({
            "page": page, "page_size": page_size, "count": len(results),
            "results": results[offset:offset + page_size],
        })
