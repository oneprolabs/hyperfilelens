from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.notification.channel_push import channel_group_name
from apps.notification.models import NotificationChannel, NotificationLog


def test_channel_group_name_is_channels_safe():
    assert channel_group_name("org", "default") == "org.default"
    assert channel_group_name("org", "tenant:default") == "org.tenant_default"
    assert ":" not in channel_group_name("user", 12)
    assert len(channel_group_name("org", "x" * 200)) < 100


@pytest.fixture
def org_client(db):
    User = get_user_model()
    user = User.objects.create_user(username="notify_tester", password="test-pass-123")
    org = Organization.objects.create(key="notify-org", name="Notify Org")
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
def test_channel_crud_and_logs_list(org_client):
    client, org = org_client
    headers = {"HTTP_X_ORG_KEY": org.key}

    res = client.post(
        "/api/v1/notifications/channels/",
        {
            "name": "Ops Email",
            "type": "email",
            "enabled": True,
            "config": {"to_emails": "ops@example.com"},
        },
        format="json",
        **headers,
    )
    assert res.status_code == 201, res.content
    channel_id = res.data["id"]

    list_res = client.get("/api/v1/notifications/channels/", **headers)
    assert list_res.status_code == 200
    assert list_res.data["count"] >= 1

    logs_res = client.get("/api/v1/notifications/logs/", **headers)
    assert logs_res.status_code == 200

    stats_res = client.get("/api/v1/notifications/logs/stats/", **headers)
    assert stats_res.status_code == 200
    assert "total" in stats_res.data

    detail_res = client.get(f"/api/v1/notifications/channels/{channel_id}/details/", **headers)
    assert detail_res.status_code == 200
    assert detail_res.data["channel"]["name"] == "Ops Email"

    assert NotificationChannel.objects.filter(organization=org).count() == 1


@pytest.mark.django_db
def test_channel_update_preserves_masked_sensitive_config(org_client):
    client, org = org_client
    headers = {"HTTP_X_ORG_KEY": org.key}
    channel = NotificationChannel.objects.create(
        organization=org,
        name="DingTalk",
        channel_type="dingtalk",
        config={
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=old",
            "secret": "existing-secret",
            "is_at_all": True,
        },
    )

    # The editor omits the secret when it has not been changed. A client that
    # round-trips the masked API value must also not overwrite the credential.
    res = client.patch(
        f"/api/v1/notifications/channels/{channel.id}/",
        {
            "config": {
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=new",
                "secret": "********",
            },
        },
        format="json",
        **headers,
    )

    assert res.status_code == 200, res.content
    channel.refresh_from_db()
    assert channel.config["webhook_url"].endswith("access_token=new")
    assert channel.config["secret"] == "existing-secret"
    assert res.data["config"]["secret"] == "********"

    res = client.patch(
        f"/api/v1/notifications/channels/{channel.id}/",
        {
            "config": {
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=latest",
            },
        },
        format="json",
        **headers,
    )

    assert res.status_code == 200, res.content
    channel.refresh_from_db()
    assert channel.config["webhook_url"].endswith("access_token=latest")
    assert channel.config["secret"] == "existing-secret"

    res = client.patch(
        f"/api/v1/notifications/channels/{channel.id}/",
        {
            "config": {
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=cleared",
                "clear_secret": True,
            },
        },
        format="json",
        **headers,
    )

    assert res.status_code == 200, res.content
    channel.refresh_from_db()
    assert channel.config["webhook_url"].endswith("access_token=cleared")
    assert "secret" not in channel.config

@pytest.mark.django_db
def test_dingtalk_details_include_saved_configuration(org_client):
    """DingTalk Overview data remains populated after the channel is saved."""
    client, org = org_client
    headers = {"HTTP_X_ORG_KEY": org.key}
    channel = NotificationChannel.objects.create(
        organization=org,
        name="regression-dingtalk-1254",
        channel_type="dingtalk",
        config={
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test",
            "secret": "test-secret",
            "at_mobiles": ["13800138000"],
            "at_user_ids": ["user-1"],
            "is_at_all": True,
        },
    )

    response = client.get(
        f"/api/v1/notifications/channels/{channel.id}/details/",
        **headers,
    )

    assert response.status_code == 200, response.content
    data = response.data
    assert data["channel"]["id"] == channel.id
    assert data["channel"]["name"] == "regression-dingtalk-1254"
    assert data["channel"]["type"] == "dingtalk"
    assert data["channel"]["config"] == {
        "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test",
        "secret": "********",
        "at_mobiles": ["13800138000"],
        "at_user_ids": ["user-1"],
        "is_at_all": True,
    }
    assert data["stats"]["logs_count"] == 0
    assert data["stats"]["success_rate"] == 0
    assert data["stats"]["policies_count"] == 0
    assert data["stats"]["alerts_count"] == 0

