from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable

from django.core.exceptions import ValidationError
from django.db import close_old_connections

from apps.node import agent_paths
from apps.node.models import NodeTask
from apps.node.services.interface import (
    cancel_agent_task,
    run_agent_task_async,
    wait_for_agent_task,
)
from apps.protection import conf as protection_conf
from apps.protection.services.backup_task import extract_kopia_failure_message
from apps.protection.services.repository_compatibility import (
    validate_backup_repository_compatible,
)
from apps.protection.services.source_execution import (
    ExecutionTarget,
    resolve_source_execution_target,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.nas_repository import nas_repository_payload
from apps.storage.services.internal.repository_access import (
    explicit_repository_server_host,
    repository_uses_bound_proxy,
    resolve_repository_reader,
)
from apps.storage.services.internal.repository_endpoints import repository_data_endpoint
from apps.storage.services.internal.repository_secrets import (
    SECRET_VALUE_KEYS,
    build_repository_runtime_payload,
    scrub_secrets,
)

logger = logging.getLogger(__name__)

TARGET_VALIDATION_TOTAL_SECONDS = 120
TARGET_VALIDATION_ACTIVE_SECONDS = 90
TARGET_VALIDATION_CLEANUP_SECONDS = 25
TARGET_VALIDATION_MAX_WORKERS = 4
TARGET_VALIDATION_AGENT_SECONDS = 60
TARGET_VALIDATION_SERVER_START_SECONDS = 25
TARGET_VALIDATION_CORRELATION_TYPE = "protection.target_validation"
S3_CLOCK_SKEW_CODE = "S3_CLOCK_SKEW"
S3_CLOCK_SKEW_MESSAGE = (
    "The source host clock differs too much from the S3 server, so the signed "
    "request was rejected. Synchronize the source host date, time, and time zone "
    "with a trusted NTP source, verify time synchronization, then retry validation."
)
_S3_CLOCK_SKEW_MARKERS = (
    "requesttimetooskewed",
    "the difference between the request time and the server's time is too large",
    "request time is too skewed",
)


@dataclass(frozen=True)
class TargetValidationInput:
    key: str
    source_type: str
    source_ref_id: int
    repository_id: int
    repository_endpoint_type: str = "external"


@dataclass(frozen=True)
class TargetValidationResult:
    status: str
    code: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _ResolvedAssignment:
    request: TargetValidationInput
    target: ExecutionTarget
    repository: Repository

    @property
    def route_key(self) -> tuple[int, int, str]:
        endpoint = ""
        if self.repository.repo_type == Repository.Type.S3:
            endpoint = repository_data_endpoint(
                self.repository.config,
                endpoint_type=self.request.repository_endpoint_type,
            )
        return (self.target.node.id, self.repository.id, endpoint)


@dataclass(frozen=True)
class _AgentOutcome:
    ok: bool
    status: str
    message: str
    result: dict[str, Any]
    timed_out: bool = False


@dataclass
class _ActivityRegistry:
    task_ids: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, task_id: str) -> None:
        with self.lock:
            self.task_ids.add(str(task_id))

    def cancel_all(self) -> None:
        with self.lock:
            task_ids = tuple(self.task_ids)
        for task_id in task_ids:
            try:
                cancel_agent_task(
                    task_id=task_id,
                    reason="backup target validation deadline reached",
                )
            except Exception as exc:
                logger.warning(
                    "failed to cancel backup target validation task "
                    "task_id=%s error_type=%s",
                    task_id,
                    type(exc).__name__,
                )


