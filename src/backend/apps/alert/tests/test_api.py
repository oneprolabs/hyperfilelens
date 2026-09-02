import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.alert.constants import AlertSeverity, AlertStatus, AlertType, ResourceType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.selectors.stats import policy_statistics
from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole


@pytest.fixture
def org_user_client(db):
    User = get_user_model()
    user = User.objects.create_user(username="alert_tester", password="test-pass-123")
    org = Organization.objects.create(key="alert-org", name="Alert Org")
    Membership.objects.create(
        user=user,
        organization=org,
        role=Membership.Role.ADMIN,
        is_active=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, org


@pytest.mark.django_db
def test_policy_crud_and_record_actions(org_user_client):
    client, org = org_user_client
    headers = {"HTTP_X_ORG_KEY": org.key}

    create_res = client.post(
        "/api/v1/alerts/policies/",
        {
            "name": "CPU high",
            "description": "test",
            "type": AlertType.TASK,
            "severity": "warning",
            "enabled": True,
            "resource_type": "task",
            "scope": "all",
            "resource_ids": [],
            "trigger_rule": {"task_type": "backup", "event_type": "task_failed"},
            "recovery_rule": {"enabled": True},
            "notification_channel_ids": [],
        },
        format="json",
        **headers,
    )
    assert create_res.status_code == 201, create_res.content
    policy_id = create_res.data["id"]

    list_res = client.get("/api/v1/alerts/policies/", **headers)
    assert list_res.status_code == 200
    assert list_res.data["count"] >= 1

    record = AlertRecord.objects.create(
        organization=org,
        policy_id=policy_id,
        type=AlertType.TASK,
        severity="warning",
        status=AlertStatus.FIRING,
        resource_type="task",
        resource_id="1",
        resource_name="backup / 1",
        title="Task failed",
        message="error",
        fingerprint="test-fp",
        metadata={},
    )

    records_res = client.get(
        "/api/v1/alerts/records/?status=firing",
        **headers,
    )
    assert records_res.status_code == 200
    assert records_res.data["count"] >= 1

    all_records_res = client.get("/api/v1/alerts/records/", **headers)
    assert all_records_res.status_code == 200
    assert all_records_res.data["count"] >= 1

    legacy_all_records_res = client.get(
        "/api/v1/alerts/records/?status=all",
        **headers,
    )
    assert legacy_all_records_res.status_code == 200
    assert legacy_all_records_res.data["count"] == all_records_res.data["count"]

    ack_res = client.post(
        f"/api/v1/alerts/records/{record.id}/acknowledge/",
        {"note": "seen"},
        format="json",
        **headers,
    )
    assert ack_res.status_code == 200
    assert ack_res.data["status"] == AlertStatus.ACKNOWLEDGED

    resolve_res = client.post(
        f"/api/v1/alerts/records/{record.id}/resolve/",
        {},
        format="json",
        **headers,
    )
    assert resolve_res.status_code == 200
    assert resolve_res.data["status"] == AlertStatus.RESOLVED

    meta_res = client.get("/api/v1/alerts/metadata/alert-types/", **headers)
    assert meta_res.status_code == 200
    assert len(meta_res.data) >= 1

    dup_res = client.post(f"/api/v1/alerts/policies/{policy_id}/duplicate/", **headers)
    assert dup_res.status_code == 201
    assert AlertPolicy.objects.filter(organization=org).count() == 2


@pytest.mark.django_db
def test_metric_policy_rejects_mismatched_node_role(org_user_client):
    client, org = org_user_client
    proxy = Node.objects.create(organization=org, name="proxy", role=NodeRole.PROXY)

    response = client.post(
        "/api/v1/alerts/policies/",
        {
            "name": "Invalid source host memory",
            "type": AlertType.METRIC,
            "severity": "warning",
            "enabled": True,
            "resource_type": ResourceType.AGENT_PROXY,
            "scope": "selected",
            "resource_ids": [str(proxy.id)],
            "trigger_rule": {
                "metric_key": "memory_usage",
                "operator": ">=",
                "threshold": 39,
                "duration_seconds": 60,
                "evaluation_interval_seconds": 60,
            },
            "recovery_rule": {"enabled": True},
            "notification_channel_ids": [],
        },
        format="json",
        HTTP_X_ORG_KEY=org.key,
    )

    assert response.status_code == 400
    assert "resource_ids" in {
        error["field"] for error in response.data["data"]["errors"]
    }


@pytest.mark.django_db
def test_metric_policy_rejects_metric_for_another_resource_type(org_user_client):
    client, org = org_user_client

    response = client.post(
        "/api/v1/alerts/policies/",
        {
            "name": "Invalid source host metric",
            "type": AlertType.METRIC,
            "severity": "warning",
            "enabled": True,
            "resource_type": ResourceType.AGENT_PROXY,
            "scope": "all",
            "resource_ids": [],
            "trigger_rule": {
                "metric_key": "capacity_usage",
                "operator": ">=",
                "threshold": 80,
                "duration_seconds": 60,
                "evaluation_interval_seconds": 60,
            },
            "recovery_rule": {"enabled": True},
            "notification_channel_ids": [],
        },
        format="json",
        HTTP_X_ORG_KEY=org.key,
    )

    assert response.status_code == 400
    assert "trigger_rule" in {
        error["field"] for error in response.data["data"]["errors"]
    }


@pytest.mark.django_db
def test_selected_policy_returns_monitoring_resource_names(org_user_client):
    client, org = org_user_client
    source_host = Node.objects.create(
        organization=org,
        name="hfl-source-test-whx-1",
        role=NodeRole.AGENT,
    )
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Source host memory",
        type=AlertType.METRIC,
        severity=AlertSeverity.WARNING,
        enabled=True,
        resource_type=ResourceType.AGENT_PROXY,
        scope="selected",
        resource_ids=[str(source_host.id)],
        trigger_rule={
            "metric_key": "memory_usage",
            "operator": ">=",
            "threshold": 39,
            "duration_seconds": 60,
            "evaluation_interval_seconds": 60,
        },
    )

    response = client.get(
        f"/api/v1/alerts/policies/{policy.id}/",
        HTTP_X_ORG_KEY=org.key,
    )

    assert response.status_code == 200
    assert response.data["monitoring_resources"] == [
        {
            "id": str(source_host.id),
            "name": source_host.name,
            "status": source_host.status,
        }
    ]


