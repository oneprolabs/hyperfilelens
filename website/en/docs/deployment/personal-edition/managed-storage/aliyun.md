---
title: Connect Alibaba Cloud OSS
description: Create a scoped RAM user and role for Personal Edition managed storage, and collect every field required by the form.
---

# Connect Alibaba Cloud OSS

::: info Audience
This guide is for Personal Edition operators. It requires Enterprise and the appropriate Managed Storage administration permissions in the Personal Edition organization. Personal users do not configure storage identities or signing keys.
:::

<p class="hfl-doc-lead">A dedicated RAM user's long-lived keys call Alibaba STS to assume a role with limited OSS permissions. Clients receive only temporary credentials further narrowed to one repository.</p>

## Prerequisites

- A cloud administrator must be able to manage the bucket, RAM users, roles, and custom policies. Do not give those administration permissions to HyperFileLens.
- Prepare an existing private bucket. Examples use `hfl-backups-prod` and connection prefix `personal/`; both are replaceable.
- This example uses a same-account user and role. For cross-account use, distinguish the caller's account from the role's account and check the bucket policy.
- The Controller must reach STS and all executors must reach the storage endpoint. Do not assume that an OSS internal endpoint is reachable outside Alibaba Cloud.

## Create a Dedicated Empty Bucket

1. Open **OSS → Buckets → Create Bucket**. Choose the production region and a unique name, then record the bucket's **Overview → Public Endpoint** and region. Do not copy a bucket-qualified URL or an internal endpoint for executors outside the cloud network.
2. Use **Standard** storage and **Private** ACL; keep public access blocked. For this baseline, do not enable retention/WORM, archive transitions, automatic object deletion, or customer-managed KMS encryption without a separately validated configuration.
3. Create the bucket and confirm it is empty. This operation belongs to the storage administrator, not the signing RAM user or assumed role.

`hfl-backups-prod` is an example, not a reserved name. Replace it everywhere in the policies and form with your actual bucket. The prefix `personal/` is an object-key prefix, not a directory to create. An existing dedicated bucket may be reused only after verifying ownership, private access, and the absence of conflicting data or retention rules.

## Create the Dedicated RAM User

1. Open **RAM → Identities → Users → Create User**. Create `hfl-sts-controller` for programmatic use without console sign-in.
2. In that user's **AccessKey** tab, create an AccessKey pair and store it securely. Do not use the primary account's keys.
3. Record the real account UID. Alibaba UIDs commonly have 16 digits; do not truncate one to the 12 digits shown in AWS examples. Copy the actual role ARN later.
4. Do not add privileged groups or attach `AdministratorAccess`, `AliyunOSSFullAccess`, or the all-roles `AliyunSTSAssumeRoleAccess`.