@pytest.mark.django_db
def test_email_channel_requires_recipients(org_client):
    client, org = org_client
    headers = {"HTTP_X_ORG_KEY": org.key}

    res = client.post(
        "/api/v1/notifications/channels/",
        {
            "name": "Incomplete Email",
            "type": "email",
            "enabled": True,
            "config": {"to_emails": ""},
        },
        format="json",
        **headers,
    )

    assert res.status_code == 400, res.content
    assert "to_emails" in str(res.data)
    assert not NotificationChannel.objects.filter(
        organization=org,
        name="Incomplete Email",
    ).exists()


@pytest.mark.django_db
def test_draft_channel_test_uses_unsaved_config_and_saved_secret(org_client):
    client, org = org_client
    headers = {"HTTP_X_ORG_KEY": org.key}
    channel = NotificationChannel.objects.create(
        organization=org,
        name="DingTalk",
        channel_type="dingtalk",
        config={"webhook_url": "https://old.example.com", "secret": "saved-secret"},
    )
    with patch("apps.notification.api.views.channel.test_channel", return_value={"status": "success"}) as send:
        res = client.post(
            "/api/v1/notifications/channels/test-config/",
            {
                "channel_id": channel.id,
                "type": "dingtalk",
                "config": {"webhook_url": "https://new.example.com"},
            },
            format="json",
            **headers,
        )
    assert res.status_code == 200, res.content
    draft = send.call_args.args[0]
    assert draft.config["webhook_url"] == "https://new.example.com"
    assert draft.config["secret"] == "saved-secret"
    channel.refresh_from_db()
    assert channel.config == {"webhook_url": "https://old.example.com", "secret": "saved-secret"}

    with patch("apps.notification.api.views.channel.test_channel", return_value={"status": "success"}) as send:
        res = client.post(
            "/api/v1/notifications/channels/test-config/",
            {
                "channel_id": channel.id,
                "type": "dingtalk",
                "config": {"webhook_url": "https://new.example.com", "clear_secret": True},
            },
            format="json",
            **headers,
        )
    assert res.status_code == 200, res.content
    assert "secret" not in send.call_args.args[0].config
    channel.refresh_from_db()
    assert channel.config["secret"] == "saved-secret"


@pytest.mark.django_db
def test_draft_channel_test_rejects_cross_org_channel(org_client):
    client, org = org_client
    other = Organization.objects.create(key="other-test-org", name="Other Org")
    channel = NotificationChannel.objects.create(
        organization=other,
        name="Other DingTalk",
        channel_type="dingtalk",
        config={"webhook_url": "https://other.example.com", "secret": "private"},
    )
    with patch("apps.notification.api.views.channel.test_channel") as send:
        res = client.post(
            "/api/v1/notifications/channels/test-config/",
            {
                "channel_id": channel.id,
                "type": "dingtalk",
                "config": {"webhook_url": "https://new.example.com"},
            },
            format="json",
            HTTP_X_ORG_KEY=org.key,
        )
    assert res.status_code == 404
    send.assert_not_called()

@pytest.mark.django_db
def test_channel_bulk_state_and_delete(org_client):
    client, org = org_client
    headers = {"HTTP_X_ORG_KEY": org.key}
    channels = [
        NotificationChannel.objects.create(
            organization=org,
            name=f"Channel {idx}",
            channel_type="webhook",
            is_active=(idx == 1),
            config={"url": f"https://example.com/{idx}"},
        )
        for idx in range(3)
    ]

    disable_res = client.post(
        "/api/v1/notifications/channels/bulk-state/",
        {"ids": [channels[0].id, channels[1].id, 999999], "is_active": False},
        format="json",
        **headers,
    )
    assert disable_res.status_code == 200, disable_res.content
    assert set(disable_res.data["updated"]) == {channels[0].id, channels[1].id}
    assert disable_res.data["failed"] == [{"id": 999999, "reason": "not_found"}]
    assert not NotificationChannel.objects.get(id=channels[0].id).is_active
    assert not NotificationChannel.objects.get(id=channels[1].id).is_active

    enable_res = client.post(
        "/api/v1/notifications/channels/bulk-state/",
        {"ids": [channels[1].id, channels[2].id], "is_active": True},
        format="json",
        **headers,
    )
    assert enable_res.status_code == 200, enable_res.content
    assert set(enable_res.data["updated"]) == {channels[1].id, channels[2].id}
    assert NotificationChannel.objects.get(id=channels[1].id).is_active
    assert NotificationChannel.objects.get(id=channels[2].id).is_active

    delete_res = client.post(
        "/api/v1/notifications/channels/bulk-delete/",
        {"ids": [channels[0].id, channels[2].id, 999998]},
        format="json",
        **headers,
    )
    assert delete_res.status_code == 200, delete_res.content
    assert set(delete_res.data["deleted"]) == {channels[0].id, channels[2].id}
    assert delete_res.data["failed"] == [{"id": 999998, "reason": "not_found"}]
    assert not NotificationChannel.objects.filter(id__in=[channels[0].id, channels[2].id]).exists()
    assert NotificationChannel.objects.filter(id=channels[1].id).exists()


