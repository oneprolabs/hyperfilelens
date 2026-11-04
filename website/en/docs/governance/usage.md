---
title: Usage and quotas
description: Review organization limits and usage, request higher quotas, and approve those requests in the Admin Console.
---

# Usage and quotas

The Usage page shows license status, quota limits, and how much the organization is using. Open <span class="hfl-path">Configuration → Governance → Usage</span> to review them. If you need a higher limit, submit a request for a platform administrator to approve in the Admin Console.

## View usage

On the Usage page, check:

- current use and the limit for each quota;
- whether any limit is close to full or already reached;
- request status under Request History (available when Enterprise quota increase is enabled).

When a quota is full, create or run actions that need that capacity may fail, with a prompt to open Usage or contact a platform administrator. Changing organization quotas directly is a platform task. Submitting a request from the organization does not give the requester permission to raise limits themselves.

## Submit a quota request

Members who can manage usage can submit a request:

1. On the Usage page, choose <span class="hfl-ui">Request Quota Increase</span> and select the quota to raise.
2. Enter the higher limit you need and explain why.
3. After you submit, track the request under <span class="hfl-ui">Request History</span>.
4. If that quota already has a pending request, wait until it is resolved before you submit another one.

Members who can only view usage can see limits and consumption, but cannot submit or cancel requests.

### Request status

| Status | Meaning |
| --- | --- |
| Pending | Submitted and waiting for platform review |
| Approved | Approved, and the new limit was applied successfully |
| Apply Failed | Approved, but the limit could not be applied; the organization quota is unchanged and needs platform follow-up |
| Rejected | Rejected; the limit is unchanged |
| Cancelled | Cancelled by the requester before approval |

Treat approval as successful only when the limit is actually applied. If the status is <span class="hfl-ui">Apply Failed</span>, the quota did not increase.

For a pending request, you can choose <span class="hfl-ui">Cancel</span>. After cancellation, the request leaves the review queue and the quota stays the same.

## Review quota requests on the platform

Platform administrators open <span class="hfl-path">Admin Console → Governance → Quota Requests</span>:

1. Filter pending requests by organization, status, or keyword.
2. Check the organization, quota, current limit, requested limit, and justification.
3. Choose <span class="hfl-ui">Approve</span> or <span class="hfl-ui">Reject</span> and add a decision note.
4. On approve, HyperFileLens applies the new limit through the normal organization quota path. If that fails, the status becomes <span class="hfl-ui">Apply Failed</span>—not a silent success.
5. The requester gets an in-app notification and can see the result under Request History on Usage.

Submit and approve stay separate: organization members who send requests do not get Admin Console quota permissions. Approval still follows instance capacity, license limits, and other existing constraints; it cannot bypass them.

Platform administrators can still change organization quotas directly on the related organization or subscription pages in the Admin Console, without using a request. That path is separate from request review.

## Day-to-day checks

- Keep critical quotas from sitting near their limits; check Usage before you add members, backups, or gateways.
- Make sure someone owns every pending request, and escalate any <span class="hfl-ui">Apply Failed</span> status to platform follow-up.
- For capacity or job problems, also check [Jobs, alerts, and audit logs](/docs/deployment/operations).
