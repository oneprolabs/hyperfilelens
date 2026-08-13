from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response

from apps.iam.permissions_org import IsOrgOperator, IsOrgReader, get_membership
from apps.task.constants import RESTORE_TASK_TYPES
from apps.task.api.serializers import (
    TaskCancelSerializer,
    TaskCreateSerializer,
    TaskEventSerializer,
    TaskRetrySerializer,
    TaskSerializer,
    TaskStepSerializer,
)
from apps.task.models import Task
from apps.task.selectors.interface import (
    get_task,
    list_task_events,
    list_task_steps,
    list_tasks,
    task_statistics,
)
from apps.task.services.interface import cancel_task, retry_task


def health(_request):
    return JsonResponse({"app": "task", "status": "ok"})


class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated, IsOrgOperator]
    lookup_field = "task_uuid"
    lookup_value_regex = "[0-9a-fA-F-]{36}"
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated(), IsOrgReader()]
        return super().get_permissions()

    def _membership(self):
        membership = get_membership(self.request)
        if membership is None:
            raise ValidationError({"org": "organization membership required"})
        return membership

    def _organization_id(self) -> int:
        return self._membership().organization_id

    def get_queryset(self):
        params = self.request.query_params
        search_field = (params.get("search_field") or "").strip().lower()
        if search_field not in {"", "name", "uuid"}:
            raise ValidationError(
                {"search_field": "search_field must be one of: name, uuid."}
            )
        resource_id = params.get("resource_id")
        try:
            resource_id_int = (
                int(resource_id) if resource_id not in (None, "") else None
            )
        except ValueError as exc:
            raise ValidationError(
                {"resource_id": "resource_id must be an integer."}
            ) from exc
        return list_tasks(
            organization_id=self._organization_id(),
            status=params.get("status") or None,
            task_type=params.get("task_type") or None,
            exclude_insight_workspace_restores=(
                (params.get("exclude_insight_workspace_restores") or "").strip().lower()
                == "true"
            ),
            trigger_type=params.get("trigger_type") or None,
            resource_type=params.get("resource_type") or None,
            resource_subtype=params.get("resource_subtype") or None,
            resource_id=resource_id_int,
            search=params.get("search") or None,
            search_field=search_field or None,
            created_after=parse_datetime(params.get("created_after") or ""),
            created_before=parse_datetime(params.get("created_before") or ""),
            finished_after=parse_datetime(params.get("finished_after") or ""),
            finished_before=parse_datetime(params.get("finished_before") or ""),
            terminal_only=(params.get("terminal_only") or "").strip().lower() == "true",
        )

    def get_object(self):
        task = get_task(
            organization_id=self._organization_id(),
            task_uuid=self.kwargs[self.lookup_field],
        )
        if task is None:
            raise NotFound("task not found")
        return task

    def get_serializer_class(self):
        if self.action == "create":
            return TaskCreateSerializer
        return TaskSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action == "create":
            context["organization_id"] = self._organization_id()
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = serializer.save()
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="statistics")
    def statistics(self, request):
        return Response(
            task_statistics(
                organization_id=self._organization_id(),
                queryset=self.get_queryset(),
            )
        )

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, task_uuid=None):
        serializer = TaskCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = self.get_object()
        task_type = task.task_type
        if task_type in RESTORE_TASK_TYPES:
            raise ValidationError(
                {"detail": "Restore tasks must be stopped from the restore workflow."}
            )
        if task_type == Task.Type.REPOSITORY_OPERATION:
            raise ValidationError(
                {"detail": "Repository tasks are managed by the server scheduler."}
            )
        if task_type == Task.Type.SOURCE_UNREGISTER:
            raise ValidationError(
                {
                    "detail": (
                        "Source deregistration tasks cannot be cancelled. "
                        "Resolve the prerequisite and submit a new request if cleanup did not start."
                    )
                }
            )
        try:
            task = cancel_task(
                task_uuid=task_uuid,
                organization_id=self._organization_id(),
                reason=serializer.validated_data.get("reason") or "",
            )
        except Task.DoesNotExist as exc:
            raise NotFound("task not found") from exc
        except DjangoValidationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(TaskSerializer(task).data)

    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, task_uuid=None):
        serializer = TaskRetrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        current_task = self.get_object()
        if current_task.task_type in RESTORE_TASK_TYPES:
            raise ValidationError(
                {
                    "detail": (
                        "Restore tasks must be restarted from their originating "
                        "restore workflow."
                    )
                }
            )
        if current_task.task_type == Task.Type.REPOSITORY_OPERATION:
            raise ValidationError(
                {"detail": "Repository tasks are retried by the server scheduler."}
            )
        if (
            current_task.task_type == Task.Type.SOURCE_UNREGISTER
            and current_task.error_code
            in {
                "SOURCE_UNREGISTER_PREFLIGHT_FAILED",
                "SOURCE_UNREGISTER_DEFERRED_CANCELLED",
                "SOURCE_UNREGISTER_INVALID_REQUEST",
                "TASK_CANCELLED",
            }
        ):
            raise ValidationError(
                {
                    "detail": (
                        "This deregistration did not start. Resolve the prerequisite "
                        "and submit a new request from the source deregistration dialog."
                    )
                }
            )
        try:
            if current_task.task_type == Task.Type.SOURCE_UNREGISTER:
                from apps.source.services.internal.backup_source_delete import (
                    retry_source_unregister_task,
                )

                task = retry_source_unregister_task(
                    task_uuid=task_uuid,
                    organization_id=self._organization_id(),
                    reason=serializer.validated_data.get("reason") or "",
                )
            else:
                task = retry_task(
                    task_uuid=task_uuid,
                    organization_id=self._organization_id(),
                    reason=serializer.validated_data.get("reason") or "",
                )
        except Task.DoesNotExist as exc:
            raise NotFound("task not found") from exc
        except DjangoValidationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(TaskSerializer(task).data)

    @action(detail=True, methods=["get"], url_path="steps")
    def steps(self, request, task_uuid=None):
        task = self.get_object()
        return Response(TaskStepSerializer(list_task_steps(task=task), many=True).data)

    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request, task_uuid=None):
        task = self.get_object()
        params = request.query_params
        after_seq = params.get("after_seq")
        try:
            after_seq_int = int(after_seq) if after_seq not in (None, "") else None
        except ValueError as exc:
            raise ValidationError(
                {"after_seq": "after_seq must be an integer."}
            ) from exc
        queryset = list_task_events(
            task=task,
            level=params.get("level") or None,
            after_seq=after_seq_int,
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            data = TaskEventSerializer(page, many=True).data
            return self.get_paginated_response(data)
        return Response(TaskEventSerializer(queryset, many=True).data)
