---
title: View AI Usage
description: Understand the AI usage information currently available to tenant users.
---

# View AI Usage

The current tenant release does not expose an **AI Usage** item in the Insights navigation. Opening the legacy `/insight/usage` route returns to **AI Copilot**. Do not rely on that route as an operational usage report.

## What tenant users can check

Tenant users can currently observe usage-related conditions through:

- readiness and capacity messages on **New Chat**;
- Public or Private Data Gateway availability;
- queue position while waiting for a Gateway;
- preparation and run states in the Chat;
- organization-capacity or model-unavailable errors.

These indicators help explain whether a Chat can run, but they are not a billing statement or complete usage history.

## When usage reporting is required

Contact the platform administrator for provider billing, organization quota, model-token totals, or historical run accounting. When reporting a discrepancy, include the organization, Chat time, session name, run status, and visible error without including questions, answers, file content, credentials, or registration commands unless explicitly required through an approved support channel.

AI capacity limits affect Insights runs; they do not invalidate existing backups or restore points.
