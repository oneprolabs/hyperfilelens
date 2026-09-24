from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.alert.models import AlertPolicy, AlertRecord
from apps.audit.services.interface import write_audit_log
from apps.iam.permissions_org import IsOrgStaffReader, IsOrgWriter

from apps.notification.api.pagination import NotificationPagination
from apps.notification.api.serializers import (
    BulkChannelDeleteSerializer,
    BulkChannelStateSerializer,
    NotificationChannelSerializer,
)
from apps.notification.api.views._org import require_org
from apps.notification.constants import ChannelType
from apps.notification.models import NotificationChannel, NotificationLog
from apps.notification.selectors.interface import channel_statistics, channels_for_org, filter_channels
from apps.notification.services.internal.log_details import notification_log_details
from apps.notification.services.interface import test_channel
from apps.notification.services.internal.channel_bulk import (
    bulk_delete_channels,
    bulk_set_channel_state,
)
from apps.iam.models import Organization


class NotificationChannelViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationChannelSerializer
    permission_classes = [IsAuthenticated, IsOrgWriter]
    pagination_class = NotificationPagination

    def get_permissions(self):
        if self.action in ("list", "retrieve", "details", "stats", "association_summary"):
            return [IsAuthenticated(), IsOrgStaffReader()]
        return super().get_permissions()

    def get_queryset(self):
        org = require_org(self.request)
        params = self.request.query_params
        return filter_channels(
            channels_for_org(organization_id=org.id),
            search=params.get("search", ""),
            channel_type=params.get("type", ""),
            enabled=params.get("enabled", ""),
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        try:
            ctx["organization"] = require_org(self.request)
        except Exception:
            pass
        return ctx

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = list(page if page is not None else queryset)
        context = self.get_serializer_context()
        context.update(self._list_summary_context(rows))
        serializer = self.get_serializer(rows, many=True, context=context)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def _list_summary_context(self, channels: list[NotificationChannel]) -> dict:
        ids = [int(ch.id) for ch in channels]
        if not ids:
            return {
                "policies_count_by_channel": {},
                "last_delivery_by_channel": {},
            }

        channel_id_strs = {str(i) for i in ids}
        policies_count_by_channel = {int(i): 0 for i in ids}
        org_id = int(channels[0].organization_id)
        policies = AlertPolicy.objects.filter(organization_id=org_id).only(
            "notification_channel_ids"
        )
        for policy in policies:
            for raw_id in policy.notification_channel_ids or []:
                if str(raw_id) not in channel_id_strs:
                    continue
                try:
                    policies_count_by_channel[int(raw_id)] += 1
                except (TypeError, ValueError, KeyError):
                    continue

        last_delivery_by_channel: dict[int, dict] = {}
        logs = NotificationLog.objects.filter(
            organization_id=org_id,
            channel_id__in=ids,
        ).only("channel_id", "status", "sent_at").order_by("-sent_at", "-id")
        for log in logs:
            channel_id = int(log.channel_id)
            if channel_id in last_delivery_by_channel:
                continue
            last_delivery_by_channel[channel_id] = {
                "status": log.status,
                "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            }
            if len(last_delivery_by_channel) == len(ids):
                break

        return {
            "policies_count_by_channel": policies_count_by_channel,
            "last_delivery_by_channel": last_delivery_by_channel,
        }

    def perform_create(self, serializer):
        org = require_org(self.request)
        channel = serializer.save(organization=org)
        write_audit_log(
            organization=org,
            user=self.request.user,
            action="notification.channel.create",
            target_type="notification_channel",
            target_id=str(channel.id),
            resource_type="notification_channel",
            resource_id=str(channel.id),
            resource_name=channel.name,
            ip_address=self.request.META.get("REMOTE_ADDR"),
            user_agent=str(self.request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={"channel_type": channel.channel_type},
        )

    def perform_update(self, serializer):
        channel = serializer.save()
        write_audit_log(
            organization=channel.organization,
            user=self.request.user,
            action="notification.channel.update",
            target_type="notification_channel",
            target_id=str(channel.id),
            resource_type="notification_channel",
            resource_id=str(channel.id),
            resource_name=channel.name,
            ip_address=self.request.META.get("REMOTE_ADDR"),
            user_agent=str(self.request.META.get("HTTP_USER_AGENT", "") or ""),
        )

    def perform_destroy(self, instance):
        org = instance.organization
        channel_id = str(instance.id)
        name = instance.name
        instance.delete()
        write_audit_log(
            organization=org,
            user=self.request.user,
            action="notification.channel.delete",
            target_type="notification_channel",
            target_id=channel_id,
            resource_type="notification_channel",
            resource_id=channel_id,
            resource_name=name,
            ip_address=self.request.META.get("REMOTE_ADDR"),
            user_agent=str(self.request.META.get("HTTP_USER_AGENT", "") or ""),
        )

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        channel = self.get_object()
        try:
            return Response(test_channel(channel))
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"status": "failed", "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=False, methods=["post"], url_path="test-config")
    def test_config(self, request):
        """Test the editor's draft without persisting it."""
        org = require_org(request)
        channel_id = request.data.get("channel_id")
        channel = None
        if channel_id is not None:
            try:
                channel = self.get_queryset().get(pk=channel_id)
            except (NotificationChannel.DoesNotExist, TypeError, ValueError):
                return Response({"detail": "Channel not found."}, status=status.HTTP_404_NOT_FOUND)

        channel_type = str(request.data.get("type") or (channel.channel_type if channel else "")).strip()
        config = request.data.get("config")
        if not channel_type or not isinstance(config, dict):
            return Response(
                {"detail": "Channel type and config are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if channel_type not in (ChannelType.EMAIL, ChannelType.WEBHOOK, ChannelType.DINGTALK, ChannelType.WECOM):
            return Response({"detail": "Unsupported channel type."}, status=status.HTTP_400_BAD_REQUEST)
        if channel and channel.channel_type != channel_type:
            return Response({"detail": "Channel type cannot be changed."}, status=status.HTTP_400_BAD_REQUEST)
        config = dict(config)
        if channel:
            existing = channel.config or {}
            clear_secret = config.pop("clear_secret", False) is True
            for field in ("smtp_password", "token", "secret", "authorization", "api_key"):
                if config.get(field) in (None, "", "********") and existing.get(field) and not (field == "secret" and clear_secret):
                    config[field] = existing[field]
            if clear_secret:
                config.pop("secret", None)
            organization = channel.organization
            name = channel.name
            is_active = channel.is_active
        else:
            organization = org
            name = str(request.data.get("name") or "Draft notification channel")
            is_active = True
        draft = NotificationChannel(
            organization=organization,
            name=name,
            channel_type=channel_type,
            config=config,
            is_active=is_active,
        )
        try:
            return Response(test_channel(draft))
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"status": "failed", "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["get"])
    def details(self, request, pk=None):
        channel = self.get_object()
        channel_id_str = str(channel.id)
        policies = [
            p
            for p in AlertPolicy.objects.filter(organization=channel.organization)
            if channel_id_str in [str(x) for x in (p.notification_channel_ids or [])]
        ]
        all_logs = NotificationLog.objects.filter(
            organization=channel.organization,
            channel_id=channel.id,
        ).order_by("-sent_at")
        recent_logs = list(all_logs[:20])
        success_count = all_logs.filter(status="success").count()
        failed_count = all_logs.filter(status="failed").count()
        logs_count = all_logs.count()
        last_success = all_logs.filter(status="success").first()
        last_failed = all_logs.filter(status="failed").first()

        alert_ids = list(all_logs.values_list("alert_record_id", flat=True).distinct())
        recent_alert_ids = [log.alert_record_id for log in recent_logs if log.alert_record_id]
        recent_records = list(
            AlertRecord.objects.filter(id__in=recent_alert_ids).order_by("-created_at")[:10]
        )

        return Response(
            {
                "channel": NotificationChannelSerializer(channel).data,
                "associated_policies": [
                    {
                        "id": str(p.id),
                        "name": p.name,
                        "description": p.description,
                        "enabled": p.enabled,
                        "created_at": p.created_at.isoformat(),
                    }
                    for p in policies
                ],
                "recent_alerts": [
                    {
                        "id": str(r.id),
                        "title": r.title,
                        "message": r.message,
                        "severity": r.severity,
                        "status": r.status,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in recent_records
                ],
                "notification_logs": notification_log_details(recent_logs),
                "stats": {
                    "policies_count": len(policies),
                    "alerts_count": AlertRecord.objects.filter(id__in=alert_ids).count()
                    if alert_ids
                    else 0,
                    "logs_count": logs_count,
                    "logs_success": success_count,
                    "logs_failed": failed_count,
                    "success_rate": round((success_count / logs_count) * 100, 2)
                    if logs_count
                    else 0,
                    "last_success_at": last_success.sent_at.isoformat()
                    if last_success
                    else None,
                    "last_failed_at": last_failed.sent_at.isoformat()
                    if last_failed
                    else None,
                },
            }
        )

    @action(detail=False, methods=["get"])
    def stats(self, request):
        org = require_org(request)
        params = request.query_params
        return Response(
            channel_statistics(
                organization_id=org.id,
                search=params.get("search", ""),
                channel_type=params.get("type", ""),
                enabled=params.get("enabled", ""),
            )
        )

    @action(detail=False, methods=["get"], url_path="association-summary")
    def association_summary(self, request):
        """Lightweight summary used by the front-end to render confirmation
        dialogs before bulk operations.

        Returns, for each id that belongs to the caller's org::

            { "items": [
                {"id": 1, "name": "...", "type": "email",
                 "policies_count": 2,
                 "policies": [{"id": "uuid", "name": "...", "enabled": true}, ...]},
                ...
              ],
              "missing": [3, 7]   # ids the caller does not own / not found
            }
        """
        org = require_org(request)
        raw = request.query_params.get("ids", "") or ""
        try:
            ids: list[int] = []
            seen: set[int] = set()
            for chunk in raw.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                try:
                    value = int(chunk)
                except (TypeError, ValueError):
                    continue
                if value in seen:
                    continue
                seen.add(value)
                ids.append(value)
        except Exception:  # noqa: BLE001
            ids = []
        # Cap to a reasonable batch size to avoid pathological requests.
        ids = ids[:200]

        if not ids:
            return Response({"items": [], "missing": []})

        channels = list(
            NotificationChannel.objects.filter(
                organization_id=org.id,
                id__in=ids,
            ).only("id", "name", "channel_type")
        )
        existing_ids = {int(ch.id) for ch in channels}
        missing = [int(i) for i in ids if i not in existing_ids]

        channel_id_strs = {str(ch.id) for ch in channels}
        policies = list(
            AlertPolicy.objects.filter(organization=org).only(
                "id", "name", "enabled", "notification_channel_ids"
            )
        )
        # Build reverse index: channel_id_str -> [policy, ...]
        channel_to_policies: dict[str, list] = {cid: [] for cid in channel_id_strs}
        for p in policies:
            for cid in (p.notification_channel_ids or []):
                if cid in channel_to_policies:
                    channel_to_policies[cid].append(p)

        items = []
        for ch in channels:
            cid = str(ch.id)
            refs = channel_to_policies.get(cid, [])
            items.append(
                {
                    "id": int(ch.id),
                    "name": ch.name,
                    "type": ch.channel_type,
                    "policies_count": len(refs),
                    "policies": [
                        {
                            "id": str(p.id),
                            "name": p.name,
                            "enabled": bool(p.enabled),
                        }
                        for p in refs
                    ],
                }
            )
        return Response({"items": items, "missing": missing})


    @action(detail=False, methods=["post"], url_path="bulk-state")
    def bulk_state(self, request):
        org = require_org(request)
        serializer = BulkChannelStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids: list[int] = list(serializer.validated_data["ids"])
        is_active: bool = serializer.validated_data["is_active"]
        result = bulk_set_channel_state(
            organization_id=org.id,
            ids=ids,
            is_active=is_active,
        )
        updated_ids: list[int] = list(result.get("updated") or [])
        if updated_ids:
            _write_bulk_state_audit(
                org=org,
                request=request,
                ids=updated_ids,
                is_active=is_active,
            )
        return Response(
            {
                "updated": updated_ids,
                "is_active": is_active,
                "failed": result.get("failed") or [],
            }
        )

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        org = require_org(request)
        serializer = BulkChannelDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids: list[int] = list(serializer.validated_data["ids"])
        result = bulk_delete_channels(
            organization_id=org.id,
            ids=ids,
        )
        deleted_ids: list[int] = list(result.get("deleted") or [])
        if deleted_ids:
            _write_bulk_delete_audit(
                org=org,
                request=request,
                ids=deleted_ids,
            )
        return Response(
            {
                "deleted": deleted_ids,
                "failed": result.get("failed") or [],
            }
        )


def _request_client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR") or ""


def _request_user_agent(request) -> str:
    return str(request.META.get("HTTP_USER_AGENT", "") or "")


def _write_bulk_state_audit(
    *,
    org: Organization,
    request,
    ids: list[int],
    is_active: bool,
) -> None:
    write_audit_log(
        organization=org,
        user=request.user,
        action="notification.channel.bulk_state",
        target_type="notification_channel",
        target_id=",".join(str(i) for i in ids[:50]),
        resource_type="notification_channel",
        resource_id=",".join(str(i) for i in ids[:50]),
        resource_name="",
        ip_address=_request_client_ip(request),
        user_agent=_request_user_agent(request),
        metadata={
            "ids": [int(i) for i in ids],
            "is_active": bool(is_active),
            "count": len(ids),
        },
    )


def _write_bulk_delete_audit(
    *,
    org: Organization,
    request,
    ids: list[int],
) -> None:
    write_audit_log(
        organization=org,
        user=request.user,
        action="notification.channel.bulk_delete",
        target_type="notification_channel",
        target_id=",".join(str(i) for i in ids[:50]),
        resource_type="notification_channel",
        resource_id=",".join(str(i) for i in ids[:50]),
        resource_name="",
        ip_address=_request_client_ip(request),
        user_agent=_request_user_agent(request),
        metadata={
            "ids": [int(i) for i in ids],
            "count": len(ids),
        },
    )
