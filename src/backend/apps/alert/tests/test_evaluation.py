"""Alert policy evaluation tests."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alert.constants import AlertType, ResourceType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.evaluation import evaluate_organization_policies
from apps.iam.models import Organization
from apps.monitor.models import DeploymentHost, ResourceMetric, SystemMetric
from apps.monitor.services.internal.resource_metrics import record_resource_metric
from apps.node.models import Node
from apps.node.models.base import NodeRole


@pytest.mark.django_db
def test_metric_policy_fires_on_high_cpu(db):
    org = Organization.objects.create(key="eval-org", name="Eval Org")
    policy = AlertPolicy.objects.create(
        organization=org,
        name="CPU",
        type=AlertType.METRIC,
        severity="warning",
        enabled=True,
        resource_type=ResourceType.SYNC_PROXY,
        scope="selected",
        resource_ids=["1"],
        trigger_rule={
            "metric_key": "cpu_usage",
            "operator": ">",
            "threshold": 80,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 60,
        },
    )
    record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.SYNC_PROXY,
        resource_id="1",
        metrics={"cpu_usage": 95},
        resource_name="proxy-1",
    )
    result = evaluate_organization_policies(organization_id=org.id)
    assert result["metric"] == 1
    assert AlertRecord.objects.filter(organization=org, policy_id=policy.id).exists()


@pytest.mark.django_db
def test_source_host_memory_policy_uses_agent_resource_metrics(db):
    org = Organization.objects.create(key="source-host-memory", name="Source Host Memory")
    node = Node.objects.create(
        organization=org,
        name="DESKTOP-LPMO3SJ",
        role=NodeRole.AGENT,
    )
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Source host memory",
        type=AlertType.METRIC,
        severity="critical",
        enabled=True,
        resource_type=ResourceType.AGENT_PROXY,
        scope="selected",
        resource_ids=[str(node.id)],
        trigger_rule={
            "metric_key": "memory_usage",
            "operator": ">=",
            "threshold": 39,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 60,
        },
    )
    record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.AGENT_PROXY,
        resource_id=str(node.id),
        metrics={"memory_usage": 40},
        resource_name=node.name,
    )

    evaluate_organization_policies(organization_id=org.id)

    alert = AlertRecord.objects.get(organization=org, policy_id=policy.id)
    assert alert.resource_type == ResourceType.AGENT_PROXY
    assert alert.resource_id == str(node.id)
    assert alert.resource_name == node.name


@pytest.mark.django_db
def test_control_plane_policy_does_not_read_source_host_metrics(db):
    org = Organization.objects.create(key="control-plane-memory", name="Control Plane Memory")
    node = Node.objects.create(
        organization=org,
        name="DESKTOP-LPMO3SJ",
        role=NodeRole.AGENT,
    )
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Control plane memory",
        type=AlertType.METRIC,
        severity="critical",
        enabled=True,
        resource_type=ResourceType.SYSTEM,
        scope="all",
        trigger_rule={
            "metric_key": "memory_usage",
            "operator": ">=",
            "threshold": 39,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 60,
        },
    )
    record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.AGENT_PROXY,
        resource_id=str(node.id),
        metrics={"memory_usage": 40},
        resource_name=node.name,
    )

    evaluate_organization_policies(organization_id=org.id)

    assert not AlertRecord.objects.filter(organization=org, policy_id=policy.id).exists()


@pytest.mark.django_db
def test_availability_policy_fires_on_stale_node(db):
    org = Organization.objects.create(key="eval-org-2", name="Eval Org 2")
    node = Node.objects.create(
        organization=org,
        name="stale-node",
        role=NodeRole.PROXY,
        status=Node.Status.ACTIVE, availability=Node.Availability.OFFLINE,
        last_seen_at=timezone.now() - timedelta(hours=2),
    )
    AlertPolicy.objects.create(
        organization=org,
        name="Heartbeat",
        type=AlertType.AVAILABILITY,
        severity="critical",
        enabled=True,
        resource_type=ResourceType.SYNC_PROXY,
        scope="selected",
        resource_ids=[str(node.id)],
        trigger_rule={"check_type": "heartbeat", "timeout_seconds": 300},
    )
    evaluate_organization_policies(organization_id=org.id)
    assert AlertRecord.objects.filter(organization=org, resource_id=str(node.id)).exists()


@pytest.mark.django_db
def test_duration_uses_consecutive_fresh_samples_at_collector_cadence(db):
    org = Organization.objects.create(key="duration-org", name="Duration Org")
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Memory",
        type=AlertType.METRIC,
        severity="warning",
        resource_type=ResourceType.AGENT_PROXY,
        scope="selected",
        resource_ids=["14"],
        trigger_rule={
            "metric_key": "memory_usage",
            "operator": ">=",
            "threshold": 6,
            "duration_seconds": 60,
            "evaluation_interval_seconds": 60,
        },
    )
    older = record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.AGENT_PROXY,
        resource_id="14",
        resource_name="hfl-source-test-whx-1",
        metrics={"memory_usage": 14},
    )
    latest = record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.AGENT_PROXY,
        resource_id="14",
        resource_name="hfl-source-test-whx-1",
        metrics={"memory_usage": 15},
    )
    now = timezone.now()
    ResourceMetric.objects.filter(pk=older.pk).update(timestamp=now - timedelta(minutes=7))
    ResourceMetric.objects.filter(pk=latest.pk).update(timestamp=now - timedelta(minutes=2))

    evaluate_organization_policies(organization_id=org.id)

    alert = AlertRecord.objects.get(policy_id=policy.id)
    assert alert.resource_name == "hfl-source-test-whx-1"


@pytest.mark.django_db
def test_stale_metric_neither_fires_nor_resolves(db):
    org = Organization.objects.create(key="stale-org", name="Stale Org")
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Stale memory",
        type=AlertType.METRIC,
        severity="warning",
        resource_type=ResourceType.AGENT_PROXY,
        scope="selected",
        resource_ids=["14"],
        trigger_rule={
            "metric_key": "memory_usage",
            "operator": ">=",
            "threshold": 50,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 60,
        },
    )
    sample = record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.AGENT_PROXY,
        resource_id="14",
        metrics={"memory_usage": 10},
    )
    ResourceMetric.objects.filter(pk=sample.pk).update(
        timestamp=timezone.now() - timedelta(minutes=11)
    )
    existing = AlertRecord.objects.create(
        organization=org,
        policy_id=policy.id,
        type=AlertType.METRIC,
        severity="warning",
        status="firing",
        resource_type=ResourceType.AGENT_PROXY,
        resource_id="14",
        title="existing",
        fingerprint=f"{policy.id}|{org.id}|14|memory_usage",
    )

    evaluate_organization_policies(organization_id=org.id)

    existing.refresh_from_db()
    assert existing.status == "firing"


@pytest.mark.django_db
def test_recovery_requires_configured_threshold_and_duration(db):
    org = Organization.objects.create(key="recovery-org", name="Recovery Org")
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Recover memory",
        type=AlertType.METRIC,
        severity="warning",
        resource_type=ResourceType.AGENT_PROXY,
        scope="selected",
        resource_ids=["14"],
        trigger_rule={
            "metric_key": "memory_usage",
            "operator": ">=",
            "threshold": 80,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 60,
        },
        recovery_rule={
            "enabled": True,
            "operator": "<",
            "threshold": 70,
            "duration_seconds": 180,
        },
    )
    alert = AlertRecord.objects.create(
        organization=org,
        policy_id=policy.id,
        type=AlertType.METRIC,
        severity="warning",
        status="firing",
        resource_type=ResourceType.AGENT_PROXY,
        resource_id="14",
        title="existing",
        fingerprint=f"{policy.id}|{org.id}|14|memory_usage",
    )
    older = record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.AGENT_PROXY,
        resource_id="14",
        metrics={"memory_usage": 65},
    )
    latest = record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.AGENT_PROXY,
        resource_id="14",
        metrics={"memory_usage": 60},
    )
    now = timezone.now()
    ResourceMetric.objects.filter(pk=older.pk).update(timestamp=now - timedelta(minutes=2))
    ResourceMetric.objects.filter(pk=latest.pk).update(timestamp=now - timedelta(minutes=1))

    evaluate_organization_policies(organization_id=org.id)

    alert.refresh_from_db()
    assert alert.status == "firing"

    ResourceMetric.objects.filter(pk=older.pk).update(timestamp=now - timedelta(minutes=5))
    AlertPolicy.objects.filter(pk=policy.pk).update(last_evaluated_at=None)
    evaluate_organization_policies(organization_id=org.id)

    alert.refresh_from_db()
    assert alert.status == "resolved"


@pytest.mark.django_db
def test_policy_evaluation_interval_is_enforced(db):
    org = Organization.objects.create(key="interval-org", name="Interval Org")
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Interval CPU",
        type=AlertType.METRIC,
        severity="warning",
        resource_type=ResourceType.SYNC_PROXY,
        scope="selected",
        resource_ids=["1"],
        trigger_rule={
            "metric_key": "cpu_usage",
            "operator": ">",
            "threshold": 80,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 300,
        },
    )
    record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.SYNC_PROXY,
        resource_id="1",
        metrics={"cpu_usage": 10},
    )
    evaluate_organization_policies(organization_id=org.id)
    record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.SYNC_PROXY,
        resource_id="1",
        metrics={"cpu_usage": 95},
    )

    result = evaluate_organization_policies(organization_id=org.id)

    assert result["skipped"] == 1
    assert not AlertRecord.objects.filter(policy_id=policy.id).exists()


@pytest.mark.django_db
def test_legacy_metric_policy_uses_default_evaluation_interval(db):
    org = Organization.objects.create(key="legacy-interval-org", name="Legacy Interval Org")
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Legacy CPU",
        type=AlertType.METRIC,
        severity="warning",
        resource_type=ResourceType.SYNC_PROXY,
        scope="selected",
        resource_ids=["1"],
        trigger_rule={
            "metric_key": "cpu_usage",
            "operator": ">",
            "threshold": 80,
            "duration_seconds": 0,
        },
    )
    record_resource_metric(
        organization_id=org.id,
        resource_type=ResourceType.SYNC_PROXY,
        resource_id="1",
        metrics={"cpu_usage": 95},
    )

    result = evaluate_organization_policies(organization_id=org.id)

    assert result["metric"] == 1
    assert AlertRecord.objects.filter(policy_id=policy.id).exists()


@pytest.mark.django_db
def test_control_plane_metrics_are_shared_but_alerts_are_tenant_scoped(db):
    first_org = Organization.objects.create(key="system-one", name="System One")
    second_org = Organization.objects.create(key="system-two", name="System Two")
    for org in (first_org, second_org):
        AlertPolicy.objects.create(
            organization=org,
            name="Control Plane CPU",
            type=AlertType.METRIC,
            severity="warning",
            resource_type=ResourceType.SYSTEM,
            scope="all",
            trigger_rule={
                "metric_key": "cpu_usage",
                "operator": ">",
                "threshold": 80,
                "duration_seconds": 0,
                "evaluation_interval_seconds": 60,
            },
        )
    host = DeploymentHost.objects.create(hostname="control-plane-1")
    SystemMetric.objects.create(host=host, cpu={"usage_percent": 95})

    evaluate_organization_policies()

    assert AlertRecord.objects.filter(organization=first_org).count() == 1
    assert AlertRecord.objects.filter(organization=second_org).count() == 1
