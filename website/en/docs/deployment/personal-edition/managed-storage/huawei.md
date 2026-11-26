---
title: Connect Huawei Cloud OBS
description: Prepare an account agency, dedicated IAM caller, and OBS prefix policy, and obtain Agency Name, Domain ID, and connection fields.
---

# Connect Huawei Cloud OBS

::: info Audience
This guide is for Personal Edition operators. It requires Enterprise and the appropriate Managed Storage administration permissions in the Personal Edition organization. Personal users do not configure storage identities or signing keys.
:::

<p class="hfl-doc-lead">The current integration issues temporary AK/SK/Token through an IAM v3 account agency. Distinguish the resource account, delegated account, and IAM user before configuring the form; do not use a cloud-service agency or a project ID in place of an account ID.</p>

## Prerequisites

- Prepare an existing private OBS bucket. Examples use `hfl-backups-prod` and connection prefix `personal/`.
- The **resource account** owns the bucket and creates the agency. The **delegated account** owns the dedicated calling IAM user. They differ in a cross-account deployment.
- An IAM administrator must confirm a supported account delegation arrangement. Also confirm same-account arrangements against the actual IAM deployment; do not substitute a cloud-service agency.
- The Controller must reach IAM, and executors must reach OBS. The long-lived caller does not need user or agency administration privileges.

## Create a Dedicated Empty Bucket

1. In the resource account, open **OBS → Buckets → Create Bucket**, choose the required region, and enter a unique bucket name. Record the actual name and regional OBS endpoint.
2. Select **Standard** storage and **Private** access. Keep public access blocked. For this baseline, do not enable WORM, archive transitions, automatic object deletion, or customer-managed KMS encryption without a separately validated configuration.
3. Create the bucket and confirm it is empty. Creating the bucket is an administrator operation, not a permission for the signing user or agency.

`hfl-backups-prod` is an example, not a reserved name. Replace it everywhere in the policies and form with your actual bucket. The prefix `personal/` is an object-key prefix, not a directory to create. An existing dedicated bucket may be reused only after verifying ownership, private access, and the absence of conflicting data or retention rules.

## Create the Dedicated Caller

1. In the delegated account's **IAM → Users**, create `hfl-sts-controller` for programmatic access without ordinary console access.
2. An administrator creates that user's AK/SK in its credential settings, or follows the [access-key procedure](https://support.huaweicloud.com/intl/en-us/perms-cfg-obs/obs_40_0007.html) through a controlled process. Do not substitute resource-account root keys.
3. Record the parent **Huawei Cloud account name**, not the IAM username or HyperFileLens sign-in email, for the agency's trust configuration.

## Create an Account Agency

1. As a resource-account administrator, open **IAM → Agencies → Create Agency** and name it `hfl-managed-storage`.
2. Select the **account / ordinary account** agency type, not **cloud service**. Enter the parent account name of the dedicated caller in the delegated-account field.
3. Select the agency relationship's validity. This is not the lifetime of each temporary key.
4. Authorize the new agency with the OBS custom policy below, using the global-service scope appropriate to OBS. Do not add `OBS Administrator` or `Security Administrator`.

