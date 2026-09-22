from __future__ import annotations

import json

from django.core.mail import EmailMessage, get_connection

from apps.notification.channels.base import BaseChannel
from apps.notification.exceptions import ChannelConfigError
from apps.notification.models import NotificationChannel, NotificationDelivery


def _connection_from_platform_email():
    """Django mail connection using Runtime > Deployment > Default SMTP."""
    from apps.configuration.services.runtime_settings import email_connection_kwargs

    cfg = email_connection_kwargs()
    connection = get_connection(
        backend=cfg["backend"],
        host=cfg["host"] or None,
        port=cfg["port"] or None,
        username=cfg["username"] or None,
        password=cfg["password"] or None,
        use_tls=cfg["use_tls"],
        use_ssl=cfg["use_ssl"],
    )
    return connection, cfg


class EmailChannel(BaseChannel):
    def send(self, *, channel: NotificationChannel, delivery: NotificationDelivery) -> None:
        cfg = channel.config or {}
        to = cfg.get("to_emails") or cfg.get("to") or cfg.get("recipients") or []
        if isinstance(to, str):
            to = [x.strip() for x in to.split(",") if x.strip()]
        if not isinstance(to, list) or not to:
            raise ChannelConfigError("email channel missing config.to recipients")

        subject = str(
            cfg.get("email_subject") or cfg.get("subject") or f"[HyperFileLens] {delivery.event_type}"
        ).strip()
        body = cfg.get("body")
        if not body:
            body = json.dumps(
                {
                    "event_type": delivery.event_type,
                    "delivery_id": delivery.id,
                    "organization_id": delivery.organization_id,
                    "payload": delivery.payload or {},
                },
                ensure_ascii=False,
                indent=2,
            )
        from_email = str(cfg.get("from_email") or "").strip() or None

        smtp_host = cfg.get("smtp_host")
        smtp_port = cfg.get("smtp_port")
        if smtp_host and smtp_port:
            connection = get_connection(
                host=str(smtp_host),
                port=int(smtp_port),
                username=cfg.get("smtp_username") or None,
                password=cfg.get("smtp_password") or None,
                use_tls=cfg.get("use_tls", True),
                use_ssl=cfg.get("use_ssl", False),
            )
        else:
            connection, platform_cfg = _connection_from_platform_email()
            if not from_email:
                from_email = platform_cfg.get("from_email") or None

        sent = EmailMessage(
            subject=subject,
            body=str(body),
            from_email=from_email,
            to=[str(x) for x in to],
            connection=connection,
        ).send()
        if sent <= 0:
            raise RuntimeError("send_mail returned 0")