See [Create a RAM user](https://help.aliyun.com/en/ram/user-guide/create-a-ram-user) and [Create an AccessKey pair](https://help.aliyun.com/en/ram/user-guide/create-an-accesskey-pair).

## Create a Role with Exact Trust

1. Open **RAM → Identities → Roles → Create Role**, choose **Alibaba Cloud Account** as the trusted entity, and create `hfl-managed-storage`. Do not create an ECS service role.
2. Edit its **Trust Policy** using the JSON below. Replace `<ACCOUNT_ID>` with the caller's account UID; use the complete user ARN if it includes a path.
3. Trust only the dedicated user, not the entire account's root principal. Consult the [specific-user/specific-role procedure](https://help.aliyun.com/en/ram/support/faq-about-ram-roles-and-sts-tokens) if labels differ.
```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "RAM": [
          "acs:ram::<ACCOUNT_ID>:user/hfl-sts-controller"
        ]
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

## Authorize the User to Assume Only This Role

Open **RAM → Permissions → Policies → Create Policy → JSON**, and create `hfl-assume-managed-storage`. Replace `<ACCOUNT_ID>` with the target role's account UID and copy its entire ARN if a path is present.
```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "acs:ram::<ACCOUNT_ID>:role/hfl-managed-storage"
    }
  ]
}
```

Under **Users → hfl-sts-controller → Grant Permission**, attach this policy only. **It belongs to the user, not the role; it does not grant direct OSS access.** Cross-account caller and role IDs are not interchangeable.

## Attach the OSS Policy to the Role

Create custom policy `hfl-managed-storage-oss` using the same policy editor. Attach it under **Roles → hfl-managed-storage → Grant Permission**. **This policy belongs to the role, not to the long-lived key owner.**

Replace all bucket names and prefix values:
```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "oss:ListObjects",
        "oss:ListObjectVersions"
      ],
      "Resource": [
        "acs:oss:*:*:hfl-backups-prod"
      ],
      "Condition": {
        "StringLike": {
          "oss:Prefix": [
            "personal/repositories/",
            "personal/repositories/*"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:GetBucketInfo"
      ],
      "Resource": [
        "acs:oss:*:*:hfl-backups-prod"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:AbortMultipartUpload",
        "oss:DeleteObject",
        "oss:DeleteObjectVersion",
        "oss:GetObject",
        "oss:ListParts",
        "oss:PutObject"
      ],
      "Resource": [
        "acs:oss:*:*:hfl-backups-prod/personal/repositories/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:ListMultipartUploads"
      ],
      "Resource": [
        "acs:oss:*:*:hfl-backups-prod"
      ]
    }
  ]
}
```
This policy defines the connection-wide permission ceiling for backup, restore, maintenance, and physical cleanup. Each temporary session is further restricted to one repository and its operation.

- **Writes and cleanup:** `DeleteObject` is required for temporary repository objects and maintenance; this is not an append-only storage design. Version deletion and multipart abort are required for final cleanup.
- **Bucket metadata:** `oss:ListMultipartUploads` is bucket-scoped metadata access used by the current Controller cleanup session, without the normal object-listing prefix condition. Bucket information is also bucket-scoped. Use a dedicated bucket: the policy includes both prefix-scoped object access and the bucket-scoped metadata permissions described above.
- **Object access:** Object content operations and object/version listings remain prefix-restricted. Do not grant the connection identity bucket creation, deletion, policy management, or global administration.

Do not require MFA, ExternalId, or SourceIdentity in this example without adapting issuance: the current form does not supply them. If your organization mandates such conditions, adapt the integration rather than removing the security requirement. Both the user's permission and role trust must match; a session cannot expand role permissions. See [Assume a RAM role](https://help.aliyun.com/en/ram/user-guide/assume-a-ram-role) and [OSS policies](https://help.aliyun.com/en/oss/user-guide/ram-policy).

## Verify the Actual AccessKey Owner

Before entering keys, have the RAM administrator locate the **actual AccessKey ID** under the intended RAM user's AccessKey tab. A login email, display name, or a different user created for the same project does not establish ownership.

Optionally, use a dedicated, securely configured Alibaba Cloud CLI profile containing that same key pair (not an administrator profile) to call [GetCallerIdentity](https://help.aliyun.com/en/ram/developer-reference/api-sts-2015-04-01-getcalleridentity):

```bash
aliyun sts GetCallerIdentity --profile hfl-sts-controller
```

Check `IdentityType` is `RAMUser`. Its `AccountId` and complete `Arn` must match the caller account and the role trust's Principal exactly. This response identifies the caller; it does not grant or prove AssumeRole permission. Do not put secrets on the command line, enable SDK debug logging, or paste temporary-credential responses into tickets.

In RAM, recheck the binding on **that user**: `sts:AssumeRole` targets the exact role ARN, and the role trusts that exact user ARN. For this setup, grant the role-call policy at **account scope**, while keeping its Resource restricted to the one role ARN; do not mistakenly restrict the binding to an unrelated resource group. Attach the OSS policy to the role, not to the signing user. An explicit deny still takes precedence.

## Complete Add Object Storage

Select **Alibaba Cloud** and the bucket's actual region.

| Form field | Value and source |
| --- | --- |
| Endpoint URL | Confirm the public regional endpoint in the OSS bucket overview and select the corresponding HyperFileLens catalog entry; do not include the bucket name |
| Region | Obtain the actual region, for example Hangzhou `cn-hangzhou`. The current HyperFileLens catalog may show `oss-cn-hangzhou`; retain the matching catalog value rather than mixing SDK signing regions |
| Access Key / Secret Key | The dedicated RAM user's AccessKey ID / AccessKey Secret, not a role name or temporary token |
| S3 URL Style | Retain the region preset; current Alibaba entries use Virtual Hosted Style |
| Use TLS (HTTPS) | On |
| Identity Service Endpoint | Current default `https://sts.aliyuncs.com`; check the official endpoint list and network requirements before using regional STS |
| Role ARN | Copy the complete ARN from **RAM → Identities → Roles → hfl-managed-storage → Basic Information**, such as `acs:ram::<ACCOUNT_ID>:role/hfl-managed-storage` |

