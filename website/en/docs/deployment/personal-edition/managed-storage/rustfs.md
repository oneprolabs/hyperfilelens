---
title: Connect RustFS
description: Prepare a regular RustFS user, scoped policy, and STS for Personal Edition, with version, network, and connection-configuration requirements.
---

# Connect RustFS

::: info Audience
This guide is for Personal Edition operators. It requires Enterprise and the appropriate Managed Storage administration permissions in the Personal Edition organization. Personal users do not configure storage identities or signing keys.
:::

<p class="hfl-doc-lead">RustFS uses its own STS and regular built-in users, not an AWS cloud role. Confirm deployment capabilities before connecting with a dedicated prefix-scoped identity.</p>

## Prerequisites

- A storage administrator prepares an existing private bucket, for example `hfl-backups-prod`, and connection prefix `personal/`.
- Obtain the **S3 API address**, not the management Console. Verify actual listeners and proxies rather than guessing from common `9000/9001` ports.
- The Controller and every executor must reach it. Check DNS, certificate trust, and firewalls. The current integration requires the same scheme/host/port for S3 and STS at the root path, with no `/s3` proxy prefix.
- Use HTTPS in production. The current version allows HTTP only for localhost, loopback, or permitted private IP literals; a hostname resolving privately is not automatically accepted over HTTP. Do not disable certificate verification.
- This guide is not a compatibility promise for every RustFS release. Record and test the actual server version, IAM configuration, and renewal. Other identity modes in provider documentation are not necessarily implemented by this integration.

## Create and Authorize the Dedicated User

1. Record the RustFS server version and configuration. Have the administrator confirm signed AssumeRole for **regular IAM users**, inline Policy support, session-token validation, and this guide's listing/version/multipart actions. An S3 upload alone does not establish compatibility.
2. In RustFS Console **Identity → Policies**, create custom policy `hfl-managed-storage` with the next section's JSON. If labels differ, follow the installed version's IAM guide.
3. Under **Identity → Users**, create enabled regular IAM user `hfl-sts-controller`, choose a strong secret, and attach that policy. Remove extra unrestricted groups or `readwrite` / `consoleAdmin` policies.
4. Use the **regular user's own** access key (username) and secret for the connection. Do not generate a Service Account under **Access Keys** instead: service accounts and temporary identities cannot call AssumeRole.
5. User policies and session policy jointly constrain temporary access. This integration does not need an AWS IAM role or a Role ARN. Do not add `admin:*`.

Use the official [RustFS IAM workflow](https://docs.rustfs.com/en/security-compliance/iam/policies) or version-matched `rc admin`. RustFS and MinIO administrative commands and APIs are not interchangeable.

## Connection-Wide Least-Privilege Policy

Attach this policy to the **dedicated regular user**, not other business users. Replace bucket and prefix:
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

Unlike the AWS/Alibaba caller that can only assume a role, this RustFS parent user itself has the scoped S3 permissions above; session policy narrows them further. Protect its long-lived secret and never send it to an Agent. Do not attach `admin:*`, unrestricted `readwrite`, or administrator groups.

The current RustFS integration signs AssumeRole with the SigV4 `s3` service at the S3 server root, unlike the `sts` signing service of an AWS STS SDK. The actual deployment must provide that API; proxies must preserve POST form bodies, signed headers, and session tokens.

## Complete Add Object Storage

Select **RustFS**:

| Form field | Value and source |
| --- | --- |
| Endpoint URL | Actual S3 API host and port, for example `rustfs.example.com:9000`; the TLS switch controls the scheme |
| Region | Actual signing region. The form defaults to `us-east-1` and uses it when blank; match any custom server region |
| Access Key / Secret Key | Dedicated regular user `hfl-sts-controller` access key and secret, not administrator/root or Service Account credentials |
| S3 URL Style | Path Style, required by the current managed integration |
| Use TLS (HTTPS) | On for production with trusted certificates; private HTTP only under the stated conditions |
| Identity Service Endpoint / Role ARN | Not shown for this provider. The system issues credentials at the same service address; no AWS role is needed |

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
| S3 reads work but issuance fails | Wrong identity type (root, service account, derived key, or temporary identity), missing policy binding, or incompatible signed AssumeRole support |
| HTML login page or 404 | Console address used instead of S3, wrong reverse-proxy backend, or a path prefix |
| HTTP endpoint rejected | Unsupported hostname/address; prefer trusted HTTPS rather than disabling validation |
| AccessDenied or signature failure | Bucket/prefix, signing region, clock skew, and proxy preservation of signed headers and Token |
| Incomplete cleanup | Version/delete-marker/multipart API implementation and permissions, retention and object locks; do not grant administration as a workaround |

## Configuration Checklist

After configuring the storage service, verify the following items before completing **Add Object Storage**.

- The deployment has been validated for regular-user signed AssumeRole, inline session policies, and session-token validation.
- The dedicated regular user has only this guide's prefix policy, not root, a Service Account, a derived access key, or unrestricted readwrite.
- S3 and STS use the same scheme, host, and port; proxies preserve signatures and tokens, and production uses trusted HTTPS.
- Bucket information and incomplete multipart-upload listings are bucket-scoped metadata permissions; object content access and object/version listings remain restricted to the repository prefix.

### Required Configuration Details

| Configuration | Verification requirement |
| --- | --- |
| Server version and deployment | Actual RustFS version, deployment type, and validated STS capabilities |
| Endpoint URL / Use TLS (HTTPS) | S3 API hostname, port, and scheme reachable by all executors, not the management Console address |
| Region | Actual signing region; verify the `us-east-1` default against the server configuration |
| Bucket | Name of the dedicated private bucket, created before adding the connection |
| Access Key / Secret Key | Dedicated regular user's own access key and secret, configured through a secure channel |
| TLS and networking | Certificate trust chain, DNS, firewall, and connectivity requirements |
| Object Prefix | Recommended default `personal/`; must match the repository prefix in the policies |

Configure keys on the Controller through a secure channel. Do not include keys in ordinary tickets, documentation, or source control. Manually generated temporary tokens and cloud root-account passwords are not required. This provider does not require an AWS Role ARN; administrator aliases and keys are for storage administration only, not the managed connection.

## Official References

- [RustFS users, groups, and policies](https://docs.rustfs.com/en/security-compliance/iam/policies)
- [RustFS service accounts and STS](https://docs.rustfs.com/en/security-compliance/iam/sts)
- [RustFS access keys](https://docs.rustfs.com/en/security-compliance/iam/access-token)
- [RustFS TLS](https://docs.rustfs.com/en/integration/tls-configured)
