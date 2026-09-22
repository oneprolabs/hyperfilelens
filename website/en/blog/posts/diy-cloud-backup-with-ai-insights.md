---
layout: hfl-blog-post
title: "Give Kopia an AI Brain"
description: "Kopia's dedup and encryption are solid, but its CLI runs on memorized flags and KopiaUI isn't much better — once something's backed up, it disappears into a black box. HyperFileLens builds its Agent directly on Kopia, collapses setup into a few clicks, and wires in an AI engine (SourceLens) that reads snapshots directly. A full hands-on walkthrough, from deployment to asking AI questions about your own backups."
author: "Ray Sun"
date: 2026-09-09T08:00:00+08:00
tags:
  - "AI"
  - "Backup"
  - "Kopia"
  - "Object Storage"
  - "Open Source"
  - "HyperFileLens"
---
Kopia is a mature open-source backup engine — about **14.1k GitHub stars**, **Apache 2.0** licensed. It does content-addressed storage, incremental snapshots, dedup, end-to-end encryption, and backs up to S3, NAS, or local disk. Put simply: **backing up files safely and efficiently is a problem Kopia already solved.**

But Kopia is a **backup engine**, not a full backup management product: there's no concept of "multiple hosts" — every machine runs its own CLI or KopiaUI, blind to the others. Even managing multiple repositories from one UI on a single machine has been an open request since 2023 ([kopia/kopia#2976](https://github.com/kopia/kopia/issues/2976)).

**HyperFileLens doesn't reinvent the backup engine — it adds a product layer on top of Kopia.** Kopia still handles backup, dedup, encryption, Snapshot, and Restore. HyperFileLens pulls multiple hosts, storage targets, tasks, and snapshots into one web console, and collapses setup into three steps — register a host, configure backup and restore, start backing up. Ten minutes, no Repository, Policy, or CLI flags involved.

On top of that, HyperFileLens adds something Kopia never had: AI. It extends the chain from **File → Snapshot** to **File → Snapshot → Search / Read / Reason → Answer** — instead of manually hunting through snapshots and directories, you just ask in plain language, and the AI searches, reads, and reasons over the raw files (docs, spreadsheets, slides, images, code), returning an answer with a citation. It's always reading the backup copy, never the live production data.

So HyperFileLens does two things: **use Kopia for reliable backup, use AI to make that backup data useful again.** This post runs through the whole chain from scratch — register a host, set up backup and restore, produce a snapshot, wire up AI, and end by asking questions about your own backed-up data.

## What you'll need

- A Linux host that can run Docker (x86; 4 cores / 8GB RAM is enough for small-scale use, 8 cores / 16GB RAM recommended for larger teams; 100GB disk), with outbound internet access to reach the model API — no public IP needed;
- An object storage account — anything S3-compatible works (Alibaba Cloud OSS, Huawei Cloud OBS, AWS S3, etc.);
- A host to back up — Linux, Windows, and macOS are all supported;
- Credentials for a language model. DeepSeek-V4-Flash is a good default; for image recognition, add DeepSeek's latest multimodal model, **DeepSeek-V4-Flash-Vision-Exp** (model ID: `deepseek-v4-flash-vision-exp`).

The whole flow: **deploy HyperFileLens → back up your data → get insights from it.**

## Step 1: Set up the host and install HyperFileLens

