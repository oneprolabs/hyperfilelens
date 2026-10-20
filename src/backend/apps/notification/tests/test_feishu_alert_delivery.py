import json
from unittest.mock import MagicMock, patch

import pytest

from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.notifier import send_notification, send_resolved_notification
from apps.iam.models import Organization
from apps.notification.models import NotificationChannel, NotificationDelivery, NotificationLog


@pytest.mark.django_db
@pytest.mark.parametrize("accepted", [True, False])
@pytest.mark.parametrize("resolved", [True, False])
def test_feishu_alert_records_delivery_and_log_outcome(accepted, resolved):
    org = Organization.objects.create(key="feishu-alert-test", name="Test")
    channel = NotificationChannel.objects.create(
        organization=org,
        name="Feishu",
        channel_type="webhook",
        config={
            "url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
            "secret": "test-signing-secret",
        },
    )
    policy = AlertPolicy.objects.create(
        organization=org,
        name="Test policy",
        type="event",
        severity="critical",
        notification_channel_ids=[channel.id],
    )
    alert = AlertRecord.objects.create(
        organization=org,
        policy_id=policy.id,
        type="event",
        severity="critical",
        status="resolved" if resolved else "firing",
        title="Test alert",
        message="Test resource needs attention",
        resource_name="Test resource",
        fingerprint="feishu-alert-test",
    )
    response = MagicMock()
    response.status = 200
    response.read.return_value = json.dumps({
        "code": 0 if accepted else 19024,
        "msg": "success" if accepted else "Key Words Not Found",
    }).encode()
    with patch("apps.notification.channels.webhook.urlrequest.urlopen") as send:
        send.return_value.__enter__.return_value = response
        (send_resolved_notification if resolved else send_notification)(alert)
    delivery = NotificationDelivery.objects.get(channel=channel)
    log = NotificationLog.objects.get(channel=channel, alert_record_id=alert.id)
    assert delivery.status == ("sent" if accepted else "failed")
    assert log.status == ("success" if accepted else "failed")
    assert delivery.event_type == log.event_type == ("alert.resolved" if resolved else "alert.firing")
    assert log.notification_type == ("resolved" if resolved else "firing")
    assert bool(delivery.sent_at) is accepted
    if accepted:
        assert delivery.error == log.error_message == ""
    else:
        assert "19024" in delivery.error
        assert log.error_message == delivery.error
    request = send.call_args.args[0]
    payload = json.loads(request.data)
    assert payload["msg_type"] == "text"
    assert "Test alert" in payload["content"]["text"]
    assert "Test resource" in payload["content"]["text"]
    assert payload["timestamp"]
    assert payload["sign"]
    assert "test-signing-secret" not in request.data.decode()
    assert send.call_count == 1