@pytest.mark.django_db
def test_selected_policy_preserves_unavailable_resource_ids(org_user_client):
    client, org = org_user_client
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Missing source host",
        type=AlertType.METRIC,
        severity=AlertSeverity.WARNING,
        enabled=True,
        resource_type=ResourceType.AGENT_PROXY,
        scope="selected",
        resource_ids=["999999"],
        trigger_rule={
            "metric_key": "memory_usage",
            "operator": ">=",
            "threshold": 39,
            "duration_seconds": 60,
            "evaluation_interval_seconds": 60,
        },
    )

    response = client.get(
        f"/api/v1/alerts/policies/{policy.id}/",
        HTTP_X_ORG_KEY=org.key,
    )

    assert response.status_code == 200
    assert response.data["monitoring_resources"] == [
        {
            "id": "999999",
            "name": "",
            "status": "unavailable",
            "available": False,
        }
    ]


@pytest.mark.django_db
def test_policy_statistics_counts_enabled_without_field_alias_conflict(org_user_client):
    _client, org = org_user_client

    AlertPolicy.objects.create(
        organization=org,
        name="Critical enabled",
        type=AlertType.METRIC,
        severity=AlertSeverity.CRITICAL,
        enabled=True,
        resource_type="system",
        scope="all",
        trigger_rule={"metric": "cpu_usage"},
    )
    AlertPolicy.objects.create(
        organization=org,
        name="Warning disabled",
        type=AlertType.METRIC,
        severity=AlertSeverity.WARNING,
        enabled=False,
        resource_type="system",
        scope="all",
        trigger_rule={"metric": "memory_usage"},
    )

    stats = policy_statistics(organization_id=org.id)

    assert stats == {
        "total": 2,
        "enabled": 1,
        "disabled": 1,
        "critical": 1,
        "warning": 1,
        "info": 0,
        "enabled_rate": 50.0,
    }


@pytest.mark.django_db
def test_policy_bulk_state_and_delete(org_user_client):
    client, org = org_user_client
    headers = {"HTTP_X_ORG_KEY": org.key}
    other_org = Organization.objects.create(key="other-alert-org", name="Other Alert Org")

    first = AlertPolicy.objects.create(
        organization=org,
        name="First",
        type=AlertType.METRIC,
        severity=AlertSeverity.WARNING,
        enabled=True,
        resource_type="system",
        scope="all",
        trigger_rule={"metric": "cpu_usage"},
    )
    second = AlertPolicy.objects.create(
        organization=org,
        name="Second",
        type=AlertType.METRIC,
        severity=AlertSeverity.CRITICAL,
        enabled=True,
        resource_type="system",
        scope="all",
        trigger_rule={"metric": "memory_usage"},
    )
    other = AlertPolicy.objects.create(
        organization=other_org,
        name="Other",
        type=AlertType.METRIC,
        severity=AlertSeverity.WARNING,
        enabled=True,
        resource_type="system",
        scope="all",
        trigger_rule={"metric": "disk_usage"},
    )

    state_res = client.post(
        "/api/v1/alerts/policies/bulk-state/",
        {
            "ids": [str(first.id), str(second.id), str(other.id)],
            "enabled": False,
        },
        format="json",
        **headers,
    )
    assert state_res.status_code == 200, state_res.content
    assert set(state_res.data["updated"]) == {str(first.id), str(second.id)}
    assert state_res.data["failed"] == [{"id": str(other.id), "reason": "not_found"}]
    first.refresh_from_db()
    second.refresh_from_db()
    other.refresh_from_db()
    assert not first.enabled
    assert not second.enabled
    assert other.enabled

    delete_res = client.post(
        "/api/v1/alerts/policies/bulk-delete/",
        {"ids": [str(first.id), str(second.id), str(other.id)]},
        format="json",
        **headers,
    )
    assert delete_res.status_code == 200, delete_res.content
    assert set(delete_res.data["deleted"]) == {str(first.id), str(second.id)}
    assert delete_res.data["failed"] == [{"id": str(other.id), "reason": "not_found"}]
    assert not AlertPolicy.objects.filter(id__in=[first.id, second.id]).exists()
    assert AlertPolicy.objects.filter(id=other.id).exists()