::: warning Official SDK examples and the current product catalog differ
Alibaba's current [AWS SDK guide](https://help.aliyun.com/en/oss/developer-reference/use-aws-sdks-to-access-oss) uses `s3.oss-<region>.aliyuncs.com`. The current HyperFileLens catalog uses `oss-<region>.aliyuncs.com`, and the managed-connection validator does not accept the former `s3.oss-` form. That endpoint format is not supported by the current managed-connection validator. Test issuance, initialization, and backup/restore using the catalog supplied with your deployment. If the environment needs the newer endpoint, complete product adaptation first; do not compensate with broader permissions or guessed Regions.
:::

### Storage Configuration

| Form field | Value |
| --- | --- |
| Name | Auto-filled from provider and bucket; editable. This is not the cloud user or role name |
| Bucket | The actual, existing private bucket; `hfl-backups-prod` is only a suggested name |
| Object Prefix | Use `personal/` for a new connection. HyperFileLens appends `repositories/<repository_uuid>/`; do not enter a user or repository UUID here |

If you change the bucket or connection prefix, replace every policy resource and prefix condition accordingly. The wildcard in these examples spans repositories under this connection, not other buckets. Do not relocate existing repositories just to rename a connection or rotate its keys.

## Verify the Connection and Repository

::: warning Initial setup and personal registration
Keep personal registration disabled during initial setup and validate with an existing test member, or use a controlled test environment. New connections are enabled by default; when no default connection exists, saving a new connection automatically makes it the default. New connections need no separate activation step, and the page does not offer a Disable action. Complete initialization, backup, and restore tests and confirm the production default before enabling personal registration.
:::

To restore an existing Disabled connection, select it and use **More Actions → Enable** with connection-management permission. Before enabling it, the server checks temporary-credential issuance and read access again. If validation fails, the connection stays disabled and the existing default is unchanged. Successful recovery preserves an existing default; if none exists, the restored connection automatically becomes the default. Follow the validation procedure on this page and confirm the production default before enabling personal registration.

1. Switch to **Personal Edition** and open **Managed Storage → Object Storage → Add**. Complete the fields and select **Add**; labels may be localized.
2. Before saving, HyperFileLens requests repository-scoped read-only temporary credentials and lists at most one object under that prefix. An empty bucket does not need a pre-created folder. Investigate identity, policy, and network errors rather than switching to unrestricted keys.
3. Check the connection's actual Enabled status and Default badge. If a default already existed, adding this connection does not replace it; after validation, use **More Actions → Set as Default** if it should serve subsequent personal registrations. Changing the default affects subsequent creation, not existing repositories.
4. Under **Backup Repositories**, create a test repository owned by a test member; repository creation and Owner assignment require appropriate permissions. Use **More Actions → Provision Missing Repositories** for historical members without repositories. Use **Retry Initialization** for an existing failed repository instead of repeated provisioning.
5. Wait for successful initialization, then use the test member's **Backup Wizard** to back up and restore a small file. Confirm that another member cannot access that repository. Test physical deletion only with a disposable repository and an explicitly confirmed scope.

