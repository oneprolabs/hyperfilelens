from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services.interface import write_audit_log
from apps.alert.services.defaults import (
    preview_restore_default_alert_rules,
    restore_default_alert_rules,
)
from common.deploy.site import platform_ops_api_allowed
from common.platform_authz import ALERTING_ALERT_RULES_MANAGE, has_platform_permission
from common.resource_scope import ResourceScope


class CanManagePlatformMonitoring(BasePermission):
    """Match Admin Console Alert Rules manage permission."""

    def has_permission(self, request, view) -> bool:
        return bool(
            platform_ops_api_allowed(request)
            and
            request.user
            and request.user.is_authenticated
            and has_platform_permission(request.user, ALERTING_ALERT_RULES_MANAGE)
        )


class PlatformDefaultAlertRulesPreviewView(APIView):
    """Preview create/restore actions for Admin Console default Alert Rules."""

    permission_classes = [IsAuthenticated, CanManagePlatformMonitoring]

    def get(self, request):
        return Response(
            preview_restore_default_alert_rules(owner_scope=ResourceScope.PLATFORM)
        )


class PlatformDefaultAlertRulesRestoreView(APIView):
    """Explicitly restore platform defaults from the Admin Console."""

    permission_classes = [IsAuthenticated, CanManagePlatformMonitoring]

    def post(self, request):
        result = restore_default_alert_rules(owner_scope=ResourceScope.PLATFORM)
        write_audit_log(
            organization=None,
            user=request.user,
            action="alert.policy.platform_defaults.restore",
            target_type="alert_policy_defaults",
            target_id=ResourceScope.PLATFORM,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=str(request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata=result,
        )
        return Response(result)
