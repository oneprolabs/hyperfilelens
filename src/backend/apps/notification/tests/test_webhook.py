import hashlib
import hmac
import json
from base64 import b64encode
from unittest.mock import MagicMock, patch

import pytest

from apps.notification.channels.webhook import WebhookChannel, _validate_platform_response, _webhook_platform
from apps.notification.exceptions import DingTalkDeliveryError, FeishuDeliveryError, WebhookTestError
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


@pytest.mark.parametrize("result", [
    {"code": 0, "msg": "success"},
    {"StatusCode": 0, "StatusMessage": "success"},
    {"code": "0", "StatusCode": 0},
])
def test_feishu_success_response(result):
    _validate_platform_response("feishu", json.dumps(result).encode())


@pytest.mark.parametrize("body", [
    b'{"code":9499,"msg":"Bad Request"}',
    b'{"StatusCode":19024,"StatusMessage":"Key Words Not Found"}',
    b'{"code":0,"StatusCode":19024}',
    b'{}', b'[]', b'null', b'not json', b'', b'\xff',
    b'{"code":false}',
])
def test_feishu_rejection_or_unconfirmed_response(body):
    with pytest.raises(FeishuDeliveryError):
        _validate_platform_response("feishu", body)


@pytest.mark.parametrize("url,platform", [
    ("https://open.feishu.cn/open-apis/bot/v2/hook/test", "feishu"),
    ("https://open.larksuite.com/open-apis/bot/v2/hook/test", "feishu"),
    ("https://open.feishu.cn.evil.example/open-apis/bot/v2/hook/test", ""),
    ("https://example.com/open-apis/bot/v2/hook/test", ""),
    ("https://open.feishu.cn/custom", ""),
])
def test_feishu_detection_is_limited_to_official_bot_endpoints(url, platform):
    assert _webhook_platform({"url": url}) == platform


@pytest.mark.parametrize("interactive", [True, False])
@pytest.mark.parametrize("accepted", [True, False])
def test_feishu_test_and_delivery_use_bot_payload_and_validate_response(interactive, accepted):
    channel = NotificationChannel(
        name="Feishu",
        channel_type="webhook",
        config={"url": "https://open.feishu.cn/open-apis/bot/v2/hook/test"},
    )
    response = MagicMock()
    response.status = 200
    response.read.return_value = json.dumps({
        "code": 0 if accepted else 9499,
        "msg": "success" if accepted else "Bad Request",
    }).encode()
    with patch("apps.notification.channels.webhook.urlrequest.urlopen") as send:
        send.return_value.__enter__.return_value = response

        def deliver():
            if interactive:
                return send_test_channel(channel)
            return WebhookChannel().send(
                channel=channel,
                delivery=NotificationDelivery(event_type="alert.firing", payload={"message": "test"}),
            )

        if accepted:
            result = deliver()
            if interactive:
                assert result == {"status": "success"}
        else:
            with pytest.raises(WebhookTestError if interactive else FeishuDeliveryError) as error:
                deliver()
            if interactive:
                assert error.value.code == "NOTIFICATION.FEISHU_REJECTED"
                assert "9499" in error.value.diagnostic
        payload = json.loads(send.call_args.args[0].data)
        assert payload["msg_type"] == "text"
        assert "HyperFileLens" in payload["content"]["text"]
        assert "event_type" not in payload


def test_generic_webhook_keeps_payload_and_signature():
    response = MagicMock()
    response.status = 200
    response.read.return_value = b"not json"
    channel = NotificationChannel(
        channel_type="webhook",
        config={"url": "https://example.com/hook", "secret": "test"},
    )
    with patch("apps.notification.channels.webhook.urlrequest.urlopen") as send:
        send.return_value.__enter__.return_value = response
        send_test_channel(channel)
    request = send.call_args.args[0]
    assert json.loads(request.data)["event_type"] == "channel.test"
    assert request.get_header("X-hfl-signature").startswith("sha256=")


def test_feishu_signed_payload():
    channel = NotificationChannel(
        channel_type="webhook",
        config={
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
            "secret": "test-secret",
        },
    )
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"code":0}'
    with (
        patch("apps.notification.channels.webhook.time.time", return_value=1600000000),
        patch("apps.notification.channels.webhook.urlrequest.urlopen") as send,
    ):
        send.return_value.__enter__.return_value = response
        send_test_channel(channel)
    request = send.call_args.args[0]
    payload = json.loads(request.data)
    assert payload["timestamp"] == "1600000000"
    assert payload["sign"] == b64encode(
        hmac.new(b"1600000000\ntest-secret", b"", hashlib.sha256).digest()
    ).decode()
    assert request.get_header("X-hfl-signature") is None
