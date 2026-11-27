//go:build !windows

package install

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestStopServiceRecognizesInactiveNonzeroExitWithoutWaiting(t *testing.T) {
	body := readPackagingInstallShell(t)
	script := shellFunction(t, body, "stop_service") + `
agent_uses_launchd() { return 1; }
systemctl() { :; }
hfl_systemctl() {
 case "$1" in
 stop) touch "$STOPPED" ;;
 is-active) if [[ -f "$STOPPED" ]]; then printf 'inactive\n'; return 3; else printf 'active\n'; fi ;;
 esac
}
sleep() { echo UNNECESSARY_WAIT; exit 99; }
log_step() { :; }
log_ok() { echo STOPPED_OK; }
log_warn() { :; }
log_skip() { :; }
log_fail() { echo "$1"; exit "${2:-1}"; }
stop_service
`
	cmd := exec.Command("bash", "-c", "set -eu\n"+script)
	cmd.Env = append(os.Environ(), "STOPPED="+filepath.Join(t.TempDir(), "stopped"))
	out, err := cmd.CombinedOutput()
	if err != nil || !strings.Contains(string(out), "STOPPED_OK") {
		t.Fatalf("stop failed or waited unnecessarily: %s %v", out, err)
	}
}

func TestStopServiceUnknownStateFailsClosed(t *testing.T) {
	script := shellFunction(t, readPackagingInstallShell(t), "stop_service") + `
agent_uses_launchd() { return 1; }
systemctl() { :; }
hfl_systemctl() { echo unknown; return 4; }
log_fail() { echo "$1"; exit "$2"; }
stop_service
`
	out, err := exec.Command("bash", "-c", "set -eu\n"+script).CombinedOutput()
	if err == nil || !strings.Contains(string(out), "Unable to determine") {
		t.Fatalf("unknown service state was accepted: %s %v", out, err)
	}
}

func TestUpgradeHealthUsesReadinessAndNeverTasksList(t *testing.T) {
	root := t.TempDir()
	bin := filepath.Join(root, "bin")
	if err := os.Mkdir(bin, 0700); err != nil {
		t.Fatal(err)
	}
	stub := `#!/usr/bin/env bash
case "$1" in
version) echo 'hyperfilelens-agent test (abcdef)' ;;
help) echo 'lifecycle ready' ;;
lifecycle) [[ "$2" == ready ]] || exit 88 ;;
*) echo 'unexpected database probe' >&2; exit 99 ;;
esac
`
	if err := os.WriteFile(filepath.Join(bin, "hfl-agent"), []byte(stub), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "MANIFEST.json"), []byte(`{"agent_commit":"abcdef"}`), 0600); err != nil {
		t.Fatal(err)
	}
	script := shellFunction(t, readPackagingInstallShell(t), "upgrade_health_check") + `
agent_manages_service() { return 0; }
agent_uses_launchd() { return 1; }
hfl_systemctl() { if [[ "$1" == show ]]; then echo MainPID=42; fi; }
uname() { echo Linux; }
run_as_upgrade_account() {
	if [[ "$1" == test ]]; then
		[[ "$2" == /proc/42/exe && "$3" == -ef && "$4" == "$INSTALL_DIR/hfl-agent" ]]
	else
		"$@"
	fi
}
process_start_marker() { echo fixture-process-start; }
sleep() { :; }
log_ok() { echo "$1"; }
log_warn() { echo "$1"; }
verify_upgrade_database_offline() { echo UNEXPECTED_OFFLINE_PROBE; return 1; }
upgrade_health_check "$ROOT" test 1
`
	cmd := exec.Command("bash", "-c", "set -eu\n"+script)
	cmd.Env = append(os.Environ(), "ROOT="+root, "INSTALL_DIR="+bin, "MANIFEST_FILE="+filepath.Join(root, "MANIFEST.json"), "HFL_UPGRADE_HEALTH_SECONDS=2")
	out, err := cmd.CombinedOutput()
	if err != nil || !strings.Contains(string(out), "local health check passed") {
		t.Fatalf("readiness check failed: %s %v", out, err)
	}
}

func TestInstallerCapturesOldRunnerFailureBeforeDeletingHelper(t *testing.T) {
	body := readPackagingInstallShell(t)
	start := strings.Index(body, "cmd_upgrade() {")
	end := strings.Index(body[start:], "\ncollect_agent_mount_points() {")
	section := body[start : start+end]
	capture := strings.Index(section, "lifecycle begin")
	if capture < 0 || capture > strings.Index(section, `recover_interrupted_upgrade "${data_dir}" "upgrade"`) {
		t.Fatal("old-runner bridge cannot capture blocked pre-stop failure")
	}
	for _, line := range strings.Split(section, "\n") {
		if strings.Contains(line, "trap 'rc=$?;") && strings.Contains(line, "cleanup_upgrade_workspace") && strings.Index(line, "record_upgrade_failure") > strings.Index(line, "cleanup_upgrade_workspace") {
			t.Fatal("helper deleted before failure bridge")
		}
	}
}

