---
title: Members
description: Manage who belongs to the organization and assign the roles that control what they can do.
---

# Members

A member is a user who can enter the current organization and use the features they are allowed to use. Open <span class="hfl-path">Configuration → Governance → Members</span> to review the list, enable or disable members, and assign roles.

## View members

The list typically shows the account, status, role, and recent updates. Filter by name or status to confirm that new members look correct. You need member view permission to open the list.

## Add and enable members

1. Add or invite the user with the flow the product provides.
2. Make sure the member is enabled before you assign a role.
3. New members do not get Administrator or other elevated roles automatically. Until you assign a role, they cannot take actions that require role permissions.

Inviting, enabling, disabling, removing, or changing roles requires the matching member-management or role-assignment permission. How you add people depends on whether the deployment allows email sign-up, external identity providers, or similar options. If external sign-in or email delivery needs extra setup, configure that in the Admin Console.

## Assign roles

1. Open the member.
2. Choose a role that matches their job from [Roles and permissions](/docs/governance/roles).
3. After you save, ask them to reopen the pages they need—or sign in again—so their permissions refresh.

Enterprise ships with default roles you can assign immediately: Administrator, Manager, Operator, and Auditor. You can also create custom roles. If a team should only see some resources, pair the organization role with [Groups](/docs/governance/groups).

## Disable and remove

- **Disable** keeps the membership but blocks access for now. Use it during handoff or when an account needs investigation.
- **Remove** takes the user out of the organization. Afterward, do not rely on old sessions or bookmarks, and check resources they still own plus any group memberships.

Before you disable or remove someone, make sure another owner can take over their backup configurations, restore plans, or Insights sessions.

## Day-to-day checks

- New members are enabled and have the right role.
- After a transfer or departure, disable or remove them promptly and check ownership on [Resources](/docs/governance/resources).
- If member count counts against organization usage, confirm the limit on [Usage and quotas](/docs/governance/usage).
