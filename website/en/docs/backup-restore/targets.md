---
title: Manage Target Storage
description: Configure and validate object storage, NAS, and Proxy local-disk repositories.
---

# Manage Target Storage

Target storage holds backup repositories. Choose it based on reachability from the source, capacity, retention requirements, and restore-read performance.

Open **Protection → Backup Wizard → Target Storage** and select **Add Repository**. After creating a repository, assign it to a backup source; appearing in the repository list does not mean that a backup configuration already uses it.

## Object storage

HyperFileLens supports AWS S3, Alibaba Cloud OSS, Huawei Cloud OBS, and supported S3-compatible services. Select the provider and configure the fields shown by the form.

![Object-storage provider selection in Add Repository with account information blurred](/docs/getting-started/select-huawei-cloud.png)

Object-storage settings commonly include **Endpoint**, **Region**, **Bucket**, **Object Prefix**, **Access Key**, and **Secret Key**. Grant credentials only the permissions required for the repository scope, and dedicate the object prefix to this HyperFileLens repository.

Access keys and secret keys are secrets. Keep them out of screenshots, use synthetic values, or cover the complete value with opaque pixels. Blurring is not safe for credentials.

![Huawei Cloud OBS repository form with account, bucket, object prefix, and credentials redacted while public endpoint, region, and SSL settings remain visible](/docs/getting-started/configure-huawei-repository.png)

## NAS

A NAS target can be bound to a Proxy that mounts the share and provides repository access. The source, Proxy, and NAS must have a working network path and the required protocol permissions.

## Proxy local disk

Choose a dedicated absolute path on the Proxy. Do not use a system temporary directory, another application's directory, or a location containing existing business data. Reserve enough capacity for snapshot growth and restore reads.

## Validate the repository

Before using the repository, confirm:

- connection and write validation succeed;
- the physical location is not already used by another repository;
- credentials, TLS, DNS, and system time are correct;
- capacity covers expected growth and retention.

Return to **Target Storage**, confirm that **Connectivity** is **Online**, and then assign the repository under **Backup Configuration**.

![Created Huawei Cloud OBS repository with Connectivity Online and account, repository name, bucket, and object prefix blurred](/docs/getting-started/huawei-repository-created.png)

If validation fails, check the endpoint, region, credential scope, TLS, DNS, system time, and the data path from the source or Proxy. Do not create multiple repository records for the same physical bucket and object prefix.
