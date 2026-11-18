import json
from unittest.mock import Mock, patch

import pytest

from apps.alert.models import AlertRecord
from apps.alert.services.internal.alert_email import build_alert_email_subject
from apps.alert.services.internal.notifier import _payload_for_alert
from apps.notification.channels.email import EmailChannel
from apps.notification.channels.sms import SmsChannel
from apps.notification.channels.webhook import _delivery_text
from apps.notification.models import NotificationChannel, NotificationDelivery


@pytest.mark.parametrize("resolved", [False, True])
@pytest.mark.parametrize("configured_subject", ["", "Operations"])
def test_task_alert_subject_is_shared_across_channels(resolved, configured_subject):
    title = "Task failed: Backup aimychats.com"
    alert = AlertRecord(type="task", title=title, message="Agent source is offline.")
    delivery = NotificationDelivery(
        event_type="alert.resolved" if resolved else "alert.firing",
        payload=_payload_for_alert(alert, resolved=resolved),
    )
    expected = f"Resolved: {title}" if resolved else title
    channel = NotificationChannel(config={"to": ["ops@example.test"], "email_subject": configured_subject})
    with (
        patch("apps.notification.channels.email._connection_from_platform_email", return_value=(Mock(), {})),
        patch("apps.notification.channels.email.EmailMessage") as email,
    ):
        email.return_value.send.return_value = 1
        EmailChannel().send(channel=channel, delivery=delivery)
    assert email.call_args.kwargs["subject"] == f"{configured_subject or '[HyperFileLens]'} {expected}"
    assert build_alert_email_subject(title=title, severity="critical", resolved=resolved) == (
        expected if resolved else f"Critical alert: {title}"
    )
    assert _delivery_text(delivery).splitlines()[0] == f"[HyperFileLens] {expected}"

    channel.config = {"url": "https://example.test", "to": ["10000000000"], "message_template": "{event_type}"}
    with patch("apps.notification.channels.sms.urlrequest.urlopen") as send:
        send.return_value.__enter__.return_value.status = 200
        SmsChannel().send(channel=channel, delivery=delivery)
    assert json.loads(send.call_args.args[0].data)["message"] == f"{delivery.event_type}\n{expected}"
