---
title: Backup, Storage, and Restore
description: Troubleshoot folder browsing, repository validation, backup jobs, snapshots, and restores.
---

# Backup, Storage, and Restore

Run connection validation again from the relevant page. If a job has already started, begin with the failed step in its details.

## Source folders cannot be browsed

- Confirm that the Agent or Proxy is online.
- Confirm that the selected path exists and the Agent's runtime identity can read it.
- For a NAS source, confirm that the Proxy can reach the share and that the protocol, share path, and credentials are correct.
- If non-ASCII filenames display incorrectly, confirm that the Proxy host supports UTF-8 filenames and reconnect the NAS.
- Confirm that the path format matches the source operating system.

## Object-storage validation fails

Use the error type to guide the check:

- **Credentials rejected:** The access key, secret key, or temporary credentials have expired or are incorrect.
- **Permission denied:** The identity cannot list the bucket or read and write the selected prefix.
- **Bucket not found:** The bucket name, region, or account is incorrect.
- **Invalid configuration:** The endpoint, region, URL style, or TLS settings do not match the service.
- **Network error or timeout:** The component performing validation cannot reach the endpoint.
- **TLS failure:** The certificate chain, service name, or host time is incorrect.

A large clock difference can also break object-storage request signing. Synchronize system time before trying validation again.

## NAS or local-storage validation fails

- Confirm that the attached Proxy is online.
- For NAS, confirm the address, protocol, share path, and credentials.
- For local storage, confirm that the directory exists, is writable, and has enough free space.
- If another Agent or Private Data Gateway needs Proxy storage, confirm that the <span class="hfl-ui">Repository Server Address</span> is reachable from its network and that the required ports are allowed.
- Do not select a directory that already contains business data or another backup repository.

## A backup fails or partially succeeds

Open the job details and review the specific failed stage and file scope. Common causes include deleted source files, changed read permissions, locked files, interrupted target connectivity, or an offline Agent.

A partially successful snapshot contains only completed data. After resolving the problem, run the backup again and verify the new snapshot. Failed data is not automatically added to the earlier snapshot.

## A snapshot cannot be restored

- The snapshot must have succeeded or partially succeeded.
- It must contain browsable backed-up files or folders.
- The selected restore scope must be part of the paths stored in that snapshot.
- The destination Agent must be online, and its destination folder must exist, be writable, and have enough free space.
- If another restore job is running for the same source, wait for it to finish before submitting another.

A stopped restore may leave incomplete files in the destination folder. Review the folder before running the restore again with the appropriate conflict behavior.
