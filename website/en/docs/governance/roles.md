---
title: Roles and permissions
description: Use Enterprise default roles or custom roles to control what organization members can do.
---

# Roles and permissions

A role controls which organization actions a member can see or perform. Open <span class="hfl-path">Configuration → Governance → Roles</span> to review default and custom roles, and restore defaults when needed.

## Owner vs roles

- **Owner** is the organization identity for the account that holds primary responsibility. It is not a deletable preset in the Roles list.
- **Role** is a permission set you assign to members. New organizations get product default roles that you can use right away.

Creating an organization or registering a user does not automatically grant elevated roles to ordinary users. Access still comes from membership plus the roles you assign.

## Default roles

Enterprise organizations include these defaults:

| Role | Typical use |
| --- | --- |
| **Administrator** | Manage members, Protection, Insights, and operations across the organization, with access to all organization resources |
| **Manager** | Manage organization governance; business resource access follows ownership and group grants |
| **Operator** | Run Protection, use Insights, and view operations on owned resources and resources granted through groups |
| **Auditor** | Read governance, Protection, and operations information for owned or group-authorized resources without changing configuration |

In [Members](/docs/governance/members), assign the default role that fits the person’s job. You do not need to design a permission set from scratch to get started.

The table describes each role’s own grants, not a restriction on other assignments. Group roles can independently add supported actions on their group’s resources. An organization-level Auditor role therefore does not keep a member read-only if a group role grants write access; review both kinds of assignment in [Groups](/docs/governance/groups).

## Custom roles

When none of the defaults fit:

1. Create a custom role and enter a name and description.
2. Select the permissions you need—for example backup configuration only, or alert viewing only.
3. Save the role, then assign it in Members.
4. Sign in as that user and confirm the pages and actions look right.

Review responsibilities before you grant management permissions. Being able to manage members or roles does not automatically unlock the Admin Console.

## Restore default roles

If a default role is missing or was deleted:

1. On the Roles page, choose <span class="hfl-ui">Restore Default Roles</span>.
2. Review the preview of roles that will be created or restored. Custom roles stay as they are, and member assignments do not change.
3. Confirm and run the restore.

Restoring defaults does not turn Owner into a normal role. Deleted presets come back only through this restore action—organization setup does not bring them back on its own.

## Day-to-day checks

- The default roles are present, or you have restored them.
- Member roles match their jobs; use [Groups](/docs/governance/groups) when people should only see some resources.
- Menus and pages follow each user’s effective permissions. If access is denied, check the member’s role and group membership first, then ask an organization administrator to adjust them.