func TestUpgradeAutomaticallyVerifiesSavedRollbackBeforeProceeding(t *testing.T) {
	body := readPackagingInstallShell(t)
	functions := shellFunction(t, body, "recover_interrupted_upgrade") + "\n" +
		shellFunction(t, body, "verify_saved_upgrade_rollback") + "\n" +
		shellFunction(t, body, "fail_rollback_verification")
	for _, tc := range []struct {
		name, phase, check, want string
		blocked                  bool
	}{
		{"legacy state", "rollback_failed", "pass", "CONTINUE_UPGRADE", false},
		{"verification pending", "rollback_verification_pending", "pass", "CONTINUE_UPGRADE", false},
		{"config mismatch", "rollback_failed", "AGENT_UPGRADE_ROLLBACK_FILES_UNVERIFIED", "FAIL:AGENT_UPGRADE_ROLLBACK_FILES_UNVERIFIED:rollback_verification", true},
		{"timeout", "rollback_verification_pending", "AGENT_UPGRADE_ROLLBACK_VERIFICATION_TIMEOUT", "FAIL:AGENT_UPGRADE_ROLLBACK_VERIFICATION_TIMEOUT:rollback_verification", true},
		{"service mismatch", "rollback_failed", "service-mismatch", "FAIL:AGENT_UPGRADE_ROLLBACK_SERVICE_UNVERIFIED:rollback_verification", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			script := functions + `
agent_lifecycle_dir() { echo "$1"; }
agent_backup_dir() { echo "$1"; }
agent_uses_launchd() { return 1; }
hfl_systemctl() { [[ "$1" == is-active ]] || exit 99; echo active; }
load_upgrade_state() {
    UPGRADE_CURRENT_PHASE=$(cat "$ROOT/state")
    UPGRADE_PREVIOUS_VERSION=0.3.12
    UPGRADE_SERVICE_WAS_ACTIVE=0
}
write_upgrade_state() { printf '%s' "$2" > "$ROOT/state"; }
fixture_helper() {
    [[ "$*" == "lifecycle verify-rollback --data-dir $ROOT --version 0.3.12" ]] || exit 99
    case "$CHECK" in pass|service-mismatch) return 0 ;; *) echo "$CHECK"; return 2 ;; esac
}
upgrade_health_check() { [[ "$1" == "$ROOT" && "$2" == 0.3.12 && "$3" == 1 ]]; }
log_step() { :; }
log_ok() { :; }
log_fail() { echo "FAIL:$HFL_UPGRADE_ERROR_CODE:$HFL_UPGRADE_FAILED_PHASE"; exit "$2"; }
HFL_UPGRADE_HELPER=fixture_helper
INSTALLATION_MODE=system
UNIT_DST="$ROOT/unit"
GATEWAY_RESOURCE_DROPIN="$ROOT/absent"
mkdir -p "$ROOT/rollback/service"
printf '%s' "$PHASE" > "$ROOT/state"
printf snapshot > "$ROOT/rollback/service/hyperfilelens-agent.service"
printf snapshot > "$UNIT_DST"
if [[ "$CHECK" == service-mismatch ]]; then printf changed > "$UNIT_DST"; fi
recover_interrupted_upgrade "$ROOT" upgrade
[[ "$(cat "$ROOT/state")" == rolled_back ]]
echo CONTINUE_UPGRADE
`
			cmd := exec.Command("bash", "-c", "set -eu\n"+script)
			cmd.Env = append(os.Environ(), "ROOT="+root, "PHASE="+tc.phase, "CHECK="+tc.check)
			out, err := cmd.CombinedOutput()
			if (err != nil) != tc.blocked || !strings.Contains(string(out), tc.want) {
				t.Fatalf("unexpected result: %s (%v)", out, err)
			}
			state, err := os.ReadFile(filepath.Join(root, "state"))
			if err != nil || (tc.blocked && string(state) != tc.phase) {
				t.Fatalf("blocked verification mutated old state: %s (%v)", state, err)
			}
			if _, err := os.Stat(filepath.Join(root, "rollback/service/hyperfilelens-agent.service")); err != nil {
				t.Fatal("rollback material removed", err)
			}
		})
	}
}

