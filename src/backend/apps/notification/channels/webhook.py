from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import b64encode
from urllib.parse import quote_plus, urlencode, urlparse, urlunparse
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from apps.notification.channels.base import BaseChannel
from apps.notification.exceptions import ChannelConfigError, DingTalkDeliveryError
from apps.notification.models import NotificationChannel, NotificationDelivery


def webhook_url(cfg: dict) -> str:
    return str(cfg.get("url") or cfg.get("webhook_url") or cfg.get("endpoint") or "").strip()


def _list_config(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _delivery_text(delivery: NotificationDelivery) -> str:
    payload = delivery.payload or {}
    title = str(payload.get("title") or delivery.event_type)
    message = str(payload.get("message") or "").strip()
    severity = str(payload.get("severity") or "").strip()
    resource = str(payload.get("resource_name") or payload.get("resource_id") or "").strip()
    lines = [f"[HyperFileLens] {title}"]
    if severity:
        lines.append(f"Severity: {severity}")
    if resource:
        lines.append(f"Resource: {resource}")
    if message:
        lines.append(message)
    return "\n".join(lines)


def _dingtalk_url(url: str, secret: str) -> str:
    if not secret:
        return url
    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sign = quote_plus(b64encode(digest).decode("utf-8"))
    parsed = urlparse(url)
    extra = urlencode({"timestamp": timestamp}) + f"&sign={sign}"
    query = f"{parsed.query}&{extra}" if parsed.query else extra
    return urlunparse(parsed._replace(query=query))


def _platform_payload(cfg: dict, delivery: NotificationDelivery) -> dict | None:
    platform = str(cfg.get("webhook_platform") or "").lower()
    text = _delivery_text(delivery)
    if platform == "dingtalk":
        return {
            "msgtype": "text",
            "text": {"content": text},
            "at": {
                "atMobiles": _list_config(cfg.get("at_mobiles")),
                "atUserIds": _list_config(cfg.get("at_user_ids")),
                "isAtAll": cfg.get("is_at_all") is True,
            },
        }
    if platform == "wecom":
        mentioned = _list_config(cfg.get("mentioned_list"))
        if cfg.get("is_at_all") is True and "@all" not in mentioned:
            mentioned = ["@all"]
        return {
            "msgtype": "text",
            "text": {
                "content": text,
                "mentioned_list": mentioned,
                "mentioned_mobile_list": _list_config(cfg.get("mentioned_mobile_list")),
            },
        }
    return None


def _validate_platform_response(platform: str, response_body: bytes) -> None:
    """Raise when a platform reports an application-level delivery failure."""
    if platform != "dingtalk":
        return

    try:
        result = json.loads(response_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("DingTalk returned an invalid JSON response") from exc

    if not isinstance(result, dict):
        raise RuntimeError("DingTalk returned an invalid JSON response")
    errcode = result.get("errcode")
    if str(errcode) != "0":
        raise DingTalkDeliveryError(errcode, result.get("errmsg"))


class WebhookChannel(BaseChannel):
    def send(self, *, channel: NotificationChannel, delivery: NotificationDelivery) -> None:
        cfg = channel.config or {}
        url = str(cfg.get("url") or cfg.get("endpoint") or "").strip()
        if not url:
            raise ChannelConfigError("webhook channel missing config.url")

        timeout = float(cfg.get("timeout_seconds") or 8)
        max_attempts = int(cfg.get("max_attempts") or 3)

        payload = _platform_payload(cfg, delivery) or {
            "event_type": delivery.event_type,
            "delivery_id": delivery.id,
            "organization_id": delivery.organization_id,
            "created_at": (
                delivery.created_at.isoformat() if delivery.created_at else None
            ),
            "payload": delivery.payload or {},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "hyperfilelens/notification",
        }
        extra_headers = cfg.get("headers") or {}
        if isinstance(extra_headers, dict):
            for key, value in extra_headers.items():
                if key and value is not None:
                    headers[str(key)] = str(value)

        secret = str(cfg.get("secret") or "").strip()
        platform = str(cfg.get("webhook_platform") or "").lower()
        if platform == "dingtalk":
            url = _dingtalk_url(url, secret)
            secret = ""
        if secret:
            sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-HFL-Signature"] = f"sha256={sig}"

        method = str(cfg.get("method") or "POST").upper()
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                req = urlrequest.Request(url=url, data=body, headers=headers, method=method)
                with urlrequest.urlopen(req, timeout=timeout) as resp:
                    code = int(getattr(resp, "status", 200))
                    if code < 200 or code >= 300:
                        raise RuntimeError(f"webhook non-2xx: {code}")
                    _validate_platform_response(platform, resp.read())
                return
            except HTTPError as exc:
                last_err = exc
                code = int(getattr(exc, "code", 0) or 0)
                if 400 <= code < 500:
                    raise
            except (URLError, TimeoutError, RuntimeError) as exc:
                last_err = exc

            if attempt < max_attempts:
                continue

        raise RuntimeError(str(last_err or "webhook failed"))
