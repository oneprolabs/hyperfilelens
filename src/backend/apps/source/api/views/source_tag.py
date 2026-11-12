"""Source tag management and per-source assignment."""

from django.db import IntegrityError, transaction
from django.db.models import Case, CharField, Count, F, Q, Subquery, Value, When
from django.db.models.functions import Cast, Concat, Lower
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services.interface import write_audit_log_from_request
from apps.iam.org_context import require_org
from apps.iam.permissions_org import (
    IsOrgOperator,
    IsOrgStaffReader,
    IsOrgAuthorizedMember,
)
from apps.iam.resource_access import (
    assert_resource_access,
    filter_resource_queryset,
    record_resource_owner,
    visible_resource_refs,
)
from apps.iam.models import Organization
from apps.iam.query_authorization import request_authorization
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


def _ensure_unique_name(request, tag):
    provider = get_authz_provider()
    validate = getattr(provider, "validate_resource_name", None)
    if callable(validate):
        validate(
            organization_id=tag.organization_id,
            resource_type="source_tag",
            resource_id=tag.pk,
            name=tag.name,
        )
    elif (
        SourceTag.objects.filter(
            organization_id=tag.organization_id,
            name__iexact=tag.name,
        )
        .exclude(pk=tag.pk)
        .exists()
    ):
        raise ValidationError({"name": "Tag name already exists."})


def _visible_tags(request, action="resource.view"):
    return filter_resource_queryset(
        request,
        SourceTag.objects.filter(organization=require_org(request)),
        "source_tag",
        action=action,
    )


def _lock_org(request):
    return Organization.objects.select_for_update(no_key=True).get(
        pk=require_org(request).pk
    )


class TagPermission(BasePermission):
    """Tag definitions have the same operation/scope separation as Sources."""

    def has_permission(self, request, view):
        if get_authz_provider() is None:
            gate = IsOrgStaffReader if request.method == "GET" else IsOrgOperator
            return gate().has_permission(request, view)
        if not IsOrgAuthorizedMember().has_permission(request, view):
            return False
        if getattr(request, "hfl_support_readonly", False):
            return request.method in ("GET", "HEAD", "OPTIONS")
        context = request_authorization(
            request, fresh=request.method not in ("GET", "HEAD", "OPTIONS")
        )
        action = (
            "delete"
            if request.method == "DELETE"
            else "view"
            if request.method in ("GET", "HEAD", "OPTIONS")
            else "manage"
        )
        key = f"protection.tags.{action}"
        if context is None:
            return False
        if request.method == "POST":
            return context.has_org_action(key)
        return context.has_action("source_tag", key)


def _validate_description(value):
    if not isinstance(value, str) or len(value.strip()) > 255:
        raise ValidationError(
            {"description": "Description must be at most 255 characters."}
        )
    return value.strip()


def _validate_color(value):
    if not isinstance(value, str) or value not in SourceTag.COLORS:
        raise ValidationError({"color": "Unknown tag color."})
    return value


def _live_tag_assignments(organization_id, request=None):
    """Do not count orphaned associations or soft-deleted source identities."""
    live_nodes = Node.objects.filter(
        organization_id=organization_id,
        role=NodeRole.AGENT,
        is_deleted=False,
    ).values("id")
    live_nas = SourceResource.objects.filter(
        organization_id=organization_id,
        resource_type=ResourceType.NAS,
        is_deleted=False,
    ).values("id")
    if request is not None:
        live_nodes = filter_resource_queryset(
            request,
            Node.objects.filter(pk__in=live_nodes),
            "node",
            action="resource.view",
        ).values("pk")
        live_nas = filter_resource_queryset(
            request,
            SourceResource.objects.filter(pk__in=live_nas),
            "source_resource",
            action="resource.view",
        ).values("pk")
    return SourceTagAssignment.objects.filter(
        organization_id=organization_id,
        tag__organization_id=organization_id,
    ).filter(
        Q(source_kind="agent", ref_id__in=Subquery(live_nodes))
        | Q(source_kind="nas", ref_id__in=Subquery(live_nas))
    )


