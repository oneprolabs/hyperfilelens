"""User-scoped in-app notifications."""

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.permissions_org import IsOrgMember
from apps.notification.api.views._org import require_org
from apps.notification.models import UserNotification


def _notification_queryset(*, user, organization_id):
    return UserNotification.objects.filter(
        user=user,
        organization_id=organization_id,
    )


def _serialize(notification):
    return {
        "id": str(notification.id),
        "kind": notification.source_type,
        "title": notification.title,
        "summary": notification.summary,
        "severity": notification.severity,
        "occurred_at": notification.updated_at,
        "updated_at": notification.updated_at,
        "is_read": notification.read_at is not None,
        "to": notification.target_url,
    }


class UserNotificationInboxView(APIView):
    permission_classes = [IsAuthenticated, IsOrgMember]

    def get(self, request):
        organization = require_org(request)
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(100, max(1, int(request.query_params.get("page_size") or 20)))
        except ValueError:
            return Response(
                {"detail": "page and page_size must be integers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = _notification_queryset(
            user=request.user,
            organization_id=organization.id,
        )
        count = queryset.count()
        unread_count = queryset.filter(read_at__isnull=True).count()
        start = (page - 1) * page_size
        rows = queryset[start : start + page_size]
        return Response(
            {
                "count": count,
                "unread_count": unread_count,
                "results": [_serialize(row) for row in rows],
            }
        )


class UserNotificationReadView(APIView):
    permission_classes = [IsAuthenticated, IsOrgMember]

    def post(self, request, notification_id):
        organization = require_org(request)
        updated = UserNotification.objects.filter(
            id=notification_id,
            user=request.user,
            organization_id=organization.id,
        ).update(read_at=timezone.now())
        if not updated:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserNotificationMarkAllReadView(APIView):
    permission_classes = [IsAuthenticated, IsOrgMember]

    def post(self, request):
        organization = require_org(request)
        UserNotification.objects.filter(
            user=request.user,
            organization=organization,
            read_at__isnull=True,
        ).update(
            read_at=timezone.now(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