@pytest.mark.django_db
def test_channel_association_summary(org_client):
    """association-summary must list alert policies that reference each channel
    and mark out-of-org ids as missing."""
    from apps.alert.models import AlertPolicy

    client, org = org_client
    headers = {"HTTP_X_ORG_KEY": org.key}
    channels = [
        NotificationChannel.objects.create(
            organization=org,
            name="Linked",
            channel_type="webhook",
            config={"url": "https://example.com/a"},
        ),
        NotificationChannel.objects.create(
            organization=org,
            name="Unlinked",
            channel_type="email",
            config={"to_emails": "ops@example.com"},
        ),
    ]
    AlertPolicy.objects.create(
        organization=org,
        name="Disk Watermark",
        notification_channel_ids=[str(channels[0].id), "99999"],
    )
    AlertPolicy.objects.create(
        organization=org,
        name="Backup Failed",
        notification_channel_ids=[str(channels[0].id)],
    )
    NotificationLog.objects.create(
        organization=org,
        channel=channels[0],
        event_type="alert.firing",
        notification_type="firing",
        status="success",
    )

    list_res = client.get("/api/v1/notifications/channels/", **headers)
    assert list_res.status_code == 200, list_res.content
    listed = {it["id"]: it for it in list_res.data["results"]}
    assert listed[channels[0].id]["policies_count"] == 2
    assert listed[channels[0].id]["last_delivery_status"] == "success"
    assert listed[channels[0].id]["last_delivery_at"]
    assert listed[channels[1].id]["policies_count"] == 0
    assert listed[channels[1].id]["last_delivery_status"] is None

    res = client.get(
        "/api/v1/notifications/channels/association-summary/",
        {"ids": f"{channels[0].id},{channels[1].id},999998"},
        **headers,
    )
    assert res.status_code == 200, res.content
    items = {it["id"]: it for it in res.data["items"]}
    assert set(items) == {channels[0].id, channels[1].id}
    assert items[channels[0].id]["policies_count"] == 2
    policy_names = {p["name"] for p in items[channels[0].id]["policies"]}
    assert policy_names == {"Disk Watermark", "Backup Failed"}
    assert items[channels[1].id]["policies_count"] == 0
    assert res.data["missing"] == [999998]


@pytest.mark.django_db
def test_channel_bulk_state_cross_org_isolation(org_client):
    """Channels from other organisations must not be affected by bulk ops."""
    client, org = org_client
    headers = {"HTTP_X_ORG_KEY": org.key}
    other_org = Organization.objects.create(key="other-org", name="Other Org")
    mine = NotificationChannel.objects.create(
        organization=org,
        name="mine",
        channel_type="webhook",
        is_active=True,
        config={"url": "https://example.com/mine"},
    )
    theirs = NotificationChannel.objects.create(
        organization=other_org,
        name="theirs",
        channel_type="webhook",
        is_active=True,
        config={"url": "https://example.com/theirs"},
    )

    res = client.post(
        "/api/v1/notifications/channels/bulk-state/",
        {"ids": [mine.id, theirs.id], "is_active": False},
        format="json",
        **headers,
    )
    assert res.status_code == 200, res.content
    assert res.data["updated"] == [mine.id]
    assert res.data["failed"] == [{"id": theirs.id, "reason": "not_found"}]
    assert not NotificationChannel.objects.get(id=mine.id).is_active
    assert NotificationChannel.objects.get(id=theirs.id).is_active

    del_res = client.post(
        "/api/v1/notifications/channels/bulk-delete/",
        {"ids": [theirs.id]},
        format="json",
        **headers,
    )
    assert del_res.status_code == 200
    assert del_res.data["deleted"] == []
    assert del_res.data["failed"] == [{"id": theirs.id, "reason": "not_found"}]
    assert NotificationChannel.objects.filter(id=theirs.id).exists()
