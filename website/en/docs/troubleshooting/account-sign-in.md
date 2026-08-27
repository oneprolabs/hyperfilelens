---
title: Accounts and Sign-in
description: Troubleshoot console access, SaaS or Community sign-in, language packs, and stale pages.
---

# Accounts and Sign-in

## The console does not open

For the official SaaS, open the [HyperFileLens SaaS console](https://app.hyperfilelens.com/). For Community, open the complete address marked `Tenant` in the installation result.

If a Community console does not open:

1. Run `sudo /opt/hyperfilelens/install.sh status` on the installation host and confirm that the services are healthy.
2. Confirm that the address, port, and reverse-proxy configuration match the current deployment.
3. Confirm that the browser can reach the address and trusts its TLS certificate.

## Official SaaS sign-in fails

- New users should use Google sign-in and select the Google account they want to use with HyperFileLens.
- Existing users who already have email-and-password access can continue to use it.
- If Google redirects back without signing you in, reopen the console and check whether the browser blocked the redirect or required cookies.
- If sign-in succeeds but the organization is unavailable, confirm that you used the correct account and ask an organization administrator to check your membership.

## Community sign-in fails

- For the first sign-in, use the initial account and password shown by the installer.
- After changing the initial password, use the new value and check for leading or trailing spaces.
- Confirm that you opened the `Tenant` address rather than a system-administration address.
- Do not edit the database directly to recover an account. Have an administrator use the account-recovery method supported by the installed release.

## Simplified Chinese is unavailable

Check the Simplified Chinese language pack in Community:

```bash
sudo /opt/hyperfilelens/install.sh lang-pack list
```

Confirm that `zh-hans` is installed and compatible with the product version. Refresh the page and select Simplified Chinese from the language menu. If translations are missing after an upgrade, check the language-pack version.

## The page shows an older version

After an upgrade, a browser may continue to use older page assets. Reload when prompted. If the page is still stale, clear data for the site or open a new browser session. A problem limited to one browser normally does not require restarting the services.
