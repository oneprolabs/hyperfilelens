"""
IAM views.
"""

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import JsonResponse

from rest_framework import permissions, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action

from apps.iam.models import Membership, Organization, PersonalApiKey
from apps.iam.permissions_org import (
    IsOrgMemberReader,
    IsOrgReader,
    get_membership,
    resolve_org_key,
)
from apps.iam.serializers import (
    MembershipSerializer,
    OrganizationSerializer,
    PersonalApiKeySerializer,
)
from apps.iam.services.membership_service import (
    assert_can_delete,
    MembershipPolicyError,
)

User = get_user_model()


class CanManageMembershipOperation(permissions.BasePermission):
    """Member lifecycle and Role assignment are independent tenant grants."""

    def has_permission(self, request, view):
        from common.extension_spi import get_authz_provider

        membership = get_membership(request)
        if membership is None or getattr(request, "hfl_support_readonly", False):
            return False
        provider = get_authz_provider()
        if provider is None:
            return True
        if view.action == "bulk":
            operation = request.data.get("action") if isinstance(request.data, dict) else None
            required = {
                "configuration.members.remove"
                if operation == "remove"
                else "configuration.members.manage"
            }
        elif request.method == "DELETE":
            required = {"configuration.members.remove"}
        elif view.action == "create":
            required = {"configuration.members.manage"}
        else:
            fields = request.data if isinstance(request.data, dict) else {}
            required = set()
            if "role" in fields:
                required.add("configuration.members.assign_role")
            if "is_active" in fields:
                required.add("configuration.members.manage")
            if not required:
                # Authorized administrators receive serializer validation
                # errors for malformed/no-op bodies; this grants no mutation.
                return any(
                    provider.has_org_action(request.user, membership.organization.key, key)
                    for key in (
                        "configuration.members.manage",
                        "configuration.members.assign_role",
                    )
                )
        return bool(required) and all(
            provider.has_org_action(
                request.user,
                membership.organization.key,
                key,
            )
            for key in required
        )


def health(_request):
    return JsonResponse({"app": "iam", "status": "ok"})


class OrganizationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated, IsOrgReader]
    governance_read_action = "configuration.organizations.view"
    # Organization is currently a read-only governance surface. Creation and
    # mutation must go through the registration/organization services so quota,
    # owner membership, and EE initialization cannot be bypassed.
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        qs = (
            Organization.objects.annotate(
                member_count=Count(
                    "memberships", filter=Q(memberships__is_active=True)
                ),
            )
            .distinct()
        )
        from common.platform_staff import is_installation_admin

        if not is_installation_admin(self.request.user):
            qs = qs.filter(
                memberships__user=self.request.user, memberships__is_active=True,
            )
        org_key = resolve_org_key(self.request)
        # This is the tenant organization surface, never a platform directory.
        qs = qs.filter(key=org_key, is_active=True)
        return qs.order_by("id")


