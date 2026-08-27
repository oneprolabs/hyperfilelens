---
title: Jobs, Alerts, and Audit Logs
description: Review operational health, jobs, alerts, notifications, and audit records.
---

# Jobs, Alerts, and Audit Logs

<p class="hfl-doc-lead">Use operational health and alerts to identify problems, then open the relevant job to locate the failed stage. Audit logs show who made an important change, when it happened, and whether it succeeded.</p>

## Check operational health

Open <span class="hfl-path">Operations → Operational Health</span> to review active alerts, offline components, backup-source issues, and recent failed jobs. Start with the affected resource or job details; host logs are not normally the first troubleshooting step.

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

## Review audit logs

Open <span class="hfl-path">Operations → Audit Logs</span> and filter important actions by user, action type, resource, result, or time. Export the filtered records when they need to be retained or analyzed elsewhere.

## Continue troubleshooting

If the console does not provide enough information, record the affected resource, task ID, error, and time, then use the [Troubleshooting Guide](/en/docs/troubleshooting/). Collect control-plane or component logs only when a troubleshooting step specifically requires them.