def validate_backup_targets(
    *,
    organization_id: int,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    started_at = time.monotonic()
    validation_deadline = started_at + TARGET_VALIDATION_ACTIVE_SECONDS
    cleanup_deadline = validation_deadline + TARGET_VALIDATION_CLEANUP_SECONDS
    request_id = str(uuid.uuid4())
    registry = _ActivityRegistry()
    inputs = [
        TargetValidationInput(
            key=str(item["key"]),
            source_type=str(item["source_type"]),
            source_ref_id=int(item["source_ref_id"]),
            repository_id=int(item["repository_id"]),
            repository_endpoint_type=str(
                item.get("repository_endpoint_type") or "external"
            ),
        )
        for item in sources
    ]
    results: dict[str, TargetValidationResult] = {}
    routes: dict[tuple[int, int, str], list[_ResolvedAssignment]] = {}

    for item in inputs:
        try:
            repository = validate_backup_repository_compatible(
                organization_id=organization_id,
                source_type=item.source_type,
                source_ref_id=item.source_ref_id,
                repository_id=item.repository_id,
            )
            if (
                repository.repo_type != Repository.Type.S3
                and item.repository_endpoint_type != "external"
            ):
                raise ValidationError(
                    {
                        "repository_endpoint_type": (
                            "Only object storage supports Endpoint selection."
                        )
                    }
                )
            target = resolve_source_execution_target(
                organization_id=organization_id,
                source_type=item.source_type,
                source_ref_id=item.source_ref_id,
            )
            assignment = _ResolvedAssignment(
                request=item,
                target=target,
                repository=repository,
            )
            routes.setdefault(assignment.route_key, []).append(assignment)
        except Exception as exc:
            results[item.key] = _validation_exception_result(exc)

    jobs: list[Callable[[], dict[tuple[int, int, str], TargetValidationResult]]] = []
    proxy_groups: dict[int, dict[tuple[int, int, str], list[_ResolvedAssignment]]] = {}
    for route_key, assignments in routes.items():
        repository = assignments[0].repository
        if repository_uses_bound_proxy(repository):
            proxy_groups.setdefault(repository.id, {})[route_key] = assignments
            continue
        jobs.append(
            _route_job(
                route_key=route_key,
                assignment=assignments[0],
                request_id=request_id,
                organization_id=organization_id,
                registry=registry,
                validation_deadline=validation_deadline,
                cleanup_deadline=cleanup_deadline,
            )
        )
    for grouped_routes in proxy_groups.values():
        jobs.append(
            _proxy_group_job(
                routes=grouped_routes,
                request_id=request_id,
                organization_id=organization_id,
                registry=registry,
                validation_deadline=validation_deadline,
                cleanup_deadline=cleanup_deadline,
            )
        )

    route_results: dict[tuple[int, int, str], TargetValidationResult] = {}
    executor = ThreadPoolExecutor(
        max_workers=TARGET_VALIDATION_MAX_WORKERS,
        thread_name_prefix="target-validation",
    )
    futures: list[Future[dict[tuple[int, int, str], TargetValidationResult]]] = []
    try:
        futures = [executor.submit(_run_thread_job, job) for job in jobs]
        remaining = max(0.0, cleanup_deadline - time.monotonic())
        done, not_done = wait(futures, timeout=remaining)
        if not_done:
            registry.cancel_all()
        for future in done:
            try:
                route_results.update(future.result())
            except Exception as exc:
                logger.warning(
                    "backup target validation worker failed "
                    "request_id=%s error_type=%s",
                    request_id,
                    type(exc).__name__,
                )
        for future in not_done:
            future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    timeout_result = TargetValidationResult(
        status="failed",
        code="VALIDATION_TIMEOUT",
        message="Backup target validation timed out. Try again.",
    )
    for route_key, assignments in routes.items():
        route_result = route_results.get(route_key, timeout_result)
        for assignment in assignments:
            results[assignment.request.key] = route_result

    ordered_results = [
        {
            "key": item.key,
            "status": results[item.key].status,
            "code": results[item.key].code,
            "message": results[item.key].message,
            "details": results[item.key].details,
        }
        for item in inputs
    ]
    overall = (
        "success"
        if all(item["status"] == "success" for item in ordered_results)
        else "failed"
    )
    logger.info(
        "backup target validation finished request_id=%s org_id=%s status=%s "
        "sources=%s routes=%s elapsed_ms=%s",
        request_id,
        organization_id,
        overall,
        len(inputs),
        len(routes),
        int((time.monotonic() - started_at) * 1000),
    )
    return {"status": overall, "results": ordered_results}


def _route_job(
    *,
    route_key: tuple[int, int, str],
    assignment: _ResolvedAssignment,
    request_id: str,
    organization_id: int,
    registry: _ActivityRegistry,
    validation_deadline: float,
    cleanup_deadline: float,
) -> Callable[[], dict[tuple[int, int, str], TargetValidationResult]]:
    def run() -> dict[tuple[int, int, str], TargetValidationResult]:
        if assignment.repository.repo_type == Repository.Type.S3:
            result = _validate_s3_route(
                assignment=assignment,
                request_id=request_id,
                organization_id=organization_id,
                registry=registry,
                validation_deadline=validation_deadline,
            )
        elif assignment.repository.repo_type == Repository.Type.NAS:
            result = _validate_direct_nas_route(
                assignment=assignment,
                request_id=request_id,
                organization_id=organization_id,
                registry=registry,
                validation_deadline=validation_deadline,
                cleanup_deadline=cleanup_deadline,
            )
        else:
            result = TargetValidationResult(
                status="failed",
                code="REPOSITORY_INCOMPATIBLE",
                message="The selected repository is not compatible with this source.",
            )
        return {route_key: result}

    return run


def _proxy_group_job(
    *,
    routes: dict[tuple[int, int, str], list[_ResolvedAssignment]],
    request_id: str,
    organization_id: int,
    registry: _ActivityRegistry,
    validation_deadline: float,
    cleanup_deadline: float,
) -> Callable[[], dict[tuple[int, int, str], TargetValidationResult]]:
    def run() -> dict[tuple[int, int, str], TargetValidationResult]:
        return _validate_proxy_repository_group(
            routes=routes,
            request_id=request_id,
            organization_id=organization_id,
            registry=registry,
            validation_deadline=validation_deadline,
            cleanup_deadline=cleanup_deadline,
        )

    return run


def _run_thread_job(
    job: Callable[[], dict[tuple[int, int, str], TargetValidationResult]],
) -> dict[tuple[int, int, str], TargetValidationResult]:
    close_old_connections()
    try:
        return job()
    finally:
        close_old_connections()


def _validate_s3_route(
    *,
    assignment: _ResolvedAssignment,
    request_id: str,
    organization_id: int,
    registry: _ActivityRegistry,
    validation_deadline: float,
) -> TargetValidationResult:
    try:
        repository_payload = build_repository_runtime_payload(
            repository=assignment.repository,
            execution_target=assignment.target,
            repository_endpoint_type=assignment.request.repository_endpoint_type,
        )
        outcome = _execute_agent_task(
            organization_id=organization_id,
            node_id=assignment.target.node.id,
            kind="repo.status",
            payload={
                "repository": repository_payload,
                "probe": "backup_target_validation",
                "health_only": True,
            },
            request_id=request_id,
            registry=registry,
            deadline=validation_deadline,
            max_wait_seconds=TARGET_VALIDATION_AGENT_SECONDS,
        )
        return _s3_outcome_result(
            outcome,
            repository=assignment.repository,
        )
    except Exception as exc:
        return _validation_exception_result(exc, repository=assignment.repository)


def _validate_direct_nas_route(
    *,
    assignment: _ResolvedAssignment,
    request_id: str,
    organization_id: int,
    registry: _ActivityRegistry,
    validation_deadline: float,
    cleanup_deadline: float,
) -> TargetValidationResult:
    repository = assignment.repository
    mount_point = agent_paths.validation_mount_point(
        request_id,
        repository.id,
        assignment.target.node.id,
    )
    validation_result: TargetValidationResult
    try:
        payload = nas_repository_payload(
            repository=repository,
            subdir="",
            node_id=assignment.target.node.id,
        )
        nas_payload = dict(payload.get("nas") or {})
        nas_payload["mount_point"] = mount_point
        task_payload = {
            "nas": nas_payload,
            **nas_payload,
            "cleanup_after_test": True,
        }
        outcome = _execute_agent_task(
            organization_id=organization_id,
            node_id=assignment.target.node.id,
            kind="nas.test",
            payload=task_payload,
            request_id=request_id,
            registry=registry,
            deadline=validation_deadline,
            max_wait_seconds=TARGET_VALIDATION_AGENT_SECONDS,
        )
        validation_result = _outcome_result(
            outcome,
            failure_code="NAS_MOUNT_FAILED",
            repository=repository,
        )
    except Exception as exc:
        validation_result = _validation_exception_result(exc, repository=repository)

    cleanup = _execute_agent_task(
        organization_id=organization_id,
        node_id=assignment.target.node.id,
        kind="nas.unmount",
        payload={"mount_point": mount_point},
        request_id=request_id,
        registry=registry,
        deadline=cleanup_deadline,
        max_wait_seconds=TARGET_VALIDATION_CLEANUP_SECONDS,
    )
    if cleanup.ok:
        return validation_result
    return _merge_cleanup_failure(
        validation_result,
        cleanup,
        repository=repository,
        resource_label="NAS validation mount",
    )


def _validate_proxy_repository_group(
    *,
    routes: dict[tuple[int, int, str], list[_ResolvedAssignment]],
    request_id: str,
    organization_id: int,
    registry: _ActivityRegistry,
    validation_deadline: float,
    cleanup_deadline: float,
) -> dict[tuple[int, int, str], TargetValidationResult]:
    assignments = [items[0] for items in routes.values()]
    repository = assignments[0].repository
    results: dict[tuple[int, int, str], TargetValidationResult] = {}
    try:
        repository_access = resolve_repository_reader(
            repository=repository,
            fallback_node=assignments[0].target.node,
            source_type=assignments[0].target.source_type,
            source_ref_id=assignments[0].target.source_ref_id,
        )
    except Exception as exc:
        failure = _validation_exception_result(exc, repository=repository)
        return {route_key: failure for route_key in routes}

    direct = [
        assignment
        for assignment in assignments
        if assignment.target.node.id == repository_access.node.id
    ]
    cross = [
        assignment
        for assignment in assignments
        if assignment.target.node.id != repository_access.node.id
    ]
    for assignment in direct:
        route_key = assignment.route_key
        try:
            outcome = _execute_agent_task(
                organization_id=organization_id,
                node_id=assignment.target.node.id,
                kind="repo.status",
                payload={
                    "repository": repository_access.repository_payload,
                    "probe": "backup_target_validation",
                    "health_only": True,
                },
                request_id=request_id,
                registry=registry,
                deadline=validation_deadline,
                max_wait_seconds=TARGET_VALIDATION_AGENT_SECONDS,
            )
            results[route_key] = _outcome_result(
                outcome,
                failure_code="TARGET_CONNECTION_FAILED",
                repository=repository,
            )
        except Exception as exc:
            results[route_key] = _validation_exception_result(
                exc,
                repository=repository,
            )

    if not cross:
        return results
    if not protection_conf.PROTECTION_PROXY_REPOSITORY_SERVER_ENABLED:
        failure = TargetValidationResult(
            status="failed",
            code="REPOSITORY_INCOMPATIBLE",
            message="Cross-node repository access is disabled.",
        )
        results.update({assignment.route_key: failure for assignment in cross})
        return results

    host, _host_source = explicit_repository_server_host(
        repository=repository,
        node=repository_access.node,
    )
    if not host:
        failure = TargetValidationResult(
            status="failed",
            code="PROXY_REPOSITORY_SERVER_ADDRESS_MISSING",
            message=(
                "The Proxy Host has no source-reachable Repository Server Address. "
                "Edit the Proxy Host and configure an address that backup sources can reach."
            ),
            details={
                "stage": "address_resolution",
                "proxy_name": repository_access.node.name,
                "proxy_address": "",
                "address_source": "unavailable",
                "port_range": "51515-52014",
            },
        )
        results.update({assignment.route_key: failure for assignment in cross})
        return results

    session_id = f"target-validation-{request_id}-repo-{repository.id}"
    username = f"hfl-validation-{request_id[:12]}@proxy-{repository_access.node.id}"
    password = secrets.token_urlsafe(24)
    server_dispatched = False
    server_result: TargetValidationResult | None = None
    try:
        server_dispatched = True
        start_outcome = _execute_agent_task(
            organization_id=organization_id,
            node_id=repository_access.node.id,
            kind="repository.server.start",
            payload={
                "session_id": session_id,
                "username": username,
                "password": password,
                "public_host": host,
                "public_host_source": _host_source,
                "repository": repository_access.repository_payload,
            },
            request_id=request_id,
            registry=registry,
            deadline=validation_deadline,
            max_wait_seconds=TARGET_VALIDATION_SERVER_START_SECONDS,
        )
        if not start_outcome.ok:
            port_exhausted = "no available repository server port" in str(
                start_outcome.message or ""
            ).lower()
            server_result = TargetValidationResult(
                status="failed",
                code=(
                    "PROXY_REPOSITORY_SERVER_PORT_EXHAUSTED"
                    if port_exhausted
                    else "PROXY_REPOSITORY_SERVER_START_FAILED"
                ),
                message=_sanitize_message(
                    start_outcome.message
                    or "The temporary Repository Server could not start on the Proxy Host.",
                    repository=repository,
                ),
                details={
                    "stage": "server_start",
                    "proxy_name": repository_access.node.name,
                    "proxy_address": host,
                    "address_source": _host_source,
                    "port_range": "51515-52014",
                },
            )
            results.update(
                {assignment.route_key: server_result for assignment in cross}
            )
            return results

        server_payload = {
            "id": repository.id,
            "type": "kopia_server",
            "url": str(
                start_outcome.result.get("server_url")
                or start_outcome.result.get("url")
                or ""
            ).strip(),
            "username": username,
            "password": password,
            "server_cert_fingerprint": str(
                start_outcome.result.get("server_cert_fingerprint") or ""
            ).strip(),
            "kopia_password": str(
                repository_access.repository_payload.get("kopia_password") or ""
            ).strip(),
            "session_id": session_id,
        }
        if not server_payload["url"] or not server_payload["server_cert_fingerprint"]:
            failure = TargetValidationResult(
                status="failed",
                code="PROXY_REPOSITORY_SERVER_START_FAILED",
                message="The repository Proxy returned incomplete server connection information.",
                details={
                    "stage": "server_start",
                    "proxy_name": repository_access.node.name,
                    "proxy_address": host,
                    "address_source": _host_source,
                    "port_range": "51515-52014",
                },
            )
            results.update({assignment.route_key: failure for assignment in cross})
            return results

        for assignment in cross:
            try:
                probe = _execute_agent_task(
                    organization_id=organization_id,
                    node_id=assignment.target.node.id,
                    kind="repo.status",
                    payload={
                        "repository": server_payload,
                        "probe": "backup_target_validation",
                        "health_only": True,
                    },
                    request_id=request_id,
                    registry=registry,
                    deadline=validation_deadline,
                    max_wait_seconds=TARGET_VALIDATION_AGENT_SECONDS,
                )
                if probe.ok:
                    results[assignment.route_key] = TargetValidationResult(
                        status="success"
                    )
                else:
                    network_failure = _is_proxy_repository_network_failure(
                        probe.message
                    )
                    results[assignment.route_key] = TargetValidationResult(
                        status="failed",
                        code=(
                            "PROXY_REPOSITORY_SERVER_UNREACHABLE"
                            if network_failure
                            else "PROXY_REPOSITORY_SERVER_CONNECTION_FAILED"
                        ),
                        message=_sanitize_message(
                            probe.message
                            or "The backup source could not connect to the Proxy Repository Server.",
                            repository=repository,
                        ),
                        details={
                            "stage": "source_probe",
                            "source_name": assignment.target.node.name,
                            "source_address": str(
                                assignment.target.node.ip_address or ""
                            ),
                            "proxy_name": repository_access.node.name,
                            "proxy_address": host,
                            "endpoint": server_payload["url"],
                            "address_source": _host_source,
                            "port_range": "51515-52014",
                        },
                    )
            except Exception as exc:
                results[assignment.route_key] = _validation_exception_result(
                    exc,
                    repository=repository,
                )
    except Exception as exc:
        failure = _validation_exception_result(exc, repository=repository)
        for assignment in cross:
            results.setdefault(assignment.route_key, failure)
    finally:
        if server_dispatched:
            stop_outcome = _execute_agent_task(
                organization_id=organization_id,
                node_id=repository_access.node.id,
                kind="repository.server.stop",
                payload={"session_id": session_id},
                request_id=request_id,
                registry=registry,
                deadline=cleanup_deadline,
                max_wait_seconds=TARGET_VALIDATION_CLEANUP_SECONDS,
            )
            if not stop_outcome.ok:
                for assignment in cross:
                    current = results.get(
                        assignment.route_key,
                        server_result
                        or TargetValidationResult(
                            status="failed",
                            code="TARGET_CONNECTION_FAILED",
                            message="Repository validation did not complete.",
                        ),
                    )
                    results[assignment.route_key] = _merge_cleanup_failure(
                        current,
                        stop_outcome,
                        repository=repository,
                        resource_label="temporary Kopia server",
                    )
    return results


def _is_proxy_repository_network_failure(message: object) -> bool:
    lower_message = str(message or "").lower()
    return any(
        token in lower_message
        for token in (
            "connection refused",
            "actively refused",
            "connection reset",
            "connection timed out",
            "context deadline exceeded",
            "i/o timeout",
            "no route to host",
            "network is unreachable",
            "name resolution",
            "no such host",
            "temporary failure in name resolution",
            "timeout awaiting response headers",
            "dial tcp",
            "connectex",
        )
    )


def _execute_agent_task(
    *,
    organization_id: int,
    node_id: int,
    kind: str,
    payload: dict[str, Any],
    request_id: str,
    registry: _ActivityRegistry,
    deadline: float,
    max_wait_seconds: int,
) -> _AgentOutcome:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _AgentOutcome(
            ok=False,
            status="timeout",
            message="Backup target validation timed out.",
            result={},
            timed_out=True,
        )
    secret_values = _secret_values(payload)
    handle = None
    try:
        handle = run_agent_task_async(
            organization_id=organization_id,
            node_id=node_id,
            kind=kind,
            payload=payload,
            persisted_payload=scrub_secrets(payload, extra_values=secret_values),
            correlation_type=TARGET_VALIDATION_CORRELATION_TYPE,
            correlation_id=request_id,
        )
        registry.add(handle.task_id)
        timeout_seconds = max(1, min(int(remaining), int(max_wait_seconds)))
        outcome = wait_for_agent_task(
            task_id=handle.task_id,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        if handle is not None:
            _cancel_agent_task_safely(
                task_id=handle.task_id,
                reason="backup target validation operation failed",
            )
        logger.warning(
            "backup target validation Agent operation failed "
            "request_id=%s node_id=%s kind=%s error_type=%s",
            request_id,
            node_id,
            kind,
            type(exc).__name__,
        )
        return _AgentOutcome(
            ok=False,
            status="failed",
            message="Unable to run the backup target validation task.",
            result={},
        )
    raw_result = outcome.task.result
    result = dict(raw_result) if isinstance(raw_result, dict) else {}
    message = "" if outcome.ok else _agent_failure_message(outcome.task, result)
    try:
        _scrub_node_task(
            task=outcome.task,
            result=result,
            message=message,
            secret_values=secret_values,
        )
    except Exception as exc:
        logger.warning(
            "failed to scrub backup target validation task "
            "request_id=%s task_id=%s error_type=%s",
            request_id,
            handle.task_id,
            type(exc).__name__,
        )
        return _AgentOutcome(
            ok=False,
            status="failed",
            message="Unable to finalize the backup target validation task safely.",
            result={},
        )
    if outcome.timed_out:
        _cancel_agent_task_safely(
            task_id=handle.task_id,
            reason="backup target validation operation timed out",
        )
        return _AgentOutcome(
            ok=False,
            status="timeout",
            message="Backup target validation timed out.",
            result=scrub_secrets(result, extra_values=secret_values),
            timed_out=True,
        )
    return _AgentOutcome(
        ok=outcome.ok,
        status=str(outcome.task.status),
        message=str(scrub_secrets(message, extra_values=secret_values) or "")[:1000],
        result=scrub_secrets(result, extra_values=secret_values),
    )


def _cancel_agent_task_safely(*, task_id: str, reason: str) -> None:
    try:
        cancel_agent_task(task_id=task_id, reason=reason)
    except Exception as exc:
        logger.warning(
            "failed to cancel backup target validation task "
            "task_id=%s error_type=%s",
            task_id,
            type(exc).__name__,
        )


def _scrub_node_task(
    *,
    task: NodeTask,
    result: dict[str, Any],
    message: str,
    secret_values: list[str],
) -> None:
    safe_result = scrub_secrets(result, extra_values=secret_values)
    safe_error = str(scrub_secrets(message, extra_values=secret_values) or "")[:2000]
    update_fields: dict[str, Any] = {}
    if safe_result != task.result:
        update_fields["result"] = safe_result
    if safe_error != str(task.last_error or ""):
        update_fields["last_error"] = safe_error
    if update_fields:
        NodeTask.objects.filter(pk=task.pk).update(**update_fields)


def _secret_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in SECRET_VALUE_KEYS and item:
                values.append(str(item))
            else:
                values.extend(_secret_values(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.extend(_secret_values(item))
    return values


def _agent_failure_message(task: NodeTask, result: dict[str, Any]) -> str:
    message = str(task.last_error or "").strip()
    extracted = extract_kopia_failure_message(result, last_error=message)
    if extracted:
        return extracted
    for key in ("error", "message", "detail"):
        value = str(result.get(key) or "").strip()
        if value:
            return value
    return message or f"Agent task failed with status {task.status}."


def _outcome_result(
    outcome: _AgentOutcome,
    *,
    failure_code: str,
    repository: Repository,
) -> TargetValidationResult:
    if outcome.ok:
        return TargetValidationResult(status="success")
    if outcome.timed_out:
        return TargetValidationResult(
            status="failed",
            code="VALIDATION_TIMEOUT",
            message="Backup target validation timed out. Try again.",
        )
    return TargetValidationResult(
        status="failed",
        code=failure_code,
        message=_sanitize_message(
            outcome.message or "Backup target connection failed.",
            repository=repository,
        ),
    )


def _s3_outcome_result(
    outcome: _AgentOutcome,
    *,
    repository: Repository,
) -> TargetValidationResult:
    if not outcome.ok and not outcome.timed_out and _is_s3_clock_skew_failure(outcome):
        return TargetValidationResult(
            status="failed",
            code=S3_CLOCK_SKEW_CODE,
            message=S3_CLOCK_SKEW_MESSAGE,
            details={
                "stage": "repository_connect",
                "remediation": "synchronize_source_time",
            },
        )
    return _outcome_result(
        outcome,
        failure_code="S3_CONNECTION_FAILED",
        repository=repository,
    )


def _is_s3_clock_skew_failure(outcome: _AgentOutcome) -> bool:
    candidates = [outcome.message]
    repository_connect = outcome.result.get("repository_connect")
    if isinstance(repository_connect, dict):
        candidates.extend(
            repository_connect.get(key)
            for key in ("stderr", "stderr_tail", "stdout", "stdout_tail")
        )
    candidates.extend(
        outcome.result.get(key)
        for key in ("stderr", "stderr_tail", "stdout", "stdout_tail")
    )
    return any(
        marker in str(candidate or "").lower()
        for candidate in candidates
        for marker in _S3_CLOCK_SKEW_MARKERS
    )


def _merge_cleanup_failure(
    current: TargetValidationResult,
    cleanup: _AgentOutcome,
    *,
    repository: Repository,
    resource_label: str,
) -> TargetValidationResult:
    cleanup_message = _sanitize_message(
        cleanup.message or f"Failed to clean up the {resource_label}.",
        repository=repository,
    )
    if current.status == "success":
        return TargetValidationResult(
            status="failed",
            code="CLEANUP_FAILED",
            message=f"Connection succeeded, but cleanup failed: {cleanup_message}"[:1000],
        )
    message = current.message or "Backup target connection failed."
    return TargetValidationResult(
        status="failed",
        code=current.code or "TARGET_CONNECTION_FAILED",
        message=f"{message} Cleanup also failed: {cleanup_message}"[:1000],
        details=current.details,
    )


def _validation_exception_result(
    exc: Exception,
    *,
    repository: Repository | None = None,
) -> TargetValidationResult:
    if isinstance(exc, (ValidationError, ValueError)):
        message = _exception_message(exc)
    else:
        logger.warning(
            "unexpected backup target validation failure error_type=%s",
            type(exc).__name__,
        )
        message = "Backup target validation failed unexpectedly. Try again."
    lowered = message.lower()
    if "source" in lowered and "not found" in lowered:
        code = "SOURCE_NOT_FOUND"
    elif "offline" in lowered or "not online" in lowered:
        code = "SOURCE_NODE_OFFLINE"
    elif "not bound" in lowered:
        code = "SOURCE_PROXY_NOT_BOUND"
    elif "repository" in lowered and "not found" in lowered:
        code = "REPOSITORY_NOT_FOUND"
    elif "endpoint" in lowered or "compatible" in lowered:
        code = "REPOSITORY_INCOMPATIBLE"
    else:
        code = "TARGET_CONNECTION_FAILED"
    return TargetValidationResult(
        status="failed",
        code=code,
        message=_sanitize_message(message, repository=repository),
    )


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            messages = [
                str(message)
                for values in exc.message_dict.values()
                for message in (values if isinstance(values, list) else [values])
            ]
            if messages:
                return " ".join(messages)
        if getattr(exc, "messages", None):
            return " ".join(str(message) for message in exc.messages)
    return str(exc or "Backup target validation failed.").strip()


def _sanitize_message(
    message: str,
    *,
    repository: Repository | None = None,
) -> str:
    extra_values = _secret_values(repository.config) if repository is not None else []
    safe = str(scrub_secrets(str(message or ""), extra_values=extra_values) or "").strip()
    return (safe or "Backup target validation failed.")[:1000]


__all__ = [
    "TARGET_VALIDATION_ACTIVE_SECONDS",
    "TARGET_VALIDATION_MAX_WORKERS",
    "TARGET_VALIDATION_TOTAL_SECONDS",
    "validate_backup_targets",
]
