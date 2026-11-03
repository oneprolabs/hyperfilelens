"""Organization alert emails — readable HTML + plain text for operators."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone
from django.utils.dateformat import format as date_format

from apps.alert.constants import ResourceType

BRAND_COLOR = "#6d28d9"
TAGLINE = "AI-Powered File Backup Insights."
_MAX_WHAT_HAPPENED = 500

_SEVERITY_LABELS = {
    "critical": "Critical",
    "warning": "Warning",
    "info": "Info",
}
_SEVERITY_COLORS = {
    "critical": ("#dc2626", "#fef2f2"),
    "warning": ("#d97706", "#fffbeb"),
    "info": ("#2563eb", "#eff6ff"),
}
_RESOLVED_COLORS = ("#059669", "#ecfdf5")

_RESOURCE_TYPE_LABELS = {choice.value: str(choice.label) for choice in ResourceType}
_TASK_TYPE_LABELS = {
    "backup": "Backup",
    "restore": "Restore",
    "repository_operation": "Repository Operation",
}


@dataclass
class AlertEmailModel:
    title: str
    severity: str
    resolved: bool
    organization_name: str = ""
    organization_key: str = ""
    rule_name: str = ""
    what_happened: str = ""
    detail_rows: list[tuple[str, str]] = field(default_factory=list)


def severity_label(severity: str | None) -> str:
    key = str(severity or "").strip().lower()
    return _SEVERITY_LABELS.get(key, (severity or "Alert").strip().title() or "Alert")


def status_label(*, resolved: bool) -> str:
    return "Resolved" if resolved else "Active"


def _severity_colors(severity: str | None, *, resolved: bool) -> tuple[str, str]:
    if resolved:
        return _RESOLVED_COLORS
    key = str(severity or "").strip().lower()
    return _SEVERITY_COLORS.get(key, (BRAND_COLOR, "#f5f3ff"))


def build_alert_email_subject(*, title: str, severity: str | None, resolved: bool) -> str:
    name = (title or "Alert").strip() or "Alert"
    if resolved:
        return f"Resolved: {name}"
    return f"{severity_label(severity)} alert: {name}"


def _format_when(value: datetime | None) -> str:
    if value is None:
        return ""
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    local = timezone.localtime(value)
    return date_format(local, "M j, Y, g:i A")


def _format_metric(value: Decimal | float | int | str | None, unit: str = "") -> str:
    if value is None or value == "":
        return ""
    text = str(value).rstrip("0").rstrip(".") if isinstance(value, Decimal) else str(value)
    unit = str(unit or "").strip()
    return f"{text}{unit}" if unit and not text.endswith(unit) else text


def _clip(text: str, limit: int = _MAX_WHAT_HAPPENED) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _add_row(rows: list[tuple[str, str]], label: str, value: str | None) -> None:
    text = str(value or "").strip()
    if text:
        rows.append((label, text))


def build_alert_email_model(
    *,
    title: str,
    severity: str | None,
    resolved: bool,
    organization_name: str = "",
    organization_key: str = "",
    rule_name: str = "",
    resource_type: str = "",
    resource_name: str = "",
    resource_id: str = "",
    message: str = "",
    metadata: dict | None = None,
    current_value=None,
    threshold_value=None,
    unit: str = "",
    first_triggered_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> AlertEmailModel:
    meta = metadata or {}
    alert_title = (title or rule_name or "Alert").strip() or "Alert"
    sev = str(severity or "").strip().lower()

    failure = str(meta.get("error_message") or "").strip() or str(message or "").strip()
    what_happened = _clip(failure)

    rows: list[tuple[str, str]] = []
    org_name = (organization_name or "").strip()
    org_key = (organization_key or "").strip()
    if org_name and org_key and org_name != org_key:
        _add_row(rows, "Organization", f"{org_name} ({org_key})")
    else:
        _add_row(rows, "Organization", org_name or org_key)

    _add_row(rows, "Rule", (rule_name or alert_title).strip())

    type_key = str(resource_type or meta.get("resource_type") or "").strip()
    type_label = _RESOURCE_TYPE_LABELS.get(type_key, type_key.replace("_", " ").title() if type_key else "")
    _add_row(rows, "Resource type", type_label)

    resource = (resource_name or "").strip() or (resource_id or "").strip()
    _add_row(rows, "Resource", resource)

    task_type = str(meta.get("task_type") or "").strip()
    task_uuid = str(meta.get("task_uuid") or "").strip()
    task_bits = []
    if task_type:
        task_bits.append(_TASK_TYPE_LABELS.get(task_type, task_type.replace("_", " ").title()))
    if task_uuid:
        task_bits.append(task_uuid)
    _add_row(rows, "Task", " · ".join(task_bits))

    current = _format_metric(current_value if current_value is not None else meta.get("value"), unit)
    threshold = _format_metric(threshold_value, unit)
    if current and threshold:
        _add_row(rows, "Current / threshold", f"{current} / {threshold}")
    elif current:
        _add_row(rows, "Current value", current)
    elif threshold:
        _add_row(rows, "Threshold", threshold)

    node_status = str(meta.get("node_status") or "").strip()
    _add_row(rows, "Node status", node_status.replace("_", " ").title() if node_status else "")

    _add_row(rows, "Started", _format_when(first_triggered_at))
    if resolved:
        _add_row(rows, "Resolved", _format_when(resolved_at) or _format_when(timezone.now()))

    return AlertEmailModel(
        title=alert_title,
        severity=sev or "warning",
        resolved=resolved,
        organization_name=org_name,
        organization_key=org_key,
        rule_name=(rule_name or alert_title).strip(),
        what_happened=what_happened,
        detail_rows=rows,
    )


def build_alert_email_bodies(model: AlertEmailModel) -> tuple[str, str]:
    """Return (plain_body, html_body)."""
    sev = severity_label(model.severity)
    status = status_label(resolved=model.resolved)
    org_display = model.organization_name or model.organization_key or "your organization"
    year = timezone.now().year
    footer = f"© {year} HyperFileLens. {TAGLINE}"
    accent, panel_bg = _severity_colors(model.severity, resolved=model.resolved)

    if model.resolved:
        intro_plain = f'The alert "{model.title}" in {org_display} was resolved.'
        intro_html = (
            f'The alert &ldquo;{html.escape(model.title)}&rdquo; in '
            f"<strong>{html.escape(org_display)}</strong> was resolved."
        )
        happened_heading = "Resolution summary"
    else:
        intro_plain = f"HyperFileLens detected a {sev.lower()} alert in {org_display}."
        intro_html = (
            f"HyperFileLens detected a {html.escape(sev.lower())} alert in "
            f"<strong>{html.escape(org_display)}</strong>."
        )
        happened_heading = "What happened"

    plain_lines = [
        "HyperFileLens",
        "",
        "Hello,",
        "",
        intro_plain,
        "",
        model.title,
        f"{sev} · {status}",
    ]
    if model.what_happened:
        plain_lines.extend(["", happened_heading, model.what_happened])
    if model.detail_rows:
        plain_lines.append("")
        plain_lines.append("Details")
        width = max(len(label) for label, _ in model.detail_rows)
        for label, value in model.detail_rows:
            plain_lines.append(f"{label.ljust(width)}  {value}")
    plain_lines.extend(
        [
            "",
            "Sign in to HyperFileLens to review this alert.",
            "",
            footer,
        ]
    )
    plain_body = "\n".join(plain_lines) + "\n"

    happened_html = ""
    if model.what_happened:
        happened_html = f"""\
  <div style="margin: 0 0 20px;">
    <div style="font-size: 12px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: #6b7280; margin: 0 0 8px;">
      {html.escape(happened_heading)}
    </div>
    <div style="background: {panel_bg}; border-left: 4px solid {accent}; padding: 12px 14px; border-radius: 4px; line-height: 1.55; color: #111827;">
      {html.escape(model.what_happened)}
    </div>
  </div>"""

    detail_rows_html = "".join(
        f"""\
    <tr>
      <td style="padding: 8px 16px 8px 0; color: #6b7280; vertical-align: top; white-space: nowrap; width: 1%;">{html.escape(label)}</td>
      <td style="padding: 8px 0; color: #111827; vertical-align: top; word-break: break-word;">{html.escape(value)}</td>
    </tr>"""
        for label, value in model.detail_rows
    )
    details_html = ""
    if detail_rows_html:
        details_html = f"""\
  <div style="margin: 0 0 20px;">
    <div style="font-size: 12px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: #6b7280; margin: 0 0 8px;">
      Details
    </div>
    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
      {detail_rows_html}
    </table>
  </div>"""

    html_body = f"""\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto; color: #111827;">
  <div style="font-size: 18px; font-weight: 700; color: {BRAND_COLOR}; margin: 0 0 20px;">HyperFileLens</div>
  <p style="margin: 0 0 8px;">Hello,</p>
  <p style="margin: 0 0 20px; line-height: 1.55; color: #374151;">{intro_html}</p>
  <div style="border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; margin: 0 0 20px;">
    <div style="height: 4px; background: {accent};"></div>
    <div style="padding: 16px 18px;">
      <div style="font-size: 18px; font-weight: 700; color: #111827; margin: 0 0 8px; line-height: 1.35;">
        {html.escape(model.title)}
      </div>
      <div>
        <span style="display: inline-block; background: {panel_bg}; color: {accent}; font-size: 12px; font-weight: 600; padding: 4px 8px; border-radius: 999px; margin-right: 6px;">
          {html.escape(sev)}
        </span>
        <span style="display: inline-block; background: #f3f4f6; color: #4b5563; font-size: 12px; font-weight: 600; padding: 4px 8px; border-radius: 999px;">
          {html.escape(status)}
        </span>
      </div>
    </div>
  </div>
  {happened_html}
  {details_html}
  <p style="margin: 0 0 20px; line-height: 1.55; color: #374151;">
    Sign in to HyperFileLens to review this alert.
  </p>
  <hr style="border: none; border-top: 1px solid #eee; margin: 0 0 16px;">
  <p style="font-size: 12px; color: #9ca3af; text-align: center; margin: 0;">
    {html.escape(footer)}
  </p>
