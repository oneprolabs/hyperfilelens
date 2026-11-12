---
title: Resources
description: Review the organization resource directory, ownership, and group access.
---

# Resources

The Resources page lists the organization’s backup sources, repositories, gateways, and similar objects, along with who owns them. Open <span class="hfl-path">Configuration → Organization Management → Resources</span> to confirm ownership and access match how your teams actually work.

## View the resource directory

The list typically includes:

- name and type (follow the types shown on the page);
- owner;
- group or other access details;
- status or last update time.

Filter by name, type, or owner to find resources that still belong to someone who has left.

## Ownership and access

- **Owner** is the account responsible for the resource. When responsibility changes, update ownership so items are not left on inactive accounts.
- **Group access:** After you add a resource to a [group](/docs/governance/groups), group members can reach it according to their group role. After you remove it from the group, that path no longer applies.
- **Organization roles** define actions. Each organization Role assignment separately selects owned resources or all organization resources for business access. Group roles can independently add supported actions on that group’s resources; effective access combines the applicable grants rather than intersecting organization and group permissions.

## Change Owner

Change Owner is an organization-management permission, not an ordinary business-resource operation. An explicitly authorized member can transfer eligible resources between eligible members of the same organization, even when their business resource scope is owned resources. Owning a resource or managing its Group does not grant this permission.

Single and bulk transfers use the same authorization rules. Transfers preserve creator history, Group assignments, resource configuration, data, and running tasks. Ownership changes do not add Role permissions. Organization membership, resource eligibility, revision conflicts, and audit requirements are enforced by the backend.

## Day-to-day checks

1. After you add a backup source, target storage, or gateway, confirm it appears here with the right owner.
2. Before you transfer or disable a member, review what they own and hand it off.
3. After you change a group’s resources, sign in as an operator and confirm visibility.

This directory supports access and ownership. It does not replace connection checks or job results on Protection pages. Before you delete a resource or break an association, make sure no backup, restore, or Insights preparation is still using it.

## Default-private resources

With an owned-resource assignment and no Group grants, members can access only their currently owned business resources, including Tags. Tag names are unique within the current owner, not across the organization. Group sharing is explicit. A Restore Plan with a direct owner does not inherit another member’s ownership access from its Backup Configuration; unassigned plans retain the documented fallback.

Events, alerts, tasks, restore records and delivery history follow the referenced business resource. Owning a shared executor or notification channel does not expose other members’ data. Personal Chats and Request History remain user-bound. Resource notifications are checked against current grants, including old inbox messages.