def _tag_list(request):
    org = require_org(request)
    return (
        _visible_tags(request)
        .annotate(
            source_count=Count(
                "assignments",
                filter=Q(
                    assignments__id__in=Subquery(
                        _live_tag_assignments(org.pk, request).values("id"),
                    )
                ),
            ),
        )
        .order_by("name", "id")
    )


def _visible_tag_count(request, tag):
    return (
        _live_tag_assignments(tag.organization_id, request)
        .filter(tag_id=tag.pk)
        .count()
    )


class SourceTagListView(APIView):
    permission_classes = [IsAuthenticated, TagPermission]

    def get(self, request):
        org = require_org(request)
        tags = _tag_list(request)
        untagged_count = 0
        for kind, model, filters in (
            ("agent", Node, {"role": NodeRole.AGENT, "is_deleted": False}),
            (
                "nas",
                SourceResource,
                {"resource_type": ResourceType.NAS, "is_deleted": False},
            ),
        ):
            resources = filter_resource_queryset(
                request,
                model.objects.filter(organization=org, **filters),
                "node" if kind == "agent" else "source_resource",
                action="resource.view",
            )
            # Hidden tags must not change a user's visible tag-filter counts.
            tagged = SourceTagAssignment.objects.filter(
                organization=org,
                source_kind=kind,
                tag_id__in=_visible_tags(request).values("pk"),
            ).values("ref_id")
            untagged_count += resources.exclude(pk__in=Subquery(tagged)).count()
        total = tags.count()
        if "page" in request.query_params or "page_size" in request.query_params:
            try:
                page = int(request.query_params.get("page", 1))
                size = int(request.query_params.get("page_size", 30))
                if page < 1 or not 1 <= size <= 100 or (page - 1) * size > 2**63 - 1:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    "Use a positive page and page_size <= 100."
                ) from exc
            tags = tags[(page - 1) * size : page * size]
        tags = list(tags)
        authority = request_authorization(request)
        actions = ("protection.tags.manage", "protection.tags.delete")
        capabilities = (
            authority.visible_refs_by_action(
                [("source_tag", tag.pk) for tag in tags],
                actions,
            )
            if authority is not None
            else None
        )
        results = []
        for tag in tags:
            results.append(
                {
                    **_tag_payload(tag),
                    **{
                        "can_" + action.rsplit(".", 1)[1]: capabilities is None
                        or ("source_tag", tag.pk) in capabilities[action]
                        for action in actions
                    },
                }
            )
        return Response(
            {
                "results": results,
                "count": total,
                "can_create": authority.has_org_action("protection.tags.manage")
                if authority
                else get_authz_provider() is None,
                "untagged_source_count": untagged_count,
            }
        )

    @transaction.atomic
    def post(self, request):
        org = _lock_org(request)
        if get_authz_provider() is not None:
            authority = request_authorization(request, fresh=True)
            if (
                authority is None
                or not authority.has_org_action("protection.tags.manage")
                or not authority.fence_queryset(
                    Organization.objects.filter(pk=org.pk)
                ).exists()
            ):
                raise PermissionDenied("Tag creation permission is not granted.")
        name = _validate_name(request.data.get("name"))
        description = _validate_description(request.data.get("description", ""))
        color = _validate_color(request.data.get("color", "neutral"))
        try:
            with transaction.atomic():
                tag = SourceTag.objects.create(
                    organization_id=org.id,
                    name=name,
                    description=description,
                    color=color,
                )
                record_resource_owner(
                    organization_id=org.pk,
                    resource_type="source_tag",
                    resource_id=tag.pk,
                    owner_id=request.user.pk,
                )
                _ensure_unique_name(request, tag)
                write_audit_log_from_request(
                    request,
                    organization=org,
                    action="source_tag.create",
                    resource_type="source_tag",
                    resource_id=str(tag.pk),
                    resource_name=tag.name,
                )
        except IntegrityError as exc:
            raise ValidationError({"name": "Tag name already exists."}) from exc
        tag.source_count = 0
        return Response(_tag_payload(tag), status=status.HTTP_201_CREATED)


