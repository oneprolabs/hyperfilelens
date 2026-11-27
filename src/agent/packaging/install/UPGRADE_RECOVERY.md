# Agent upgrade verification and recovery

The Unix installer waits for local service readiness, not a second Agent process
opening SQLite during startup. Readiness is bound to the Agent Root, executable,
PID, process creation identity, version, and commit. It is published only after
local startup recovery. It does not require Internet access or a control-plane
connection. The installer allows up to 120 seconds for readiness and requires a
stable service instance before committing the upgrade.

Old Agents do not publish readiness. When rolling back to an older Agent, the
incoming installer checks the restored database while the service is stopped,
then verifies the restored service process and build identity. It does not run
`tasks list` while that service is starting.

## Failed upgrades

Each detached upgrade handled by the new installer can retain private evidence
under `<Agent Root>/lifecycle/attempts`. Evidence is bound to the uniquely matched
accepted task execution and the verified target package. Failure updates the
existing local task-result outbox without opening another WebSocket or changing
the database schema. For older Agents that send upgrade handoff as a running
result, update the control-plane WebSocket handlers before rolling out the Agent
package. The controller persists that handoff without acknowledging it as a
terminal result. Current Agents send handoff as task progress instead, so it
cannot consume a later installer failure's acknowledgement.

An installer-local success is not complete lifecycle success. Control-plane
session, inventory, and build verification still apply. Data Gateways must also
complete the sidecar upgrade before the complete upgrade can succeed.

Do not delete `upgrade-state.json`, `FAILED`, or `backup/rollback` to make an
upgrade retry appear successful. An online old Agent is not by itself proof of
successful rollback verification.

## Retry from the console without SSH

When a Linux or macOS Agent is still online, retry the upgrade using a target
package that supports automatic rollback verification. The incoming installer
works with older Agents that already stage and execute the installer from the
incoming package. For `rollback_failed` (including legacy records) or
`rollback_verification_pending`, it verifies the restored installation under the
same lifecycle lock used for the upgrade. If verification passes, it records
`rolled_back` and continues the requested upgrade. No force option or separate
command on the host is needed.

Restoration failure and incomplete verification are recorded separately. If any
verification fails, the retry stops before deploying the new build and retains
rollback materials. The console receives a diagnostic for the relevant recovery
check. Database integrity checks have individual 90-second limits within a
four-minute overall verification budget; a timeout is reported separately
from a verification error. Retry in a quieter window if the host is busy.

Already-installed 0.3.27 Agents can reject an existing `rollback_failed`
transaction before downloading the target package. The target installer cannot
change that old preflight. Use the explicit verification command below for
those hosts. For account installations, continue to use the administrator-run
installer; remote upgrade is intentionally unavailable, and maintenance checks
run with the configured ordinary service account where required.

An unreachable Agent cannot receive a console retry. Use the documented
out-of-band process if it is offline or if its saved materials do not match. Do
not attempt to force an upgrade over a partially restored database. Windows keeps its existing operator-recovery entry points; restore snapshot
validation also applies to Windows before service stop.

## Explicit rollback verification (operator fallback)

Use the installer extracted from a **trusted fixed release package**, not the
older installed installer. Run it as the same installation principal and with
the same installation mode as the original Agent. For a system installation:

```bash
sudo /path/to/extracted-fixed-package/install.sh recover-upgrade \
  --from /path/to/fixed-package.tar.gz
```

For a user-scoped installation, invoke the extracted installer as the owning
user with the original `HFL_INSTALLATION_MODE` (without `sudo`).

This operation acquires the lifecycle lock and verifies:

- The existing transaction is `rollback_failed` or `rollback_verification_pending`
  and its previous version matches the retained rollback metadata.
- Restored binaries, manifest, and version marker match the retained snapshot.
- Configuration matches the snapshot, both current and saved databases pass a
  read-only integrity check, and durable repository aliases match.
- The service definition/resource policy matches its retained snapshot, and the
  restored service passes identity and stability verification.

It does **not** restore or replace a live database, restart the service, perform
another upgrade, or delete the rollback snapshot. If verification succeeds, the
transaction becomes `rolled_back`; a later separately requested upgrade can
perform the normal verified cleanup. Any mismatch fails closed and retains the
recovery materials for operator investigation. If the old service is inactive
or the restoration is incomplete, resolve that condition explicitly before
retrying verification.

An automatic or explicitly interrupted restore requires both `agent.env` and
`agent.db`, valid prior binaries and manifest, and any required service
snapshot. The installer checks archive contents and SQLite integrity in private
staging before stopping a service, then restores only that verified copy. A
missing optional legacy configuration is not a deletion instruction. If the
database has never been initialized, start the old installation before upgrading.

Console upgrades pin the manifest build to the task's expected commit when
one is supplied. The managed staging slot also requires a matching active
detached task. For an explicitly authorized standalone upgrade, use the trusted
package from a path outside the managed staging slot.

The installer verifies and runs from a private workspace; reuse of a verified
unpacked input package does not require re-extraction. Incomplete rollback
inputs must not cause the installer to stop an existing service, and a failed
restore is not a reason to start a partially restored service. A legacy Unix
runner may still attempt its old unconditional service recovery after the
installer exits; update the runner before relying on the exit-70 guard.

## Diagnostics and compatibility

Task results retain the original installer phase, exit code, attempt identity,
and rollback outcome. The control plane recognizes these additive error codes:

- `AGENT_UPGRADE_STARTUP_FAILED`
- `AGENT_UPGRADE_INSTALL_FAILED`
- `AGENT_UPGRADE_RECOVERY_REQUIRED`

Legacy Agents and unknown codes continue to use the existing generic failure
fallback. Never include credentials, process environments, or complete command
lines in diagnostics. Cache-usage conflicts expose only a safe process category
and PID.
