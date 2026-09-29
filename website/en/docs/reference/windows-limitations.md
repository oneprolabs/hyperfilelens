---
title: Windows file attribute and permission limitations
description: Understand which Windows file attributes and permissions HyperFileLens currently preserves during backup and restore.
---

# Windows file attribute and permission limitations

HyperFileLens can back up and restore file content from Windows hosts and
Windows-accessible SMB shares. Content restore and Windows filesystem
metadata preservation are separate capabilities. A successful restore confirms
that the selected content was written to the target path. It does not mean
that Windows file attributes or NTFS permissions were backed up or restored.

## Current support boundary

The current release uses the product's filesystem snapshot and restore
engine. Its metadata model records file names, directory structure, content,
timestamps, and basic mode information. It does not record Windows-specific
file attributes or NTFS security descriptors, so the current release cannot
restore them from a snapshot.

| Capability | Current behavior |
| --- | --- |
| File content | Backed up and restored |
| File names and directory structure | Backed up and restored, subject to filesystem and path restrictions |
| Modification times | The restore engine attempts to restore them; the destination filesystem can reject or change the result |
| Windows `Hidden` attribute | Not backed up or restored |
| Windows `System` attribute | Not backed up or restored |
| Windows `Archive` attribute | Not backed up or restored |
| Windows `Read-only` attribute | Not backed up or restored as a Windows attribute; the resulting write-protection state can differ |
| NTFS owner and security descriptor | Not backed up or restored |
| NTFS allow/deny ACEs | Not backed up or restored |
| Active Directory user and group SIDs | Not backed up or restored |
| NTFS inheritance and inherited ACEs | Not backed up or restored |

A source file with `Hidden` set is therefore restored without a backed-up
`Hidden` value. Windows can display that restored file as visible. The same
limitation applies to the `System` and `Archive` attributes.

## NTFS permissions and Active Directory ACLs

The current release does not back up or restore the Windows security descriptor
for a file or directory. The following source details are not available in the
backup metadata and are not reconstructed during restore:

- the owner;
- local or domain user and group SIDs;
- allow and deny access-control entries;
- explicit versus inherited permissions;
- inheritance flags and protected ACL state;
- permissions involving groups such as `BUILTIN\Administrators`.

For example, a source entry containing `BUILTIN\Administrators:(F)` does not
have that ACE backed up by the current release. The restored entry therefore
does not receive that source ACE from the snapshot. Do not use a successful
content restore as proof that the source ACL was recovered.

The destination can still have permissions from its own directory inheritance
or filesystem defaults. Those destination permissions are not the source ACL
and do not represent ACL recovery.

## SMB or CIFS shares through a Linux Proxy

When an SMB or CIFS share is mounted through a Linux Proxy, the backup engine
works with the filesystem view exposed by the Linux mount. The current release does not
back up or restore the original Windows security descriptor, domain SIDs,
DACL, or inheritance behavior through this path.

Adding an ACL-related `mount.cifs` option can change how the Linux mount
exposes permissions. It does not make Windows ACL capture or restore supported
by HyperFileLens. This limitation applies whether the share is a backup source
or a restore target.

## Restore validation recommendations

For data whose Windows permissions or attributes are important:

1. Restore a representative sample to a separate test directory.
2. Verify file content, names, and directory structure.
3. Check Windows attributes and ACLs with the native tools for the target
   system.
4. Verify the result for the actual Agent account and destination filesystem.
5. Keep a separate native Windows or AD-aware permissions backup if exact ACL
   fidelity is a regulatory or operational requirement.

Do not use a HyperFileLens content restore as the only ACL backup. The current
release does not create an ACL backup that can restore the source Windows
security descriptor.

## Future support

There is no current release commitment for Windows attribute or NTFS ACL
preservation. A future release must add and verify Windows file attributes,
security descriptors, SID identity, privilege requirements, and SMB/NAS target
behavior before this page or the support matrix can claim support.

The support boundary in this page applies to the current product release.
