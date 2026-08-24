# Blue/green deployment contract

## Runtime ownership

The release installer is the current single-host deployment driver. It changes
only resources owned by the `hyperfilelens` Compose project and keeps these
components outside the color lifecycle:

- PostgreSQL and Redis;
- stable Nginx and its host ports;
- Worker and leader-elected Scheduler;
- Platform Gateway, Proxy, and user-installed Data Gateways;
- external SourceLens, and bundled SourceLens when its bundle is unchanged.

`api-blue`/`api-green` and `web-blue`/`web-green` are stateless color pools.
Web serves the Tenant SPA, Admin SPA, and Website on three internal listeners;
only stable Nginx publishes `11442`, `11443`, and `11444`.

## Upgrade state machine

1. Take and verify a managed backup while the active color stays online.
2. Load verified target images and stop the singleton Scheduler.
3. Merge the release environment schema and apply final deployment settings.
4. Run one migration/collectstatic/periodic-registration job.
5. Start and directly health-check the inactive API/Web color.
6. Atomically replace `hfl-active-upstreams.conf`, validate Nginx, and reload it.
7. Close old Daphne sessions with WebSocket code `1012`; Agents reconnect through
   stable Nginx. Running backup/restore nodes must reattach to a non-retired
   instance before the old color can retire.
8. Gracefully hand off Worker work, start Worker and Scheduler, then require the
   final host-local HFL health gate. Only now commit `active-color`; failures
   before this point restore the previous upstream and color.
9. Remove the retired API/Web color. If the bundled SourceLens fingerprint
   changed, independently block new AI Runs, drain recorded active Runs, and
   upgrade SourceLens only after HFL is healthy. On failure, the maintenance
   gate is cleared only after SourceLens recovery passes its health check; an
   incomplete recovery remains fail-closed until the gate's fail-safe lease
   expires.

The recovery trap never recreates the previous color using target image
metadata. Before traffic is committed, a failure restores the previous upstream
and starts only already-existing stable/color containers. It then drains the
candidate Daphne pool, requires active backup/restore Agents to reattach to the
restored route, and removes the candidate API/Web pool. During the one-time
legacy topology transition, the target Web pool remains because the legacy
runtime has no separately addressable Web service. Database backups are never
restored automatically.

## Durable task continuity

- Agent task commands and terminal results are durable and ACKed.
- Running Agent backup/restore progress is periodically persisted to Agent
  SQLite and replayed after reconnect.
- Celery uses late acknowledgement and prefetch one; Worker receives a ten-minute
  graceful stop before unfinished work is returned to the queue.
- Restore terminal NodeTasks are periodically reconciled into item events and
  product-task finalization. The repair is idempotent and repository-server stop
  commands are not duplicated.
- Scheduler replicas use one PostgreSQL advisory-lock leader.

## Schema and release rules

Database migrations must use expand/contract ordering. The previous API remains
live while target migrations run, so destructive schema cleanup belongs in a
later release. A release that cannot run both adjacent application versions
against the expanded schema is not blue/green safe.

`active-color`, `deployment-state`, and
`deploy/nginx/snippets/hfl-active-upstreams.conf` are local execution cache. A
managed backup includes them for diagnosis and recovery.

## Multi-host boundary

Compose currently drives one host, but color services have no fixed container
names or host ports, WebSocket drain is instance-scoped, and Scheduler leadership
is cluster-safe. A future fleet driver must store deployment intent/history in
PostgreSQL, coordinate singleton migrations, update an external load balancer,
drain every old Daphne instance, and provide shared or object-backed media,
static, language-pack, and Agent-release storage. Those fleet responsibilities
must not be inferred from the local execution-cache files.

The product version, application-image identity, and stable entry image have
separate lifecycles. The release manifest supplies the Backend and Frontend
image references, while `HFL_GATEWAY_VERSION` pins the stable Nginx image
present at first installation (or the first blue/green upgrade). A later
`install.sh start` therefore cannot unexpectedly replace the public entry.
The generated active upstreams use Docker's embedded resolver (`127.0.0.11`)
and shared Nginx zones, so a continuously running gateway follows address
changes when Compose recreates the active API or Web pool. Blue/green cutover
still performs an explicit `nginx -t` and graceful reload so route changes are
validated before traffic moves. Updating the gateway image itself requires a
separately planned entry-layer maintenance action; zero-downtime gateway-image
replacement requires an external load balancer or a host-level dual-entry
driver.
