---
title: Accounts and sign-in
description: Troubleshoot HyperFileLens Community console access, sign-in, language packs, and stale pages.
---

# Accounts and sign-in

## The console does not open

Open the full URL labeled `Tenant` in the installation output. If the console does not open:

1. Run `sudo /opt/hyperfilelens/install.sh status` on the installation host and confirm that the services are healthy.
2. Confirm that the address, port, and reverse-proxy configuration match the current deployment.
3. Confirm that the browser can reach the address and trusts its TLS certificate.

## Sign-in fails

- For the first sign-in, use the initial account and password shown by the installer.
- After changing the initial password, use the new value and check for leading or trailing spaces.
- Confirm that you opened the `Tenant` address rather than a system-administration address.
- Do not edit the database directly to recover an account. Have an administrator use the account-recovery method supported by the installed release.

## Simplified Chinese is unavailable

To check the Simplified Chinese language pack in the Community deployment, run:

```bash
sudo /opt/hyperfilelens/install.sh lang-pack list
```

Confirm that `zh-hans` is installed and compatible with the product version. Refresh the page and select Simplified Chinese from the language menu. If translations are missing after an upgrade, check the language-pack version.

## The page shows an older version

After an upgrade, a browser may continue to use older page assets. Reload when prompted. If the page is still stale, clear data for the site or open a new browser session. A problem limited to one browser normally does not require restarting the services.