| Item | Requirement |
| --- | --- |
| OS | Ubuntu 24.04 (x86_64) |
| CPU / RAM | 4 cores / 8GB for small-scale use; 8 cores / 16GB recommended for larger teams |
| Disk | 100GB (working space AI uses when processing files during Insights — scale up if you're backing up more) |
| Network | Outbound internet access (to reach the model API); no public IP required |
| Ports | 11442–11445/TCP (`11443` is the main console, `11444` is the model admin panel) — internal network only |
| Dependencies | Docker Engine 24.0.0+, Docker Compose V2 2.20.0+ |

SSH in, install Docker, then run the install script. If you're in mainland China, GitHub itself is the bottleneck — the `curl` step needs to hit Gitee instead, since no `--mirror` flag fixes that:

Mainland China:

```bash
curl -fsSL \
  https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn --yes
```

Everywhere else:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror global --yes
```

The installer prints two URLs and an initial email/password, using the host's LAN IP. If the LAN IP is `172.30.164.250`, you'll see:

- `HyperFileLens · http://172.30.164.250:11443`
- `Platform Ops · http://172.30.164.250:11444`

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/install-complete-terminal.webp" alt="Terminal output after the install script finishes, showing the HyperFileLens and Platform Ops access URLs and the initial email/password" loading="lazy" />
  <figcaption>What the installer prints when it's done</figcaption>
</figure>

Open the `11443` address, sign in with that email and password, and change the password right away. Check the timezone matches the system clock while you're there.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/console-overview-en.webp" alt="The HyperFileLens console home page after login, showing the data protection pipeline across production source, target storage, and recovery drill" loading="lazy" />
  <figcaption>The console home page after logging in</figcaption>
</figure>

## Step 2: Set up the NAT-facing address and the AI model

Registering hosts, adding storage, and generating Agent install commands later all use this host's externally reachable address; the AI assistant also needs a model configured before it's usable. Neither is part of the backup flow itself, but both need to be done before you start adding data sources — change them later and you'll have to re-register.

**External access address**: if this host is on public cloud, the installer prints the internal IP, but the address actually reachable from outside is the cloud provider's NAT or public IP — fix that first. Open `Platform Ops`, go to **External Access**, and set it to the real reachable public address. If you're only using this on a LAN, skip this step.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/platform-external-access-en.webp" alt="The External Access settings page, showing the access configuration input, the currently effective address, and a suggested public address" loading="lazy" />
  <figcaption>On public cloud, point external access at your public IP</figcaption>
</figure>

**AI model**: still in `Platform Ops`, go to **AI Engine → AI Models**, click **Add AI Model**. DeepSeek is right there as a provider — no need to route through another gateway.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/add-ai-model-provider-en.webp" alt="The Add AI Model provider list, with OpenAI, DeepSeek, DashScope (Qwen), Anthropic and other major providers all available" loading="lazy" />
  <figcaption>DeepSeek is right there in the provider list</figcaption>
</figure>

Add **DeepSeek-V4-Flash** (model ID: `deepseek-v4-flash`) for text; if you need image recognition, add **DeepSeek-V4-Flash-Vision-Exp** (model ID: `deepseek-v4-flash-vision-exp`) too. Base URL is `https://api.deepseek.com` — set the API key, and save once **Test Connection** passes.

## Step 3: Back up your data — add the host directory you want protected

Go to **Protection → Backup Wizard**, click **Add Source**, choose **Source Host**, and pick Linux as the OS (Windows and macOS work the same way).

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/add-source-select-os-en.webp" alt="The Add Backup Source page, with Source Host selected and Linux chosen as the target operating system" loading="lazy" />
  <figcaption>Pick the OS, copy the install command</figcaption>
</figure>

It gives you an install command — copy it to the target host's terminal and run it. Back in the Backup Wizard, refresh the source list and confirm the new host shows **Registered** and **Online**.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/backup-source-registered-en.webp" alt="The Backup Wizard source list, showing the newly registered Linux host as online and registered" loading="lazy" />
  <figcaption>Host registered, showing online</figcaption>
</figure>

## Step 4: Backup setup

1. Click **Create Backup Configuration** — a five-step wizard: Sources → Backup Policy → Target → Restore Plan → Review.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/create-backup-configuration-en.webp" alt="The Create Backup Configuration wizard, with Sources, Backup Policy, Target, Restore Plan, and Review steps on the left, and a host directory tree with path selection on the right" loading="lazy" />
  <figcaption>Create Backup Configuration: Sources → Backup Policy → Target → Restore Plan → Review</figcaption>
</figure>

2. The Target step needs an object storage repository: create a Bucket and a sub-account in your storage console (grant it only read/write/list on that Bucket), and get an Access Key / Secret Key. Back in HyperFileLens, click **Add Object Storage Repository**, pick a platform (Huawei Cloud / Alibaba Cloud / AWS have presets; anything else, pick **S3-Compatible Storage**), fill in the endpoint, region, AK/SK, bucket, and object prefix, then save and verify. The secret key is only shown once — don't leave it in a screenshot anywhere.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/add-object-storage-repository-en.webp" alt="The Add Object Storage Repository form, showing Huawei Cloud, Alibaba Cloud, AWS, and S3-Compatible Storage as preset platforms, plus endpoint, region, access key, and secret key fields" loading="lazy" />
  <figcaption>Pick any storage platform — the fields are mostly the same</figcaption>
</figure>

3. Point Target at this repository, leave Backup Policy and Restore Plan on their defaults, and save on Review.

4. Click **Backup Now**, and wait for the status to hit **Succeeded**.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/backup-task-succeeded-en.webp" alt="Step 3 of the Backup Wizard, showing two hosts' backup tasks both with a Succeeded status" loading="lazy" />
  <figcaption>Backup task done, status Succeeded</figcaption>
</figure>

Storage cost is basically negligible (Alibaba Cloud OSS standard storage runs around $0.017/GB per month). Egress only happens on restore: full the first time, incremental after that, so it's small to begin with — and AI questions read from the already-restored copy, so they don't trigger another download.

## Step 5: Ask AI, see it work

The backup task shows **Succeeded** — no need to separately verify it. AI pulling the right answer out of the snapshot is the most direct proof it works. If you actually need to restore something, click **Restore** and follow the flow; not covered here.

Go to **Insights → AI Copilot** — you'll see your chat history on the left. Click **New Chat**.

Pick a data source and snapshot, then add the files you want analyzed. PDF, DOCX, PPTX, and XLSX are all supported, and it recognizes images inside files as well as standalone image files — it's working off the backup snapshot, not live production data. (This is why step 2 had you add a multimodal model — it makes recognition more accurate.)

Set the analysis type to **Knowledge Q&A**, and data privacy to **Public Data Gateway** (the platform's own gateway — nothing extra to deploy).

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/ai-copilot-new-chat-analysis-en.webp" alt="AI Copilot's New Chat setup, with analysis type set to Knowledge Q&A (Recommended) and data privacy set to Public Data Gateway, next to a summary panel showing the chosen backup source and snapshot" loading="lazy" />
  <figcaption>Knowledge Q&A for analysis type, Public Data Gateway for data privacy</figcaption>
</figure>

Click **Start Chat**, and once it's ready, just ask. Say you've backed up a copy of *Journey to the West* — you could ask: "Some demons want to eat Tang Sanzang's flesh, others want to marry him — what's the difference in what they're after?" The AI answers straight from the backed-up file, with a citation back to the source.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/ai-copilot-answer-en.webp" alt="The AI Copilot chat interface, answering a question about a backed-up document with a structured response and a table comparing two categories, citing the source file" loading="lazy" />
  <figcaption>Ask a question directly against your backup — the answer cites its source</figcaption>
</figure>

## Kopia's got a brain now

Kopia itself hasn't been touched — dedup, encryption, and incremental backup all work exactly as before. What changed is the layer on top: you're not checking status host by host on the command line anymore, setup is a few clicks instead of memorized flags, and backup data isn't just sitting there waiting for a disaster — AI can read it, question it, reason about it, any time you want an answer.

Start to finish — deploy, back up, ask AI a question — this takes about an hour or two. After that, schedule the backups and forget about them.

## Open Source

HyperFileLens is Apache 2.0 licensed:

- Main project: <https://github.com/oneprolabs/hyperfilelens>
- AI engine, SourceLens: <https://github.com/oneprolabs/sourcelens>
- Issues: <https://github.com/oneprolabs/hyperfilelens/issues>

Don't want to self-host? The free SaaS version at <https://hyperfilelens.com> skips deployment entirely — just add a data source and go.

## Join the OneProLabs Open-Source Community

Running into issues, want to contribute, or just want to talk shop about the project — find us here:

- GitHub: <https://github.com/oneprolabs/hyperfilelens>
- Twitter/X: [@oneprolabs](https://twitter.com/oneprolabs)

If you use WeChat, you're also welcome to join our WeChat group — just scan the QR code below with WeChat.

<figure>
  <img src="/images/backups-help-ai-understand-business/wechat-group-qrcode-only.webp" alt="QR code for the OneProLabs open-source WeChat group" loading="lazy" />
  <figcaption>Scan with WeChat to join the OneProLabs group</figcaption>
</figure>
