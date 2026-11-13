---
title: Roles and permissions
description: Use Enterprise default roles or custom roles to control what organization members can do.
---

# Roles and permissions

A role controls which organization actions a member can see or perform. Open <span class="hfl-path">Configuration → Organization Management → Roles</span> to review default and custom roles, and restore defaults when needed.

## Owner vs roles

- **Owner** is the organization identity for the account that holds primary responsibility. It is not a deletable preset in the Roles list.
- **Role** is a permission set you assign to members. New organizations get product default roles that you can use right away.

Creating an organization or registering a user does not automatically grant elevated roles to ordinary users. Access still comes from membership plus the roles you assign.

## Default roles

Enterprise organizations include these defaults:

| Role | Typical use |
| --- | --- |
| **Administrator** | Manage members, Protection, Insights, and operations across the organization, with business resource scope configured separately at assignment |
| **Manager** | Manage organization governance; business resource access follows assignment scope and group grants |
| **Operator** | Run Protection, use Insights, and view operations within the organization-assignment scope and group grants |
| **Auditor** | Read governance, Protection, and operations information within assigned resource scopes without changing configuration |

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
2. Review the preview of roles that will be created or restored. Restoring an existing preset resets its name, description, permissions, and active state to the product defaults, including any edits you made to that preset.
3. Confirm and run the restore.

Restoring defaults does not turn Owner into a normal role. Deleted presets come back only through this restore action—organization setup does not bring them back on its own.

Separately created custom roles and member assignments are not changed. However, restoring a preset changes the effective permissions of members and groups already assigned to it. Review those assignments before confirming, especially if you previously reduced that preset’s permissions.

## Day-to-day checks

- The default roles are present, or you have restored them.
- Member roles match their jobs; use [Groups](/docs/governance/groups) when people should only see some resources.
- Menus and pages follow each user’s effective permissions. If access is denied, check the member’s role and group membership first, then ask an organization administrator to adjust them.

## Roles and business resource scopes

Roles define operations, not an all-resources scope switch. In Members, assign an organization Role and choose **Owned Resources** or **All Organization Resources** for that assignment. New members default to owned resources. Group assignments always apply only to that Group’s resources.

This scope limits business-resource operations from that assignment. It does not restrict organization-management permissions, including ownership transfers, or expand another Group’s grants. Restoring default Roles does not reset existing assignment scopes.
