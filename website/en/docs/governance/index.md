---
title: Governance overview
description: Manage organization members, roles, groups, and usage quotas, and see where platform approval happens.
---

# Governance overview

Governance is where you manage who belongs to an organization, what they can do, which resources they can reach, and how much capacity the organization can use. Day-to-day work lives under <span class="hfl-path">Configuration → Governance</span>. Raising instance-level limits and approving quota requests is done by platform administrators in the Admin Console.

Enterprise includes the full set of organization governance features: default roles, groups, the resource directory, usage and quota requests, and platform approval. If those menus are missing in a Community deployment, the feature is not enabled there.

## Day-to-day workflow

1. Open [Organization](/docs/governance/organization) and confirm you are in the right workspace.
2. Review the default roles in [Roles and permissions](/docs/governance/roles), then assign them in [Members](/docs/governance/members).
3. If teams need access to only some resources, set up [Groups](/docs/governance/groups) and check ownership on [Resources](/docs/governance/resources).
4. Watch limits on [Usage and quotas](/docs/governance/usage). If you need more capacity, submit a request for platform approval.

**Owner** is an organization identity, not a role you create or delete in the Roles list. New users do not get elevated roles automatically. Access still depends on membership and the roles you assign.

## Organization vs platform

| Scope | Where to work | What it covers |
| --- | --- | --- |
| Organization | <span class="hfl-path">Configuration → Governance</span> | Organization details, members, groups, roles, resources, usage, and requests |
| Platform | <span class="hfl-path">Admin Console → Governance</span> | Organizations, platform roles, subscription plans, organization quotas, and [quota request review](/docs/governance/usage#review-quota-requests-on-the-platform) |

Submitting a quota request does not give someone permission to change quotas. Only platform administrators who can manage organization quotas can approve a request and apply the new limit.

For failed jobs, offline nodes, and similar runtime issues, see [Jobs, alerts, and audit logs](/docs/deployment/operations). Alert rules and notification channels belong under Operations, not Governance.
