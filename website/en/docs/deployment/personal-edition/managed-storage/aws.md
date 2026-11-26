---
title: Connect AWS S3
description: Prepare a dedicated IAM caller, narrowly trusted role, and scoped S3 policy for Personal Edition managed storage.
---

# Connect AWS S3

::: info Audience
This guide is for Personal Edition operators. It requires Enterprise and the appropriate Managed Storage administration permissions in the Personal Edition organization. Personal users do not configure storage identities or signing keys.
:::

<p class="hfl-doc-lead">The Controller calls AWS STS AssumeRole with a dedicated IAM user's keys. The role holds S3 permissions, and each temporary session is narrowed to a specific repository.</p>

## Prerequisites

- An administrator prepares the bucket, IAM user, policies, and role. Do not grant these administrative permissions to the connection identity.
- Create a private bucket in advance, enable Block Public Access, and preferably dedicate it to HyperFileLens. Examples use `hfl-backups-prod` and Object Prefix `personal/`.
- This guide assumes the standard AWS partition and a same-account user and role. For China, GovCloud, or cross-account use, first check product catalog, ARN partition, session policy, and bucket-policy compatibility. Replacing a hostname alone is not enough.
- Evaluate SSE-KMS, Object Lock, and archival storage separately. These examples include neither KMS nor retention-bypass permissions.

## Create a Dedicated Empty Bucket

1. Open **S3 → Buckets → Create bucket**, select **General purpose**, the required AWS Region, and a unique name. Record the actual name and Region.
2. Keep **Block all public access** enabled and the default **Bucket owner enforced** Object Ownership. Use the default S3-managed encryption (SSE-S3) for this baseline. Do not enable Object Lock or introduce SSE-KMS, archive transitions, or lifecycle deletion without a separately validated configuration.
3. Create the bucket and confirm it is empty. Bucket creation belongs to the storage administrator; neither the signing IAM user nor the assumed role needs that permission.

`hfl-backups-prod` is an example, not a reserved name. Replace it everywhere in the policies and form with your actual bucket. The prefix `personal/` is an object-key prefix, not a directory to create. An existing dedicated bucket may be reused only after verifying ownership, private access, and the absence of conflicting data or retention rules.

## Create an IAM User and a Narrowly Trusted Role

1. Open **IAM → Users → Create user**, create `hfl-sts-controller`, and leave console sign-in disabled. Do not add privileged groups.
2. Open **Security credentials → Access keys → Create access key** and select the appropriate workload use case. The current form uses explicit AK/SK; running outside AWS is not a reason to supply root keys.
3. Open **IAM → Roles → Create role → Custom trust policy** and create `hfl-managed-storage` with this policy. Replace `<ACCOUNT_ID>` with the caller's 12-digit account ID, and copy the full user ARN if it includes a path.
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::<ACCOUNT_ID>:user/hfl-sts-controller"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

See [Access keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html) and [custom trust roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-custom.html). Do not substitute an EC2/ECS service principal or broadly trust the account without constraining the caller.

## Attach the Role-Specific Caller Policy to the User

Open **IAM → Policies → Create policy → JSON**, create `hfl-assume-managed-storage`, then attach it under **Users → hfl-sts-controller → Permissions → Add permissions**.

Replace `<ACCOUNT_ID>` with the role owner's account ID and include the role's actual path:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/hfl-managed-storage"
    }
  ]
}
```

**This policy belongs to the user and grants no direct S3 access.** Role trust does not replace caller authorization. The current form supplies no ExternalId, MFA, or source identity; adapt issuance first if your organization's policy requires those conditions, rather than removing the controls.

## Attach the S3 Prefix Policy to the Role

Create `hfl-managed-storage-s3` under **IAM → Policies → Create policy → JSON** and attach it under **Roles → hfl-managed-storage → Permissions → Add permissions**. Replace the bucket and prefix:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:ListBucketVersions"
      ],
      "Resource": [
        "arn:aws:s3:::hfl-backups-prod"
      ],
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "personal/repositories/*"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::hfl-backups-prod"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:AbortMultipartUpload",
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:GetObject",
        "s3:ListMultipartUploadParts",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::hfl-backups-prod/personal/repositories/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucketMultipartUploads"
      ],
      "Resource": [
        "arn:aws:s3:::hfl-backups-prod"
      ]
    }
  ]
}
```
This policy defines the connection-wide permission ceiling for backup, restore, maintenance, and physical cleanup. Each temporary session is further restricted to one repository and its operation.

- **Writes and cleanup:** `DeleteObject` is required for temporary repository objects and maintenance; this is not an append-only storage design. Version deletion and multipart abort are required for final cleanup.
- **Bucket metadata:** `s3:ListBucketMultipartUploads` is bucket-scoped metadata access used by the current Controller cleanup session, without the normal object-listing prefix condition. Bucket information is also bucket-scoped. Use a dedicated bucket: the policy includes both prefix-scoped object access and the bucket-scoped metadata permissions described above.
- **Object access:** Object content operations and object/version listings remain prefix-restricted. Do not grant the connection identity bucket creation, deletion, policy management, or global administration.

This does not grant `s3:ListAllMyBuckets`, bucket creation/deletion, bucket-policy administration, or IAM management. Apply `s3:prefix` to listing statements, not `GetBucketLocation` or object-content statements. See [AssumeRole](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html) for trust and permission intersections.

