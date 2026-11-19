from __future__ import annotations

import json
from urllib import request as urlrequest

from apps.notification.channels.alert_title import task_alert_title
from apps.notification.channels.base import BaseChannel
from apps.notification.exceptions import ChannelConfigError
from apps.notification.models import NotificationChannel, NotificationDelivery


class SmsChannel(BaseChannel):
    def send(self, *, channel: NotificationChannel, delivery: NotificationDelivery) -> None:
        cfg = channel.config or {}
        url = str(cfg.get("url") or cfg.get("api_url") or "").strip()
        if not url:
            raise ChannelConfigError("sms channel missing config.url")

        phones = cfg.get("phone_numbers") or cfg.get("phones") or cfg.get("to") or []
        if isinstance(phones, str):
            phones = [x.strip() for x in phones.split(",") if x.strip()]
        if not isinstance(phones, list) or not phones:
            raise ChannelConfigError("sms channel missing config.phone_numbers")

        title = task_alert_title(delivery)
        template = str(
            cfg.get("message_template")
            or cfg.get("body")
            or "[HyperFileLens] {title}"
        )
        message = template.format(
            event_type=delivery.event_type,
            title=title or delivery.event_type,
            payload=delivery.payload or {},
            organization_id=delivery.organization_id,
        )
        if title and title not in message:
            message = f"{message}\n{title}" if message.strip() else title
        body = json.dumps(
            {
                "phones": [str(p) for p in phones],
                "message": message,
                "event_type": delivery.event_type,
                "payload": delivery.payload or {},
            },
            ensure_ascii=False,
        ).encode("utf-8")

        headers = {"Content-Type": "application/json; charset=utf-8"}
        api_key = str(cfg.get("api_key") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        timeout = float(cfg.get("timeout_seconds") or 8)
        req = urlrequest.Request(url=url, data=body, headers=headers, method="POST")
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200))
            if code < 200 or code >= 300:
                raise RuntimeError(f"sms gateway non-2xx: {code}")