class SourceTagDetailView(APIView):
    permission_classes = [IsAuthenticated, TagPermission]

    def _get(self, request, pk):
        action = (
            "resource.delete"
            if request.method == "DELETE"
            else "resource.view"
            if request.method in ("GET", "HEAD", "OPTIONS")
            else "resource.manage"
        )
        tag = _visible_tags(request, action).filter(pk=pk).first()
        if tag is None:
            raise NotFound()
        return tag

    def get(self, request, pk):
        tag = self._get(request, pk)
        tag.source_count = _visible_tag_count(request, tag)
        return Response(_tag_payload(tag))

    @transaction.atomic
    def patch(self, request, pk):
        _lock_org(request)
        tag = self._get(request, pk)
        fields = []
        if "name" in request.data:
            tag.name = _validate_name(request.data["name"])
            _ensure_unique_name(request, tag)
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
        write_audit_log_from_request(
            request,
            organization=require_org(request),
            action="source_tag.update",
            resource_type="source_tag",
            resource_id=str(tag.pk),
            resource_name=tag.name,
        )
        return Response(_tag_payload(tag))

    @transaction.atomic
    def delete(self, request, pk):
        _lock_org(request)
        tag = self._get(request, pk)
        write_audit_log_from_request(
            request,
            organization=require_org(request),
            action="source_tag.delete",
            resource_type="source_tag",
            resource_id=str(tag.pk),
            resource_name=tag.name,
        )
        tag.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SourceTagAssignmentView(APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get_permissions(self):
        if get_authz_provider() is not None:
            # Binding existing Tags edits the Source, not organization Tag definitions.
            return [IsAuthenticated(), IsOrgAuthorizedMember()]
        if self.request.method == "PUT":
            return [IsAuthenticated(), IsOrgOperator()]
        return super().get_permissions()

    def _source(self, request, kind, ref_id, action):
        org = require_org(request)
        if kind == "agent":
            exists = Node.objects.filter(
                organization_id=org.id,
                pk=ref_id,
                role=NodeRole.AGENT,
                is_deleted=False,
            ).exists()
            resource_type = "node"
        elif kind == "nas":
            exists = SourceResource.objects.filter(
                organization_id=org.id,
                pk=ref_id,
                resource_type=ResourceType.NAS,
                is_deleted=False,
            ).exists()
            resource_type = "source_resource"
        else:
            raise NotFound()
        if not exists:
            raise NotFound()
        assert_resource_access(request, resource_type, ref_id, action=action)
        return org

    def get(self, request, kind, ref_id):
        org = self._source(request, kind, ref_id, "resource.view")
        tags = (
            _visible_tags(request)
            .filter(
                organization_id=org.id,
                assignments__organization_id=org.id,
                assignments__source_kind=kind,
                assignments__ref_id=ref_id,
            )
            .order_by("name", "id")
        )
        return Response({"results": [_tag_summary(tag) for tag in tags]})

    @transaction.atomic
    def put(self, request, kind, ref_id):
        _lock_org(request)
        org = self._source(request, kind, ref_id, "resource.manage")
        ids = request.data.get("tag_ids")
        if (
            not isinstance(ids, list)
            or len(ids) > 50
            or any(type(value) is not int or value < 1 for value in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValidationError(
                {"tag_ids": "Expected up to 50 distinct positive tag IDs."}
            )
        with transaction.atomic():
            tags = list(_visible_tags(request).filter(id__in=ids))
            if len(tags) != len(ids):
                raise ValidationError({"tag_ids": "Unknown tag."})
            SourceTagAssignment.objects.filter(
                organization_id=org.id,
                source_kind=kind,
                ref_id=ref_id,
                tag_id__in=_visible_tags(request).values("pk"),
            ).exclude(tag_id__in=ids).delete()
            SourceTagAssignment.objects.bulk_create(
                [
                    SourceTagAssignment(
                        organization_id=org.id,
                        source_kind=kind,
                        ref_id=ref_id,
                        tag_id=tag_id,
                    )
                    for tag_id in ids
                ],
                ignore_conflicts=True,
            )
        return Response(
            {
                "results": [
                    _tag_summary(tag)
                    for tag in sorted(tags, key=lambda tag: (tag.name, tag.id))
                ]
            }
        )


class SourceTagAssignmentListView(APIView):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]

    def get(self, request):
        raw = request.query_params.get("ids", "")
        ids = raw.split(",") if raw else []
        if len(ids) > 100 or any(
            (parsed := parse_selectable_id(value)) is None
            or parsed[0] not in {"agent", "nas"}
            for value in ids
        ):
            raise ValidationError(
                {"ids": "Expected up to 100 agent or NAS source IDs."}
            )
        refs = [
            (
                "node" if value.startswith("agent:") else "source_resource",
                int(value.split(":")[1]),
            )
            for value in ids
        ]
        allowed = visible_resource_refs(request, refs, action="resource.view")
        if allowed is not None:
            refs = [ref for ref in refs if ref in allowed]
        kind_ids = {
            "agent": [ref for kind, ref in refs if kind == "node"],
            "nas": [ref for kind, ref in refs if kind == "source_resource"],
        }
        from django.db.models import Q

        assignments = (
            SourceTagAssignment.objects.filter(
                organization_id=require_org(request).id,
                tag__organization_id=require_org(request).id,
                tag_id__in=_visible_tags(request).values("pk"),
            )
            .filter(
                Q(source_kind="agent", ref_id__in=kind_ids["agent"])
                | Q(source_kind="nas", ref_id__in=kind_ids["nas"])
            )
            .select_related("tag")
            .order_by("tag__name", "tag_id")
        )
        results = {
            f"{'agent' if kind == 'node' else 'nas'}:{ref}": [] for kind, ref in refs
        }
        for assignment in assignments:
            results[f"{assignment.source_kind}:{assignment.ref_id}"].append(
                _tag_summary(assignment.tag)
            )
        return Response({"results": results})


class SourceTagBulkAssignmentView(APIView):
    """Add/remove selected associations without replacing sources' other tags."""

    permission_classes = [IsAuthenticated, IsOrgOperator]

    def get_permissions(self):
        if get_authz_provider() is not None:
            return [IsAuthenticated(), IsOrgAuthorizedMember()]
        return super().get_permissions()

    @transaction.atomic
    def post(self, request):
        org = _lock_org(request)
        operation = request.data.get("operation")
        if operation not in ("add", "remove"):
            raise ValidationError({"operation": "Must be add or remove."})
        ids = request.data.get("source_ids")
        tag_ids = request.data.get("tag_ids")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 100
            or any(
                not isinstance(value, str)
                or parse_selectable_id(value) is None
                or not value.startswith(("agent:", "nas:"))
                for value in ids
            )
            or len(set(ids)) != len(ids)
        ):
            raise ValidationError(
                {"source_ids": "Expected 1 to 100 unique Agent/NAS source IDs."}
            )
        if (
            not isinstance(tag_ids, list)
            or not 1 <= len(tag_ids) <= 50
            or any(type(value) is not int or value < 1 for value in tag_ids)
            or len(set(tag_ids)) != len(tag_ids)
        ):
            raise ValidationError(
                {"tag_ids": "Expected 1 to 50 unique positive tag IDs."}
            )
        refs = [parse_selectable_id(value) for value in ids]
        node_ids = [ref_id for kind, ref_id in refs if kind == "agent"]
        nas_ids = [ref_id for kind, ref_id in refs if kind == "nas"]
        with transaction.atomic():
            if _visible_tags(request).filter(id__in=tag_ids).count() != len(tag_ids):
                raise ValidationError({"tag_ids": "Unknown tag."})
            locked_nodes = list(
                Node.objects.select_for_update()
                .filter(
                    organization_id=org.id,
                    id__in=node_ids,
                    role=NodeRole.AGENT,
                    is_deleted=False,
                )
                .values_list("id", flat=True)
            )
            locked_nas = list(
                SourceResource.objects.select_for_update()
                .filter(
                    organization_id=org.id,
                    id__in=nas_ids,
                    resource_type=ResourceType.NAS,
                    is_deleted=False,
                )
                .values_list("id", flat=True)
            )
            if len(locked_nodes) != len(node_ids) or len(locked_nas) != len(nas_ids):
                raise NotFound("One or more backup sources do not exist.")
            resource_refs = [
                ("node" if kind == "agent" else "source_resource", ref_id)
                for kind, ref_id in refs
            ]
            visible = visible_resource_refs(
                request,
                resource_refs,
                action="resource.manage",
            )
            if visible is not None and not set(resource_refs).issubset(visible):
                raise PermissionDenied(
                    "You do not have access to one or more selected resources."
                )
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
                    organization_id=org.id,
                    source_kind=kind,
                    ref_id=ref_id,
                    tag_id=tag_id,
                )
                for kind, ref_id in refs
                for tag_id in tag_ids
                if (kind, ref_id, tag_id) not in existing
            ]
            SourceTagAssignment.objects.bulk_create(additions, ignore_conflicts=True)
            return Response(
                {"sources": len(ids), "added": len(additions), "removed": 0}
            )


