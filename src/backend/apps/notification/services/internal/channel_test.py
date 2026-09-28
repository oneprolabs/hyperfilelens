"""Test-send through a notification channel."""

from __future__ import annotations


import socket
import ssl
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError

from django.core.mail import EmailMessage

from apps.notification.constants import ChannelType
from apps.notification.models import NotificationChannel
from apps.notification.channels import WebhookChannel
from apps.notification.exceptions import WebhookTestError
from apps.notification.models import NotificationDelivery


def _classify_webhook_error(exc: Exception) -> WebhookTestError:
    diagnostic = str(exc)
    reason = exc.reason if isinstance(exc, URLError) else exc
    reason_text = str(reason).lower()
    if isinstance(reason, ssl.SSLCertVerificationError) or "certificate verify failed" in reason_text:
        return WebhookTestError(
            "NOTIFICATION.WEBHOOK_TLS_FAILED",
            "The webhook HTTPS certificate could not be verified.",
            diagnostic,
        )
    if isinstance(exc, HTTPError):
        return WebhookTestError(
            "NOTIFICATION.WEBHOOK_HTTP_ERROR",
            f"The webhook endpoint returned HTTP {exc.code}.",
            diagnostic,
        )
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in reason_text:
        return WebhookTestError(
            "NOTIFICATION.WEBHOOK_TIMEOUT",
            "The webhook request timed out.",
            diagnostic,
        )
    return WebhookTestError(
        "NOTIFICATION.WEBHOOK_UNREACHABLE",
        "The webhook server could not be reached.",
        diagnostic,
    )


def test_channel(channel: NotificationChannel) -> dict:
    """Send a lightweight test message through a channel."""
    cfg = channel.config or {}
    channel_type = channel.channel_type

    if channel_type == ChannelType.EMAIL:
        recipients = cfg.get("to_emails") or cfg.get("to") or cfg.get("recipients") or []
        if isinstance(recipients, str):
            recipients = [x.strip() for x in recipients.split(",") if x.strip()]
        if not recipients:
            raise ValueError("Email channel requires to_emails.")

        subject = cfg.get("email_subject") or "[HyperFileLens] Notification channel test"
        smtp_host = cfg.get("smtp_host")
        smtp_port = cfg.get("smtp_port")

        from_email = cfg.get("from_email") or None
        if smtp_host and smtp_port:
            from django.core.mail import get_connection

            connection = get_connection(
                host=smtp_host,
                port=int(smtp_port),
                username=cfg.get("smtp_username") or None,
                password=cfg.get("smtp_password") or None,
                use_tls=cfg.get("use_tls", True),
                use_ssl=cfg.get("use_ssl", False),
            )
        else:
            from apps.notification.channels.email import _connection_from_platform_email

            connection, platform_cfg = _connection_from_platform_email()
            if not from_email:
                from_email = platform_cfg.get("from_email") or None

        email = EmailMessage(
            subject=subject,
            body="This is a test notification from HyperFileLens.",
            from_email=from_email,
            to=recipients,
            connection=connection,
        )
        email.send()
        return {"status": "success"}

    if channel_type in (ChannelType.WEBHOOK, ChannelType.DINGTALK, ChannelType.WECOM):
        url = (
            cfg.get("url")
            or cfg.get("webhook_url")
            or cfg.get("endpoint")
            or ""
        )
        if not str(url).strip():
            raise ValueError("Webhook URL is required.")
        parsed_url = urlparse(str(url).strip())
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise ValueError("Webhook URL must be a valid http:// or https:// URL.")
        test_cfg = {**cfg, "url": url}
        # A connection test should report reachability promptly. Production
        # delivery retries are intentionally not used for an interactive test.
        test_cfg["max_attempts"] = 1
        try:
            test_cfg["timeout_seconds"] = min(
                max(float(cfg.get("timeout_seconds") or 8), 1),
                10,
            )
        except (TypeError, ValueError):
            test_cfg["timeout_seconds"] = 8
        if channel_type in (ChannelType.DINGTALK, ChannelType.WECOM):
            test_cfg["webhook_platform"] = channel_type
        stub = NotificationDelivery(
            organization_id=channel.organization_id,
            channel=channel,
            event_type="channel.test",
            payload={"type": "test", "message": "HyperFileLens notification channel test"},
            status=NotificationDelivery.Status.PENDING,
        )
        patched = NotificationChannel(
            organization_id=channel.organization_id,
            name=channel.name,
            channel_type=ChannelType.WEBHOOK,
            config=test_cfg,
            is_active=channel.is_active,
        )
        try:
            WebhookChannel().send(channel=patched, delivery=stub)
        except Exception as exc:
            raise _classify_webhook_error(exc) from exc
        return {"status": "success"}

    return {"status": "skipped", "message": "Unsupported channel type."}
