---
title: Jobs, alerts, and audit logs
description: Review operational health, jobs, alerts, notifications, and audit records.
---

# Jobs, alerts, and audit logs

<p class="hfl-doc-lead">Use operational health and alerts to identify problems, then open the relevant job to locate the failed stage. Audit logs show who made an important change, when it happened, and whether it succeeded.</p>

## Check operational health

Open <span class="hfl-path">Operations → Operational Health</span> to review active alerts, offline components, backup source issues, and recent failed jobs. Start with the affected resource or job details; host logs are not normally the first troubleshooting step.

## Review jobs

Open <span class="hfl-path">Operations → Task List</span> and filter backup, restore, component maintenance, and related jobs by name, status, type, or time.

For an abnormal job, review its status, failed step, related resources, and error message in that order. For a partially successful job, also identify the scope that did not finish. Use <span class="hfl-ui">Cancel</span> or <span class="hfl-ui">Retry</span> only when the product offers that action for the job.

## Alerts and notifications

Open <span class="hfl-path">Operations → Alerts</span> to review current and historical alerts:

- **Alerts:** Review severity, affected resources, first occurrence, and current status. Acknowledging an alert means someone is investigating it; it does not mean the problem is resolved. Mark it resolved only after the resource recovers.
- **Alert rules:** Select the resources, conditions, and severity to monitor.
- **Notification channels:** Configure how alert notifications are delivered.
- **Delivery history:** Confirm whether a notification was sent and review failures.

After resolving an alert, check the resource again and confirm that subsequent jobs complete normally.

## Default alert rules

Every new organization starts with a set of default alert rules for common task failures and unavailable components. Open <span class="hfl-path">Operations → Alerts → Alert Rules</span> to review them—you do not need to create these rules yourself before monitoring begins.

### Organization defaults

| Rule | Type | Severity | Trigger |
| --- | --- | --- | --- |
| Backup Task Failed | Task | Critical | Fires when a backup task fails. By default, notifications can repeat every hour. |
| Backup Task Timed Out | Task | Critical | Fires when a backup task times out. By default, notifications can repeat every hour. |
| Restore Task Failed | Task | Critical | Fires when a restore task fails. By default, notifications can repeat every hour. |
| Restore Task Timed Out | Task | Critical | Fires when a restore task times out. By default, notifications can repeat every hour. |
| Source Host Unavailable | Health | Critical | Fires when a Source Host stops reporting heartbeats for more than 300 seconds. Also notifies on recovery. |
| Proxy Host Unavailable | Health | Critical | Fires when a Proxy Host stops reporting heartbeats for more than 300 seconds. Also notifies on recovery. |
| Data Gateway Unavailable | Health | Critical | Fires when a Data Gateway stops reporting heartbeats for more than 300 seconds. Also notifies on recovery. |

The rules are on by default and cover all matching resources in the organization. Administrators can change the conditions or severity, turn a rule off, or restore any missing defaults from the product. Creating an organization again or upgrading the product does not add duplicate copies of the same default rule.

### Control-plane defaults (Platform Ops)

If Platform Ops is enabled, the control plane also includes default rules for its own resource usage. These monitor the control plane host, not organization backup resources:

| Rule | Type | Severity | Trigger |
| --- | --- | --- | --- |
| Control Plane CPU Usage High | Metric | Warning | CPU stays at or above 90% for about 10 minutes |
| Control Plane Memory Usage High | Metric | Critical | Memory stays at or above 90% for about 10 minutes |
| Control Plane Disk Usage High | Metric | Critical | Disk stays at or above 90% for about 10 minutes |

Review and change them in Platform Ops under alert rules. They are typically not shown in a normal organization workspace.

### Notifications

- Organization alerts go out as in-app notifications first, so you can receive them without setting up an external channel.
- Email, webhooks, and other external channels need separate configuration. Do not expect external delivery until those channels are in place.
- If email delivery is configured and a rule is not linked to another channel, organization default alerts may also email the organization owner. Confirm the exact behavior in your deployment.

For members, roles, and quotas, see [Organization Management](/docs/governance/).

## Review audit logs

Open <span class="hfl-path">Operations → Audit Logs</span> and filter important actions by user, action type, resource, result, or time. Export the filtered records when they need to be retained or analyzed elsewhere.

## Continue troubleshooting

If the console does not provide enough information, record the affected resource, task ID, error, and time, then use the [Troubleshooting Guide](/docs/troubleshooting/). Collect control-plane or component logs only when a troubleshooting step specifically requires them.