## Verify the Signing Identity

On a controlled terminal, configure a dedicated AWS CLI profile with the same IAM-user keys, then run:

```bash
aws sts get-caller-identity --profile hfl-sts-controller --region us-east-1 --endpoint-url https://sts.us-east-1.amazonaws.com
```

This standard-partition example returns identity information, not temporary keys. Verify `Account` and the complete user `Arn` match the caller policy and role trust; a different CLI administrator profile proves nothing about the configured key. For China, use the matching regional endpoint and account partition. Do not put keys in command arguments, print AssumeRole credential responses, or enable SDK debug logs. Identity lookup does not itself prove role assumption or S3 access.

## Complete Add Object Storage

Select **AWS** and the bucket's actual region.

| Form field | Value and source |
| --- | --- |
| Endpoint URL | Catalog regional S3 endpoint, for example `s3.us-east-1.amazonaws.com`; not a bucket-qualified or website endpoint |
| Region | Confirm in S3 bucket properties, for example `us-east-1`; endpoint and signing region must match |
| Access Key / Secret Key | Dedicated IAM user's Access key ID / Secret access key, securely stored at creation |
| S3 URL Style | Keep the regional catalog preset; if choosing Virtual Hosted Style manually, verify bucket DNS and TLS compatibility |
| Use TLS (HTTPS) | On |
| Identity Service Endpoint | For new connections, selecting Region suggests a regional endpoint, for example `https://sts.ap-southeast-1.amazonaws.com`. Confirm STS availability for the account; an explicit endpoint remains editable |
| Role ARN | Copy **IAM → Roles → hfl-managed-storage → Summary → ARN**; do not enter the user ARN |

A normal one-hour role maximum is sufficient for the default 15-minute requests. Multi-day tasks are not a reason to lengthen each token. See [STS regional configuration](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp_enable-regions.html).

### STS Endpoint and Signing Region

New AWS connections suggest STS in the selected bucket region. Existing connections and manually edited STS endpoints are preserved. S3 Region still identifies the bucket; the Controller derives the **STS signing region from the STS endpoint**, not from the bucket:

| Identity Service Endpoint | STS signing region |
| --- | --- |
| `https://sts.amazonaws.com` (existing global configuration) | `us-east-1` |
| `https://sts.ap-southeast-1.amazonaws.com` | `ap-southeast-1` |
| `https://sts.eu-west-1.amazonaws.com` | `eu-west-1`, even when the bucket is in another region |
| `https://sts.cn-north-1.amazonaws.com.cn` | `cn-north-1`; requires the China account, `aws-cn` ARNs, matching catalog and policies |

Prefer regional STS. Check [STS endpoints and activation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp_region-endpoints.html), especially for opt-in regions. The global endpoint's signing scope remains `us-east-1`; this does not claim that AWS always routes its traffic to that physical region. GovCloud and other partitions are not covered by this guide.

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
| AssumeRole AccessDenied | Role ARN in caller policy, user ARN in trust policy, SCPs, permission boundaries, and additional conditions |
| Read-only check AccessDenied | Role's S3 policy, bucket policy, and `s3:prefix`; allow `personal/repositories/*`, not only listing the `personal/` root |
| Endpoint/Region mismatch | S3 endpoint and bucket Region must match; STS signs for its own endpoint. Check regional STS activation and partition, not extra S3 permissions |
| Writes or deletion fail | DeleteObject, multipart and version permissions, plus separate KMS, Object Lock, or retention restrictions |

## Configuration Checklist

After configuring the storage service, verify the following items before completing **Add Object Storage**.

- The role trusts only the dedicated IAM user, and the role-specific sts:AssumeRole policy is attached to that user.
- The S3 data policy is attached to the role, with scoped object permissions and the operations required for backup, restore, maintenance, and cleanup.
- Root keys, AdministratorAccess, AmazonS3FullAccess, and IAM administration are not used.
- Bucket information and incomplete multipart-upload listings are bucket-scoped metadata permissions; object content access and object/version listings remain restricted to the repository prefix.

### Required Configuration Details

| Configuration | Verification requirement |
| --- | --- |
| Bucket | Name of the dedicated private S3 bucket with Block Public Access enabled |
| Region / Endpoint URL | Actual bucket region and matching S3 endpoint |
| Role ARN | Complete ARN copied from the IAM role details |
| Identity Service Endpoint | AWS STS endpoint matching the account and network requirements |
| Access Key / Secret Key | Dedicated IAM user's Access key ID / Secret access key, configured through a secure channel |
| Additional security controls | Applicable KMS, Object Lock, SCP, permission-boundary, or special bucket-policy requirements |
| Object Prefix | Recommended default `personal/`; must match the repository prefix in the policies |

Configure keys on the Controller through a secure channel. Do not include keys in ordinary tickets, documentation, or source control. Manually generated temporary tokens and cloud root-account passwords are not required.

## Official References

- [IAM access keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html)
- [Custom trust roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-custom.html)
- [STS AssumeRole](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html)
- [S3 actions and condition keys](https://docs.aws.amazon.com/service-authorization/latest/reference/list_s3.html)
- [S3 Block Public Access](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html)
