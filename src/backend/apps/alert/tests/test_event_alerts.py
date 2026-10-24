import pytest

from apps.alert.constants import AlertType, ResourceType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.event_alerts import handle_platform_event
from apps.iam.models import Organization
from common.resource_scope import ResourceScope


@pytest.mark.django_db
def test_platform_owned_event_policy_fires_for_tenant_event():
    org = Organization.objects.create(key="platform-event-org", name="Platform Event Org")
    platform_policy = AlertPolicy.objects.create(
        organization=None,
        owner_scope=ResourceScope.PLATFORM,
        name="Platform login failures",
        type=AlertType.EVENT,
        severity="warning",
        enabled=True,
        resource_type=ResourceType.USER,
        scope="all",
        trigger_rule={
            "event_category": "user",
            "event_types": ["login_failed"],
        },
    )
    org_policy = AlertPolicy.objects.create(
        organization=org,
        owner_scope=ResourceScope.ORGANIZATION,
        name="Org login failures",
        type=AlertType.EVENT,
        severity="warning",
        enabled=True,
        resource_type=ResourceType.USER,
        scope="all",
        trigger_rule={
            "event_category": "user",
            "event_types": ["login_failed"],
        },
    )

    fired = handle_platform_event(
        organization=org,
        event_category="user",
        event_type="login_failed",
        title="Login failed",
        resource_type="user",
        resource_id="42",
        resource_name="alice",
    )

    assert fired == 2
    assert AlertRecord.objects.filter(policy_id=platform_policy.id).count() == 1
    assert AlertRecord.objects.filter(policy_id=org_policy.id).count() == 1
    platform_alert = AlertRecord.objects.get(policy_id=platform_policy.id)
    assert platform_alert.owner_scope == ResourceScope.PLATFORM
    assert platform_alert.organization_id is None
    assert platform_alert.metadata["organization_id"] == org.id
    assert platform_alert.metadata["organization_key"] == org.key


@pytest.mark.django_db
def test_platform_owned_event_policy_keeps_tenant_incidents_separate():
    org_a = Organization.objects.create(key="event-org-a", name="Event Org A")
    org_b = Organization.objects.create(key="event-org-b", name="Event Org B")
    platform_policy = AlertPolicy.objects.create(
        organization=None,
        owner_scope=ResourceScope.PLATFORM,
        name="Platform login failures",
        type=AlertType.EVENT,
        severity="warning",
        enabled=True,
        resource_type=ResourceType.USER,
        scope="all",
        trigger_rule={
            "event_category": "user",
            "event_types": ["login_failed"],
        },
    )

    for org in (org_a, org_b):
        handle_platform_event(
            organization=org,
            event_category="user",
            event_type="login_failed",
            title="Login failed",
            resource_type="user",
            resource_id="42",
            resource_name="alice",
        )

    alerts = list(AlertRecord.objects.filter(policy_id=platform_policy.id).order_by("id"))
    assert len(alerts) == 2
    assert {alert.metadata["organization_id"] for alert in alerts} == {org_a.id, org_b.id}
    assert alerts[0].fingerprint != alerts[1].fingerprint