class MembershipViewSet(viewsets.ModelViewSet):
    serializer_class = MembershipSerializer
    governance_org_action = "members.manage"

    def get_serializer_class(self):
        if self.action == "create":
            from apps.iam.serializers import MemberOnboardingSerializer

            return MemberOnboardingSerializer
        return MembershipSerializer

    def get_permissions(self):
        permission_class = (
            IsOrgMemberReader
            if self.request.method in permissions.SAFE_METHODS
            else CanManageMembershipOperation
        )
        return [IsAuthenticated(), permission_class()]

    @action(detail=False, methods=["get"])
    def management(self, request):
        from apps.iam.services.member_management import membership_management_profile

        return Response(membership_management_profile(request))

    @action(detail=False, methods=["post"])
    def bulk(self, request):
        """Apply one member action atomically, never delete accounts/resources."""
        from django.db import transaction
        from rest_framework.exceptions import NotFound, PermissionDenied

        from apps.iam.bulk import MembershipBulkActionSerializer
        from apps.iam.services.member_management import (
            assert_membership_management,
            membership_management_permissions,
            membership_management_profile,
        )
        from apps.subscription.services.interface import enforce_license_quota

        serializer = MembershipBulkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        actor = get_membership(request)
        if actor is None:
            raise PermissionDenied("Organization member management is not granted.")
        with transaction.atomic():
            Organization.objects.select_for_update(no_key=True).get(
                pk=actor.organization_id
            )
            # Authority may have changed while waiting for the organization lock.
            fresh_actor = get_membership(request)
            if (
                fresh_actor is None
                or not fresh_actor.user.is_active
                or not CanManageMembershipOperation().has_permission(request, self)
            ):
                raise PermissionDenied("Organization member management is not granted.")
            targets = list(
                Membership.objects.select_for_update(of=("self", "user", "organization"))
                .select_related("user", "user__profile", "organization")
                .filter(
                    organization_id=actor.organization_id,
                    pk__in=data["membership_ids"],
                )
                .order_by("pk")
            )
            if len(targets) != len(data["membership_ids"]):
                raise NotFound("One or more memberships were not found.")
            kind = data["action"]
            changed, skipped = [], []
            profile = membership_management_profile(request)
            for target in targets:
                noop = kind != "remove" and target.is_active == (kind == "activate")
                if noop:
                    flags = membership_management_permissions(
                        request, target, profile=profile
                    )
                    if (
                        flags["protected_reason"]
                        or not profile["can_manage"]
                        or (kind == "activate" and not target.user.is_active)
                    ):
                        raise PermissionDenied(
                            "This organization membership cannot be managed by you."
                        )
                    skipped.append(target.pk)
                    continue
                assert_membership_management(
                    request, operation=f"can_{kind}", target=target
                )
                changed.append(target)
            if kind == "activate" and changed:
                # Check the entire addition before changing the first member.
                # The shared org lock serializes this with onboarding and quota.
                enforce_license_quota(
                    actor.organization, "max_users", additional=len(changed)
                )
            for target in changed:
                if kind == "remove":
                    self.perform_destroy(target)
                else:
                    update = self.get_serializer(
                        target, data={"is_active": kind == "activate"}, partial=True
                    )
                    update.is_valid(raise_exception=True)
                    self.perform_update(update)
            return Response({
                "action": kind, "updated": len(changed),
                "skipped": len(skipped), "skipped_ids": skipped,
            })

    def get_queryset(self):
        membership = get_membership(self.request)
        if membership is None:
            return Membership.objects.none()
        queryset = (
            Membership.objects.select_related("user", "user__profile", "organization")
            .filter(organization_id=membership.organization.pk)
            .order_by("id")
        )
        search = str(self.request.query_params.get("search") or "").strip()
        if search:
            from common.extension_spi import get_authz_provider

            projection = getattr(
                get_authz_provider(), "membership_directory_user_ids", None
            )
            extra_ids = (
                projection(
                    organization_id=membership.organization.pk,
                    search=search,
                )
                if callable(projection)
                else set()
            )
            queryset = queryset.filter(
                Q(user__email__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user_id__in=extra_ids)
            )
        return queryset

    def create(self, request, *args, **kwargs):
        from apps.iam.serializers import MemberOnboardingSerializer
        from apps.iam.services.member_onboarding import onboard_member
        from common.extension_spi import get_authz_provider

        serializer = MemberOnboardingSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = data.get("role") or (
            "member" if callable(getattr(get_authz_provider(), "effective_permissions", None))
            else Membership.Role.OPERATOR
        )
        result = onboard_member(
            request, email=data.get("email"), user_id=data.get("user"),
            role=role, is_active=data["is_active"],
        )
        payload = dict(MembershipSerializer(
            result.membership, context=self.get_serializer_context(),
        ).data)
        payload["account_created"] = result.account_created
        if result.temporary_password is not None:
            payload["credentials"] = {
                "email": result.membership.user.email,
                "temporary_password": result.temporary_password,
            }
        response = Response(payload, status=201)
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        return response

    def perform_update(self, serializer):
        from django.db import transaction
        from django.shortcuts import get_object_or_404
        from apps.audit.services.interface import write_audit_log_from_request
        from apps.iam.services.member_management import assert_membership_management
        from apps.iam.services.membership_service import authoritative_role

        with transaction.atomic():
            Organization.objects.select_for_update(no_key=True).get(
                pk=serializer.instance.organization_id
            )
            target = get_object_or_404(
                Membership.objects.select_for_update().select_related(
                    "organization", "user"
                ),
                pk=serializer.instance.pk,
            )
            fields = serializer.validated_data
            if "role" in fields:
                assert_membership_management(
                    self.request,
                    operation="can_edit_role",
                    target=target,
                    role=fields["role"],
                )
            if "is_active" in fields:
                # Idempotent status writes still require authority over this target.
                assert_membership_management(
                    self.request,
                    operation="can_activate"
                    if fields["is_active"]
                    else "can_deactivate",
                    target=target,
                )
                if fields["is_active"] and not target.is_active:
                    assert_membership_management(
                        self.request, operation="can_activate", target=target
                    )
            before = {"role": authoritative_role(target), "is_active": target.is_active}
            serializer.instance = target
            updated = serializer.save()
            after = {
                "role": authoritative_role(updated),
                "is_active": updated.is_active,
            }
            if before != after:
                write_audit_log_from_request(
                    self.request,
                    organization=updated.organization,
                    action="membership.update",
                    resource_type="membership",
                    resource_id=str(updated.pk),
                    resource_name=updated.user.email or updated.user.username,
                    changes={
                        "before": before,
                        "after": after,
                        "user_id": updated.user_id,
                    },
                )

    def perform_destroy(self, instance):
        from django.db import transaction

        from apps.iam.services.membership_service import sync_member_role

        with transaction.atomic():
            # Serialize with Group association writes, which lock this org first.
            Organization.objects.select_for_update(no_key=True).get(
                pk=instance.organization_id
            )
            from django.shortcuts import get_object_or_404
            from apps.iam.services.member_management import assert_membership_management

            instance = get_object_or_404(
                Membership.objects.select_for_update().select_related(
                    "organization", "user"
                ),
                pk=instance.pk,
            )
            assert_membership_management(
                self.request, operation="can_remove", target=instance
            )
            try:
                assert_can_delete(instance)
            except MembershipPolicyError as exc:
                from rest_framework.exceptions import ValidationError

                raise ValidationError(exc.detail) from exc
            user_id = instance.user_id
            organization_id = instance.organization_id
            from apps.audit.services.interface import write_audit_log_from_request
            from apps.iam.services.membership_service import authoritative_role

            write_audit_log_from_request(
                self.request,
                organization=instance.organization,
                action="membership.remove",
                resource_type="membership",
                resource_id=str(instance.pk),
                resource_name=instance.user.email or instance.user.username,
                changes={
                    "removed_user_id": user_id,
                    "before": {
                        "role": authoritative_role(instance),
                        "is_active": instance.is_active,
                    },
                    "after": None,
                },
                details="Organization membership removed; Group grants are revoked without deleting resources.",
            )
            instance.delete()
            sync_member_role(
                user_id=user_id, organization_id=organization_id, role=None
            )


class PersonalApiKeyViewSet(viewsets.ModelViewSet):
    serializer_class = PersonalApiKeySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PersonalApiKey.objects.filter(user=self.request.user).order_by(
            "-created_at"
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
