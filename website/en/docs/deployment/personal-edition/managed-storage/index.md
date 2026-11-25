---
title: Managed Storage Overview
description: Configure storage connections and independent repositories for Personal Edition, with scoped credentials and a rollout checklist.
---

# Managed Storage Overview

::: info Audience
This guide is for Personal Edition operators. It requires Enterprise and the appropriate Managed Storage administration permissions in the Personal Edition organization. Personal users do not configure storage identities or signing keys.
:::

<p class="hfl-doc-lead">Operators prepare storage and an issuing identity. HyperFileLens creates independent repositories for personal users, who use Backup Wizard without receiving the operator's long-lived cloud keys.</p>

## Managed and Direct Repositories

| Mode | Entry point and responsibility | Bucket and repository |
| --- | --- | --- |
| Direct object-storage repository | An ordinary organization follows [Manage target storage](/docs/backup-restore/targets) | Follow that repository wizard; its credentials are not the managed signing identity described here |
| Personal managed storage | Operators configure connections in Personal Edition's Managed Storage page | The bucket must already exist. One connection can host independent repositories, each with its own UUID prefix and Kopia password |

Managed Storage is not available in every Enterprise organization. Access requires Enterprise, the current organization's capability, and effective organization administration grants. A platform identity in the 11444 Admin Console does not replace authorization in the current organization on 11443. See [Roles and permissions](/docs/governance/roles).

## Connection Workflow

```text
Storage administrator: create dedicated private bucket + configure issuer and scoped policies
      ↓ confirm connection parameters and configure signing keys through a secure channel
Personal Edition operator: keep personal registration disabled → add connection
      ↓ read-only check → save (enabled; automatically default if none exists)
Operator: validate with an existing test member → confirm production default
      ↓ enable personal registration after validation
      ↓
System: provision at registration / administrator creates or backfills repositories
      ↓
Independent repository: personal/repositories/<repository_uuid>/
      ↓
Personal user: back up and restore their data with Backup Wizard
```

Kopia clients on source Agents connect directly to object storage; backup data does not pass through the Controller. Other executors, such as a Private Data Gateway, obtain access for their authorized task. See [Network and ports](/docs/deployment/network).

## Choose a Provider

| Storage | Issuance mechanism | Administrator prepares |
| --- | --- | --- |
| [Huawei Cloud OBS](./huawei) | IAM v3 account agency, returning temporary AK/SK/Token | Dedicated IAM user, agency-specific assume permission, agency and OBS policy, resource account Domain ID |
| [Alibaba Cloud OSS](./aliyun) | Alibaba STS AssumeRole | Dedicated RAM user, role-specific assume permission, role trust and OSS policy |
| [AWS S3](./aws) | AWS STS AssumeRole | Dedicated IAM user, role-specific assume permission, role trust and S3 policy |
| [RustFS](./rustfs) | RustFS's own SigV4 AssumeRole | A compatible deployment, regular IAM user and scoped policy; not a service account |
| [MinIO](./minio) | MinIO's own SigV4 AssumeRole | A compatible deployment, regular built-in user and scoped policy; not a derived access key |

**S3 API compatibility does not imply compatible STS issuance.** Do not configure Huawei or Alibaba issuing credentials as AWS STS, or assume that S3 read/write support alone makes a service suitable for managed storage.

## Names and Permission Scope

```text
hfl-backups-prod                     # example bucket; create it in advance
└── personal/                        # Object Prefix in Add Object Storage
    └── repositories/                # appended by HyperFileLens
        ├── <repository_uuid_a>/     # user A's repository
        └── <repository_uuid_b>/     # user B's repository
```

- The bucket and `personal/` are recommendations, not fixed values. New connection prefixes must be canonical slash-terminated paths without wildcards. Update cloud policies when changing either value.
- The connection identity needs the `personal/repositories/*` business scope, not content access to the entire bucket.
- Executor sessions are narrowed to the actual repository UUID. User isolation also depends on organization, Owner, explicit sharing, and role authorization, not only storage paths.
- Bucket information and multipart-cleanup metadata permissions can be bucket-scoped. Each guide distinguishes these from access to object contents outside the repository prefix.
- Examples assume a normal private bucket. Evaluate WORM/Object Lock, archive storage, KMS, cross-account bucket policies, and network-source conditions separately; do not compensate by granting full administration.

## Credentials and Network Boundaries

