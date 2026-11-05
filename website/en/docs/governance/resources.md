---
title: Resources
description: Review the organization resource directory, ownership, and group access.
---

# Resources

The Resources page lists the organization’s backup sources, repositories, gateways, and similar objects, along with who owns them. Open <span class="hfl-path">Configuration → Governance → Resources</span> to confirm ownership and access match how your teams actually work.

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
- **Organization roles** grant actions according to ownership, or across the organization when the role permits access to all resources. Group roles can independently add supported actions on that group’s resources; effective access combines the applicable grants rather than intersecting organization and group permissions.

## Day-to-day checks

1. After you add a backup source, target storage, or gateway, confirm it appears here with the right owner.
2. Before you transfer or disable a member, review what they own and hand it off.
3. After you change a group’s resources, sign in as an operator and confirm visibility.

This directory supports access and ownership. It does not replace connection checks or job results on Protection pages. Before you delete a resource or break an association, make sure no backup, restore, or Insights preparation is still using it.