func TestRollbackDistinguishesRestorationFailureFromIncompleteVerification(t *testing.T) {
	function := shellFunction(t, readPackagingInstallShell(t), "upgrade_rollback_on_error")
	for _, tc := range []struct{ failure, want string }{
		{"missing", "preserved"},
		{"restore", "rollback_failed"},
		{"verify", "rollback_verification_pending"},
		{"none", "rolled_back"},
	} {
		t.Run(tc.failure, func(t *testing.T) {
			script := function + `
DATA_DIR="$FIXTURE"
UPGRADE_BIN_BACKUP="$FIXTURE/rollback/bin"
mkdir -p "$UPGRADE_BIN_BACKUP"
if [[ "$FAILURE" != missing ]]; then echo binary > "$UPGRADE_BIN_BACKUP/hfl-agent"; echo archive > "$FIXTURE/rollback/latest.tar.gz"; fi
UPGRADE_CURRENT_PHASE=service_started
UPGRADE_PREVIOUS_VERSION=0.3.12
UPGRADE_STOP_ATTEMPTED=1
UPGRADE_DEPLOYMENT_STARTED=1
UPGRADE_STATE_SNAPSHOT_READY=1
UPGRADE_SERVICE_WAS_ACTIVE=1
UPGRADE_SERVICE_WAS_ENABLED=1
HFL_UPGRADE_ERROR_CODE=AGENT_UPGRADE_STARTUP_FAILED
write_upgrade_state() { echo "STATE:$2"; }
agent_backup_dir() { echo "$1"; }
log_warn() { :; }
log_fail() { exit "$2"; }
stop_service() { touch "$FIXTURE/stopped"; }
prepare_upgrade_rollback() { [[ "$FAILURE" != missing ]] || return 1; HFL_PREPARED_ROLLBACK="$FIXTURE/rollback"; }
restore_upgrade_binaries() { [[ "$FAILURE" != restore ]]; }
restore_upgrade_state() { :; }
restore_upgrade_service_definition() { :; }
verify_upgrade_database_offline() { :; }
start_service_only() { touch "$FIXTURE/restarted"; }
agent_manages_service() { return 0; }
upgrade_health_check() { [[ "$FAILURE" != verify ]]; }
cleanup_upgrade_rollback() { echo CLEANUP; }
upgrade_rollback_on_error 1
`
			cmd := exec.Command("bash", "-c", "set -eu\n"+script)
			fixture := t.TempDir()
			cmd.Env = append(os.Environ(), "FAILURE="+tc.failure, "FIXTURE="+fixture)
			out, err := cmd.CombinedOutput()
			wantState := strings.Contains(string(out), "STATE:"+tc.want+"\n")
			if tc.want == "preserved" {
				wantState = !strings.Contains(string(out), "STATE:")
			}
			if err == nil || !wantState {
				t.Fatalf("unexpected rollback outcome: %s (%v)", out, err)
			}
			if tc.failure == "missing" {
				if _, err := os.Stat(filepath.Join(fixture, "stopped")); !os.IsNotExist(err) {
					t.Fatalf("stopped before proving snapshot present: %s", out)
				}
			}
			if tc.failure == "missing" || tc.failure == "restore" {
				if _, err := os.Stat(filepath.Join(fixture, "restarted")); !os.IsNotExist(err) {
					t.Fatalf("restarted after incomplete rollback: %s", out)
				}
			}
			if strings.Contains(string(out), "CLEANUP") != (tc.failure == "none") {
				t.Fatalf("incorrect snapshot cleanup: %s", out)
			}
		})
	}
}

func TestAdministratorUpgradeUsesTrustedServiceUser(t *testing.T) {
	body := readPackagingInstallShell(t)
	functions := shellFunction(t, body, "resolve_installed_upgrade_account") + "\n" + shellFunction(t, body, "run_as_upgrade_account")
	script := functions + `
INSTALLATION_MODE=account
RUN_AS_USER=untrusted_config_value
agent_uses_launchd() { return 1; }
hfl_systemctl() { [[ "$*" == 'show hyperfilelens-agent.service -p User' ]] || return 9; echo User=installed_user; }
id() { [[ "$*" == '-u installed_user' ]] || return 9; echo 1234; }
runuser() { [[ "$*" == '-u installed_user -- maintenance --read-only' ]]; }
resolve_installed_upgrade_account
run_as_upgrade_account maintenance --read-only
`
	if out, err := exec.Command("bash", "-c", "set -eu\n"+script).CombinedOutput(); err != nil {
		t.Fatalf("maintenance switched to the wrong principal: %s %v", out, err)
	}
}