- Keep long-lived issuing keys on the Controller. Do not give them to personal users, Agents, or Proxies, or put them in screenshots, tickets, repositories, or shell history.
- HyperFileLens requests temporary AK/SK/Token credentials, currently for `900` seconds by default. A provider's longer maximum is not a reason to issue long-lived sessions. Execution authorization and cloud-token lifetimes differ; long jobs use controlled renewal, not a single token made valid for days.
- Executors must reach the storage endpoint; the Controller must also reach IAM/STS. Supply public endpoints when components are outside the cloud network. A self-hosted address must be reachable by every intended executor, not just `localhost` on the Controller.
- Relevant components must trust the HTTPS certificate chain. Do not disable verification or make a bucket public to work around errors. See each self-hosted guide for the limited private-network HTTP exception.

## Defaults and Existing Members

An authorized operator adds connections under **Managed Storage → Object Storage**. New connections are enabled by default and need no separate activation step; the page does not offer a Disable action. If no default connection exists, saving a new connection automatically makes it the default. If a default already exists, adding another connection leaves it unchanged; after validation, use **More Actions → Set as Default** when the new connection should serve subsequent personal registrations.

To restore an existing Disabled connection, select it and use **More Actions → Enable** with connection-management permission. Before enabling it, the server checks temporary-credential issuance and read access again. If validation fails, the connection stays disabled and the existing default is unchanged. Successful recovery preserves an existing default; if none exists, the restored connection automatically becomes the default. Follow the validation procedure on this page and confirm the production default before enabling personal registration.

::: warning Keep personal registration disabled during initial setup
Saving the first connection can make it available to new registrations immediately, before write access, initialization, and restore have been tested. Keep personal registration disabled during initial setup and validate with an existing test member, or use a controlled test environment. Confirm the production default and complete validation before enabling personal registration. A saved connection's Enabled status is not proof that these tests passed.
:::

Personal registration depends on an enabled default connection. Configure storage rather than exposing storage-administration pages to ordinary users.

New personal registrations provision a repository using the default at that time. Adding a connection does not automatically backfill every historical member. Open **Backup Repositories → More Actions → Provision Missing Repositories** and use the actual preview: only eligible members without a repository are included. Owners, inactive members, and other ineligible accounts may be excluded; the count need not match total organization membership.

Backfill is neither initialization retry nor undelete. Use **Retry Initialization** for a failed existing repository and investigate removing or retained records according to their state. Changing the default does not migrate existing repositories or remove their cloud data.

### Repository Counts and Historical Records

The Repositories column in Object Storage counts current, undeleted repository bindings and excludes deleted historical records. Creating, create-failed, removing, and remove-failed repositories remain included until marked deleted.

A count of zero does not necessarily allow connection deletion. Historical bindings may still support cleanup, auditing, and retained-data management. While those bindings exist, the system continues to protect the connection and its issuing configuration; a zero count neither removes history nor overrides deletion safeguards.

## Verify Before Rollout

| Stage | Check | What it does not prove |
| --- | --- | --- |
| Save connection | Issue temporary credentials and list within a repository prefix; failure does not save or replace the default | Object writes, maintenance, and deletion are not yet validated |
| Initialize repository | Independent location, password, and actual repository initialization succeed | Not connectivity from every source, or long-job renewal |
| Back up and restore | A test member restores matching file contents; another ordinary member cannot access that repository | Not compatibility with every version or cloud environment |
| Lifecycle | Where required, test maintenance, renewal, cancellation, and physical cleanup on disposable data | Deletion is not reversible; do not run destructive tests on production repositories |

Keep personal registration disabled while preparing and validating the initial connection, or perform validation in a controlled test environment. The first saved connection is automatically the default when none exists; do not assume there is a separate activation step. Complete the tests above and confirm the production default before enabling personal registration. Record provider, server version, region, endpoint type, policy revision, and results. These guides describe supported configuration procedures. Complete the validation above in the target environment before production rollout.

## Configuration Preparation and Credential Management

Storage administrators create the bucket, configure the issuing identity, and apply least-privilege policies. Operators with Managed Storage administration permissions add connections, validate repositories, and configure the default connection. Each provider guide ends with a configuration checklist covering authorization requirements and required configuration details.

For an initial deployment, create a dedicated, empty private bucket before adding the connection. No pre-uploaded objects or repository folders are required. Configure issuing keys on the Controller through a secure channel; do not include them in ordinary tickets, documentation, or source control, or distribute them to personal users or executors. HyperFileLens issues temporary credentials as needed; manually prepared tokens and cloud root-account passwords are not required.
