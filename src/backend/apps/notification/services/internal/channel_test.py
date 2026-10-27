"""Test-send through a notification channel."""

from __future__ import annotations


import smtplib
import socket
import ssl
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError

from django.core.mail import EmailMessage

from apps.notification.constants import ChannelType
from apps.notification.models import NotificationChannel
from apps.notification.channels import WebhookChannel
from apps.notification.exceptions import (
    DingTalkDeliveryError,
    EmailTestError,
    FeishuDeliveryError,
    WebhookTestError,
)
from apps.notification.models import NotificationDelivery

# Interactive connection tests should fail fast; production delivery may wait longer.
_EMAIL_TEST_TIMEOUT_SECONDS = 10


def _classify_webhook_error(exc: Exception) -> WebhookTestError:
    diagnostic = str(exc)
    reason = exc.reason if isinstance(exc, URLError) else exc
    reason_text = str(reason).lower()
    if isinstance(exc, FeishuDeliveryError):
        return WebhookTestError(
            "NOTIFICATION.FEISHU_REJECTED",
            "Feishu did not accept the notification.",
            diagnostic,
        )
    if isinstance(exc, DingTalkDeliveryError):
        return WebhookTestError(
            "NOTIFICATION.DINGTALK_REJECTED",
            "DingTalk rejected the notification.",
            diagnostic,
        )
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


def _classify_email_error(exc: Exception) -> EmailTestError:
    diagnostic = str(exc)
    text = diagnostic.lower()
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in text:
        return EmailTestError(
            "NOTIFICATION.EMAIL_TIMEOUT",
            "The SMTP connection timed out.",
            diagnostic,
        )
    if isinstance(
        exc,
        (
            ConnectionError,
            OSError,
            smtplib.SMTPConnectError,
            smtplib.SMTPServerDisconnected,
        ),
    ) or any(token in text for token in ("connection refused", "name or service not known", "nodename nor servname", "temporary failure in name resolution")):
        return EmailTestError(
            "NOTIFICATION.EMAIL_UNREACHABLE",
            "The SMTP server could not be reached.",
            diagnostic,
        )
    if isinstance(exc, smtplib.SMTPAuthenticationError) or "auth" in text:
        return EmailTestError(
            "NOTIFICATION.EMAIL_AUTH_FAILED",
            "SMTP authentication failed.",
            diagnostic,
        )
    return EmailTestError(
        "NOTIFICATION.EMAIL_SEND_FAILED",
        "The test email could not be sent.",
        diagnostic,
    )


def _email_connection(*, cfg: dict, timeout: float):
    from django.core.mail import get_connection

    smtp_host = cfg.get("smtp_host")
    smtp_port = cfg.get("smtp_port")
    if smtp_host and smtp_port:
        return get_connection(
            host=smtp_host,
            port=int(smtp_port),
            username=cfg.get("smtp_username") or None,
            password=cfg.get("smtp_password") or None,
            use_tls=cfg.get("use_tls", True),
            use_ssl=cfg.get("use_ssl", False),
            timeout=timeout,
        ), cfg.get("from_email") or None

    from apps.configuration.services.runtime_settings import email_connection_kwargs

    platform_cfg = email_connection_kwargs()
    connection = get_connection(
        backend=platform_cfg["backend"],
        host=platform_cfg["host"] or None,
        port=platform_cfg["port"] or None,
        username=platform_cfg["username"] or None,
        password=platform_cfg["password"] or None,
        use_tls=platform_cfg["use_tls"],
        use_ssl=platform_cfg["use_ssl"],
        timeout=timeout,
    )
    from_email = cfg.get("from_email") or platform_cfg.get("from_email") or None
    return connection, from_email


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
        connection, from_email = _email_connection(cfg=cfg, timeout=_EMAIL_TEST_TIMEOUT_SECONDS)
        email = EmailMessage(
            subject=subject,
            body="This is a test notification from HyperFileLens.",
            from_email=from_email,
            to=recipients,
            connection=connection,
        )
        try:
            email.send()
        except Exception as exc:  # noqa: BLE001 — classify for interactive test UX
            raise _classify_email_error(exc) from exc
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
            owner_scope=channel.owner_scope,
            organization_id=channel.organization_id,
            channel=channel,
            event_type="channel.test",
            payload={"type": "test", "message": "HyperFileLens notification channel test"},
            status=NotificationDelivery.Status.PENDING,
        )
        patched = NotificationChannel(
            owner_scope=channel.owner_scope,
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
