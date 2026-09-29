import json
from unittest.mock import patch

import pytest

from apps.notification.channels.webhook import WebhookChannel, _validate_platform_response
from apps.notification.exceptions import DingTalkDeliveryError
from apps.notification.models import NotificationChannel, NotificationDelivery
from apps.notification.services.internal.channel_test import test_channel as send_test_channel


def test_dingtalk_success_response_is_accepted():
    _validate_platform_response(
        "dingtalk",
        json.dumps({"errcode": 0, "errmsg": "ok"}).encode(),
    )


def test_dingtalk_application_error_is_reported():
    with pytest.raises(DingTalkDeliveryError, match="errcode=310000"):
        _validate_platform_response(
            "dingtalk",
            json.dumps({"errcode": 310000, "errmsg": "keyword mismatch"}).encode(),
        )


def test_generic_webhook_response_is_not_interpreted_as_dingtalk():
    _validate_platform_response("webhook", b"not json")


def test_dingtalk_test_send_reports_platform_rejection():
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"errcode":310000,"errmsg":"keyword mismatch"}'

    channel = NotificationChannel(
        name="DingTalk",
        channel_type="dingtalk",
        config={"webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test"},
    )
    with patch("apps.notification.channels.webhook.urlrequest.urlopen", return_value=Response()):
        with pytest.raises(Exception) as error:
            send_test_channel(channel)
    assert error.value.code == "NOTIFICATION.DINGTALK_REJECTED"
    assert "errcode=310000" in error.value.diagnostic


def test_dingtalk_delivery_reports_platform_rejection():
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"errcode":310000,"errmsg":"keyword mismatch"}'

    channel = NotificationChannel(
        name="DingTalk",
        channel_type="webhook",
        config={
            "url": "https://oapi.dingtalk.com/robot/send?access_token=test",
            "webhook_platform": "dingtalk",
        },
    )
    delivery = NotificationDelivery(event_type="alert.firing", payload={"message": "test"})
    with patch("apps.notification.channels.webhook.urlrequest.urlopen", return_value=Response()):
        with pytest.raises(DingTalkDeliveryError, match="keyword mismatch"):
            WebhookChannel().send(channel=channel, delivery=delivery)
