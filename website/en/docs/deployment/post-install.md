---
title: Post-installation Checks
description: Confirm that HyperFileLens Community is running and ready for first use.
---

# Post-installation Checks

After installation, verify the services before signing in and checking the main product areas.

## 1. Check service status

On the Community host, run:

```bash
sudo /opt/hyperfilelens/install.sh status
```

Confirm that the services report a normal state and record the installed version. If a service is unhealthy, see [Installation and Nodes](/en/docs/troubleshooting/installation-nodes).

## 2. Sign in to the console

1. Find the complete address marked `Tenant` in the installation result and open it in a browser.
2. Sign in with the initial account shown by the installer.
3. Change the initial password after signing in for the first time.

## 3. Check the product areas

Confirm that the top navigation includes the following areas and that each one opens correctly:

- **Overview:** Review backup sources, target storage, restore tests, running jobs, and items that need attention.
- **Protection:** Open backup sources, target storage, backup configurations, and restore workflows.
- **Insights:** Open the AI-powered Insights workspace.
- **Configuration:** Open organization information, member roles, and system settings.
- **Operations:** Open operational health, alerts, jobs, and audit information.

If a service or page does not open, record the page message and product version, then see [Installation and Nodes](/en/docs/troubleshooting/installation-nodes).