Check [Create an agency](https://support.huaweicloud.com/intl/en-us/usermanual-iam/iam_06_0002.html). If the console rejects the account arrangement, have the administrator confirm a supported arrangement rather than trying a cloud-service agency.

### Account Example

Account **A** owns the bucket and creates agency `hfl-managed-storage`. Account **B** owns IAM user `hfl-sts-controller` and its AK/SK. In A's agency, enter **B's account name** as the delegated account. In the form, use **A's account ID** for Domain ID, A's agency name, and B's IAM-user keys. B's dedicated user group receives only permission to assume that named agency. This is the documented cross-account arrangement; do not assume that an arbitrary same-account arrangement is supported.

The OBS JSON below is an **IAM custom policy** attached to the agency, not an OBS bucket-policy document. Use the IAM policy editor; these two policy formats are not interchangeable.

## Attach OBS Prefix Permissions to the Agency

Open **IAM → Permissions/Policies → Create Custom Policy → JSON**, create `hfl-managed-storage-obs`, replace every bucket/prefix value, and attach it to the agency:
```json
{
  "Version": "1.1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "obs:bucket:ListBucket",
        "obs:bucket:ListBucketVersions"
      ],
      "Resource": [
        "OBS:*:*:bucket:hfl-backups-prod"
      ],
      "Condition": {
        "StringStartWith": {
          "obs:prefix": [
            "personal/repositories/"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "obs:bucket:GetBucketLocation"
      ],
      "Resource": [
        "OBS:*:*:bucket:hfl-backups-prod"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "obs:object:AbortMultipartUpload",
        "obs:object:DeleteObject",
        "obs:object:DeleteObjectVersion",
        "obs:object:GetObject",
        "obs:object:ListMultipartUploadParts",
        "obs:object:PutObject"
      ],
      "Resource": [
        "OBS:*:*:object:hfl-backups-prod/personal/repositories/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "obs:bucket:ListBucketMultipartUploads"
      ],
      "Resource": [
        "OBS:*:*:bucket:hfl-backups-prod"
      ]
    }
  ]
}
```
This policy defines the connection-wide permission ceiling for backup, restore, maintenance, and physical cleanup. Each temporary session is further restricted to one repository and its operation.

- **Writes and cleanup:** `DeleteObject` is required for temporary repository objects and maintenance; this is not an append-only storage design. Version deletion and multipart abort are required for final cleanup.
- **Bucket metadata:** `obs:bucket:ListBucketMultipartUploads` is bucket-scoped metadata access used by the current Controller cleanup session, without the normal object-listing prefix condition. Bucket information is also bucket-scoped. Use a dedicated bucket: the policy includes both prefix-scoped object access and the bucket-scoped metadata permissions described above.
- **Object access:** Object content operations and object/version listings remain prefix-restricted. Do not grant the connection identity bucket creation, deletion, policy management, or global administration.

OBS conditions do not use AWS wildcard semantics unchanged. This connection-wide parent policy uses `StringStartWith` for `personal/repositories/`. The current per-repository session uses `StringLike` with that repository's exact root, without appending an AWS-style wildcard. Wildcards in object resources are separate from listing conditions. See [OBS custom policies and conditions](https://support.huaweicloud.com/intl/en-us/usermanual-iam/iam_01_0019.html).

## Allow Only This Agency for the Calling User

In the delegated account, create a dedicated IAM group and add `hfl-sts-controller`. Create this **agency-call policy** and authorize the group in the global-service scope. Do not grant unrestricted access to all agencies:
```json
{
  "Version": "1.1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "iam:tokens:assume"
      ],
      "Resource": {
        "uri": [
          "/iam/agencies/hfl-managed-storage"
        ]
      }
    }
  ]
}
```

`/iam/agencies/hfl-managed-storage` follows the agency-resource form in Huawei's specific-agency authorization guide; the final component is the **agency name**, not its ID. Replace it when the agency name differs. The OBS data policy belongs to the **agency**, while `iam:tokens:assume` belongs to the **caller's group**. See [Assign agency permissions to IAM users](https://support.huaweicloud.com/intl/en-us/usermanual-iam/iam_01_0063.html).

## Obtain Account Details and Complete Add Object Storage

Select **Huawei Cloud** and the actual bucket region.

| Form field | Value and source |
| --- | --- |
| Endpoint URL | Regional endpoint in the OBS bucket overview, such as `obs.cn-north-4.myhuaweicloud.com`, supplied by the matching catalog selection |
| Region | Actual bucket region, such as `cn-north-4`; not an IAM project ID |
| Access Key / Secret Key | Dedicated calling IAM user's AK/SK |
| S3 URL Style | Keep the regional preset; current Huawei entries use Virtual Hosted Style |
| Use TLS (HTTPS) | On |
| Identity Service Endpoint | Current default `https://iam.myhuaweicloud.com`; verify official IAM endpoints for special regions |
| Agency Name | Actual agency name under the **resource account → IAM → Agencies**, not the agency ID |
| Domain ID | **Resource account → My Credentials → API Credentials → Account ID**, 32 hexadecimal characters; not a project ID, user ID, or the other account's caller ID |

::: warning IAM Endpoint Scope
`https://iam.myhuaweicloud.eu` corresponds to EU-Dublin and the officially documented scope, not every European or international region. Verify OBS endpoint, IAM endpoint, and account environment separately. Consult the [official IAM endpoint list](https://support.huaweicloud.com/intl/en-us/api-iam/iam_01_0001.html); an international console does not automatically require `.eu`.
:::

See [Obtain account, user, and agency information](https://support.huaweicloud.com/intl/en-us/api-iam/iam_17_0002.html) for field sources.

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

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Delegated account cannot be found | Use the parent account name, not IAM username, ID, email, or service name; confirm the account relationship |
| Temporary credential issuance fails | Agency type/trust, caller group's agency-specific permission, agency name, and resource account Domain ID |
| OBS AccessDenied | OBS policy attached to the agency, bucket/prefix, and listing conditions; do not copy AWS wildcard rules |
| IAM or OBS endpoint fails | Account environment, actual region, DNS and TLS; do not put the OBS address in the IAM field |
| Physical cleanup fails | Version, delete-marker and multipart permissions, and retention restrictions; bucket deletion is not the remedy |

## Configuration Checklist

After configuring the storage service, verify the following items before completing **Add Object Storage**.

- The agency uses the account type, and its trusted account matches the dedicated calling IAM user's parent account.
- The OBS data policy is attached to the agency; the agency-specific caller policy is granted through the dedicated user group.
- The caller has no global-administrator or all-agencies access, and the agency restricts object permissions to the intended bucket and repository prefix.
- Bucket information and incomplete multipart-upload listings are bucket-scoped metadata permissions; object content access and object/version listings remain restricted to the repository prefix.

### Required Configuration Details

| Configuration | Verification requirement |
| --- | --- |
| Bucket | Name of the dedicated private bucket, created before adding the connection |
| Region / OBS Endpoint | Actual bucket region and matching OBS endpoint |
| Identity Service Endpoint | IAM endpoint appropriate to the resource account |
| Agency Name | Name of the account agency created in the resource account |
| Domain ID | Resource account's 32-character account ID, not a project ID or caller user ID |
| Account relationship | Resource and delegated accounts, excluding sign-in credentials |
| Access Key / Secret Key | Dedicated calling IAM user's AK/SK, configured through a secure channel |
| Object Prefix | Recommended default `personal/`; must match the repository prefix in the policies |

Configure keys on the Controller through a secure channel. Do not include keys in ordinary tickets, documentation, or source control. Manually generated temporary tokens and cloud root-account passwords are not required.

## Official References

- [Create an account agency](https://support.huaweicloud.com/intl/en-us/usermanual-iam/iam_06_0002.html)
- [Agency-specific caller authorization](https://support.huaweicloud.com/intl/en-us/usermanual-iam/iam_01_0063.html)
- [IAM API permissions](https://support.huaweicloud.com/intl/en-us/api-iam/iam_19_0001.html)
- [IAM custom-policy syntax and condition operators](https://support.huaweicloud.com/intl/en-us/usermanual-iam/iam_01_0019.html)
- [OBS IAM permissions and resource scope](https://support.huaweicloud.com/intl/en-us/perms-cfg-obs/obs_40_0003.html)
- [Account and agency information](https://support.huaweicloud.com/intl/en-us/api-iam/iam_17_0002.html)
- [IAM endpoints](https://support.huaweicloud.com/intl/en-us/api-iam/iam_01_0001.html)
