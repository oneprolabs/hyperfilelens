---
title: Groups
description: Use groups to limit collaboration to a shared set of members and resources.
---

# Groups

Groups let teams share a clear boundary inside an organization. Put the right members and resources in a group, then use group roles to control what those members can do. Open <span class="hfl-path">Configuration → Governance → Groups</span> to manage them.

Organization roles grant organization permissions, with business resource access following ownership unless the role allows access to all organization resources. A group role can independently grant supported actions on resources in that group. These grants are combined; the organization role is not an upper limit on group permissions.

## When to use groups

- Several teams share one organization, but each team should only work with its own backup sources, repositories, or gateways.
- Operators need access to some resources without becoming organization administrators.
- Auditors need read-only access to one set of resources.

## Create and maintain a group

1. Create a group and give it a name and description.
2. Add members and choose a role from the available role catalog. Only permissions supported in the group context apply to that assignment.
3. Add the resources that belong in the group, or set the accessible scope with the options on the page.
4. Sign in as someone in the group and confirm they can see the expected resources—and nothing outside that scope.

Group permissions do not grant organization-wide administration. A role assigned in a group may allow management of that group’s members or resources, but cannot grant organization role management, usage-limit changes, or platform administration through that assignment.

For example, a member with an organization-level Auditor role can still gain write access to a group’s resources through a group role that grants those actions. To keep the member read-only, check both the role assigned in [Members](/docs/governance/members) and every group assignment; an organization-level read-only role does not override a group write grant.

## Day-to-day checks

- Group descriptions match real team boundaries. Avoid one catch-all group that removes isolation.
- After transfers or departures, update membership and group roles quickly.
- On [Resources](/docs/governance/resources), confirm ownership and group links. After a resource leaves a group, former members should no longer reach it through that group.
- Before you delete a group, make sure no critical backup or restore workflow depends on it.