**Saving a connection verifies issuance and read accessibility, not write access, the complete repository lifecycle, or connectivity from every client.** See the [rollout checklist](./index#verify-before-rollout).

### Proxy Compatibility

If an HTTPS proxy is configured but `NO_PROXY` matches the actual STS host, open **Admin Console → External Access → Access Configuration → Alibaba Cloud STS Proxy Compatibility**. This opt-in setting is disabled by default. It lets the original Alibaba SDK honor the bypass through a short-lived isolated process for that STS call; without an HTTPS proxy, or without a matching bypass, no isolated process is used. It does not alter OSS traffic or global proxy settings.

This setting fixes neither IAM permissions nor `NoPermission`. If STS returns `NoPermission` / AssumeRole denial with a Request ID, connectivity has reached STS: check actual key ownership, caller-policy binding, exact role ARN and trust Principal. If STS succeeds but OSS returns AccessDenied, inspect the role's data policy, bucket policy and prefix. A proxy connection error is a separate transport problem.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Issuance fails | User's role-specific `sts:AssumeRole`, role trust, complete ARN, keys, and account UID |
| AccessDenied on save or initialization | OSS policy is attached to the role; bucket and `oss:Prefix` match; no bucket-policy explicit deny or conflicting network restriction |
| Region or signature error | The catalog endpoint/signing-region pair and the compatibility warning above; STS's region is not necessarily the bucket's region |
| Backup works but deletion fails | Version listing, version deletion, and multipart permissions; do not delete production objects as a troubleshooting shortcut |

## Configuration Checklist

After configuring the storage service, verify the following items before completing **Add Object Storage**.

- The role trusts only the dedicated RAM user, and the user can assume only the designated role.
- The OSS data policy is attached to the role, not directly to the long-lived key owner, and its object scope matches the connection prefix.
- No global administration, full OSS access, all-role assume permission, or bucket creation/deletion is granted.
- Bucket information and incomplete multipart-upload listings are bucket-scoped metadata permissions; object content access and object/version listings remain restricted to the repository prefix.

### Required Configuration Details

| Configuration | Verification requirement |
| --- | --- |
| Bucket | Name of the dedicated private bucket, created before adding the connection |
| Region / Endpoint URL | Actual bucket region and public OSS endpoint matching the current product catalog |
| Role ARN | Complete ARN copied from the RAM role details |
| Identity Service Endpoint | Default `https://sts.aliyuncs.com`; verify network and regional requirements for a regional endpoint |
| Access Key / Secret Key | Dedicated RAM user's AccessKey ID / AccessKey Secret, configured through a secure channel |
| Object Prefix | Recommended default `personal/`; must match the repository prefix in the policies |

Configure keys on the Controller through a secure channel. Do not include keys in ordinary tickets, documentation, or source control. Manually generated temporary tokens and cloud root-account passwords are not required.

## Official References

- [Grant permissions to the actual RAM user](https://help.aliyun.com/en/ram/user-guide/grant-permissions-to-the-ram-user)
- [RAM user and role restrictions](https://help.aliyun.com/en/ram/support/faq-about-ram-roles-and-sts-tokens)
- [Custom policies](https://help.aliyun.com/en/ram/create-a-custom-policy)
- [OSS policies](https://help.aliyun.com/en/oss/user-guide/ram-policy)
- [Bucket overview](https://help.aliyun.com/en/oss/bucket-overview-page)
- [STS endpoints](https://help.aliyun.com/en/ram/developer-reference/api-sts-2015-04-01-endpoint)
