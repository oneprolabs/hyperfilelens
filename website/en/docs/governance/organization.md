---
title: Organization
description: View and update the current organization’s basic details in Configuration.
---

# Organization

An organization is the workspace for members, backup resources, jobs, and business data. Open <span class="hfl-path">Configuration → Governance → Organization</span> and make sure you are working in the right organization.

## View organization details

On the Organization page, check:

- the organization name and other identifying details;
- how the signed-in account relates to the organization (for example, whether it is the Owner);
- any status or limit notes shown on the page.

Use the organization switcher at the top of the console to move between organizations. After you switch, Governance, Protection, Insights, and Operations all show data for the selected organization.

## Update organization details

Members who can manage the organization can edit the fields the page allows. Before you save, make sure names and other visible details match your naming standards.

Do not use this page to manage instance installation or licensing. Instance licenses and capacity pools are managed in the Admin Console under platform governance.

## Before you disable or delete an organization

Platform administrators usually disable or delete organizations under <span class="hfl-path">Admin Console → Governance → Organizations</span>. Those actions affect who can sign in and which resources stay visible, so do them only when the business need is clear and you have reviewed the impact on data and permissions.

The installation administrator is not the same as the organization Owner or an assigned Administrator role. In the current Enterprise authorization model, the installation administrator has independent platform-level authority that grants all organization permissions in active organizations, without requiring organization membership or a tenant role assignment. Removing a membership or reducing an organization role does not revoke that authority.

For ordinary organization members, access depends on active membership and the applicable organization role and group grants. Review these in [Members](/docs/governance/members), [Roles and permissions](/docs/governance/roles), and [Groups](/docs/governance/groups).