</div>"""
    return plain_body, html_body


def send_organization_alert_email(
    *,
    recipient: str,
    model: AlertEmailModel | None = None,
    connection_cfg: dict[str, Any] | None = None,
    **legacy_kwargs: Any,
) -> None:
    """Send an alert email with HTML and plain-text alternatives."""
    from apps.configuration.services.runtime_settings import email_connection_kwargs

    if model is None:
        model = build_alert_email_model(
            title=str(legacy_kwargs.get("title") or "Alert"),
            severity=legacy_kwargs.get("severity"),
            resolved=bool(legacy_kwargs.get("resolved")),
            organization_name=str(legacy_kwargs.get("organization_name") or ""),
            organization_key=str(legacy_kwargs.get("organization_key") or ""),
            rule_name=str(legacy_kwargs.get("rule_name") or ""),
            resource_type=str(legacy_kwargs.get("resource_type") or ""),
            resource_name=str(legacy_kwargs.get("resource_name") or ""),
            resource_id=str(legacy_kwargs.get("resource_id") or ""),
            message=str(legacy_kwargs.get("message") or ""),
            metadata=legacy_kwargs.get("metadata") or {},
            current_value=legacy_kwargs.get("current_value"),
            threshold_value=legacy_kwargs.get("threshold_value"),
            unit=str(legacy_kwargs.get("unit") or ""),
            first_triggered_at=legacy_kwargs.get("first_triggered_at"),
            resolved_at=legacy_kwargs.get("resolved_at"),
        )

    cfg = connection_cfg or email_connection_kwargs()
    subject = build_alert_email_subject(
        title=model.title,
        severity=model.severity,
        resolved=model.resolved,
    )
    plain_body, html_body = build_alert_email_bodies(model)
    connection = get_connection(
        backend=cfg["backend"],
        host=cfg["host"] or None,
        port=cfg["port"] or None,
        username=cfg["username"] or None,
        password=cfg["password"] or None,
        use_tls=cfg["use_tls"],
        use_ssl=cfg["use_ssl"],
    )
    message_obj = EmailMultiAlternatives(
        subject=subject,
        body=plain_body,
        from_email=cfg.get("from_email") or None,
        to=[recipient],
        connection=connection,
    )
    message_obj.attach_alternative(html_body, "text/html")
    sent = message_obj.send(fail_silently=False)
    if sent <= 0:
        raise RuntimeError("send_mail returned 0")