class SourceTagSourceListView(APIView):
    """Authorize both the label and source rows in SQL before count/page."""

    permission_classes = [IsAuthenticated, TagPermission]

    def get(self, request, pk):
        org = require_org(request)
        if not _visible_tags(request).filter(pk=pk).exists():
            raise NotFound()
        search = str(request.query_params.get("search") or "").strip()
        if len(search) > 255:
            raise ValidationError({"search": "Must be at most 255 characters."})
        try:
            page = int(request.query_params.get("page") or 1)
            page_size = int(request.query_params.get("page_size") or 30)
            if (
                page < 1
                or not 1 <= page_size <= 100
                or (page - 1) * page_size > 2**63 - 1
            ):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"page": "Use a positive page and page_size <= 100."}
            ) from exc
        branches = []
        for kind, model, filters in (
            ("agent", Node, {"role": NodeRole.AGENT}),
            ("nas", SourceResource, {"resource_type": ResourceType.NAS}),
        ):
            bindings = (
                _live_tag_assignments(org.pk)
                .filter(tag_id=pk, source_kind=kind)
                .values("ref_id")
            )
            rows = filter_resource_queryset(
                request,
                model.objects.filter(
                    organization=org, pk__in=Subquery(bindings), **filters
                ),
                "node" if kind == "agent" else "source_resource",
                action="resource.view",
            )
            if search:
                rows = rows.filter(name__icontains=search)
            availability = (
                F("availability")
                if kind == "agent"
                else Case(
                    When(bound_node__isnull=False, then=F("bound_node__availability")),
                    default=F("availability"),
                    output_field=CharField(),
                )
            )
            branches.append(
                rows.order_by()
                .annotate(
                    _source_id=Concat(Value(kind + ":"), Cast("pk", CharField())),
                    _type=Value("host" if kind == "agent" else "nas"),
                    _availability=availability,
                    _sort=Lower("name"),
                )
                .values("_source_id", "name", "_type", "_availability", "_sort")
            )
        rows = branches[0].union(branches[1], all=True).order_by("_sort", "_source_id")
        total = rows.count()
        offset = (page - 1) * page_size
        return Response(
            {
                "page": page,
                "page_size": page_size,
                "count": total,
                "results": [
                    {
                        "id": row["_source_id"],
                        "name": row["name"],
                        "type": row["_type"],
                        "availability": row["_availability"],
                    }
                    for row in rows[offset : offset + page_size]
                ],
            }
        )
