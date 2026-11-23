# SourceLens 0.63.8 upgrade

## Scope and deployment boundaries

- Upgrade the pinned source from v0.63.4 to v0.63.8, including API, workers,
  scheduler, frontend, and the Gateway LensNode payload.
- Keep the functional patch series empty. Do not patch SourceLens application
  code or reproduce its retry, failover, cleanup, concurrency, or evaluation
  services in HFL.
- Local development and ordinary release CI continue building pristine
  SourceLens source with its Compose build definitions. Normalize the actual
  Compose output images to HFL distribution tags. Source-built digests are not
  expected to match official image digests.
- Enterprise SaaS Upgrade continues using the original official images pinned
  by `deploy/online/sourcelens/runtime.json`. The CN and global images have
  matching manifests for this release; runtime local aliases remain unchanged.
- v0.63.8 changed the upstream Compose image namespace to Aliyun ACR. This is
  a source-build output naming change, not a reason to pull application images
  during a local source deployment. Existing mirror, offline, incremental,
  force-build, and no-cache behavior remains in place.
- Preserve the existing HFL Chat composer, per-Chat choices, model settings,
  and citation rendering. Limit progress changes to numbered plan steps,
  timestamp-backed step durations, and a readable model-stream failure message.
  History without timestamps must not acquire fabricated durations.

## Upstream behavior reused

The version upgrade brings evidence-loop termination, MCP output recovery,
stale LensNode run cancellation, unchanged-file detection, empty-model-response
failover, retention fixes, datasource concurrency controls, and reduced admin
list read amplification. These remain SourceLens-owned features and settings.
Do not add HFL-side retry loops or tune upstream concurrency globally as part of
this upgrade.

The upstream Agent Evaluation foundation and migration are included. HFL adds
no evaluation page, scheduler, or automatic evaluation-pool provisioning.
Existing user sessions and LensNodes retain the upstream default `source=user`
and `pool=default` values. Keep HFL's explicit Celery memory settings unchanged
rather than silently adopting a larger upstream process limit.

## Deployment and rollback checklist

1. Record the installed source identity, image references, configuration, and
   Gateway versions. Plan a maintenance window for in-flight runs.
2. Before any new container starts, back up the SourceLens PostgreSQL database,
   persistent storage/attachments/deliverables, configuration, and encryption
   keys. Keep a matching HFL database/configuration backup and record the
   point-in-time boundary. Verify that the backup can be restored.
3. Rehearse against isolated copies. Apply the previous release's migrations,
   then upgrade with v0.63.8 (including `lens.0068_evaluation_foundation`).
   Confirm existing user sessions/runs remain user-sourced, existing LensNodes
   remain in the default pool, and no pending migrations remain.
4. Check API health, frontend availability, worker/scheduler readiness, Gateway
   enrollment/heartbeat and TLS capability. Publish the matching LensNode bundle
   through the existing lifecycle; do not automatically upgrade production
   Gateways.
5. Smoke-test session creation/list/history, attachments, stream/reconnect,
   plan progress, clarification, cancellation, citations, model testing and
   enablement, and normal/force cleanup. Verify evaluation traffic does not
   enter the normal user session list. Real model/Gateway tests need an
   explicitly authorized isolated environment and credentials.
6. For rollback, stop new work, restore the previous images **and matching
   database/persistent-data/configuration backups**, then validate health and
   Gateway compatibility. Switching image tags alone is not a validated rollback
   across a schema migration. Account for writes since the backup.

## Verification layers

Run shell syntax/release contracts, source-build image-reference tests, local
and release lifecycle tests, SaaS delivery/runtime-lock tests, and frontend
type/lint/unit/build checks. Also perform real source image builds and an
isolated PostgreSQL migration/API smoke test. Mocked script tests do not replace
those deployment checks, and a migration/API smoke test does not replace
end-to-end Chat/model/Gateway validation.
