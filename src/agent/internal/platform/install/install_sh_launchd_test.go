//go:build !windows

package install

import (
	"fmt"
	"os/exec"
	"strings"
	"testing"
)

func TestInstallShellWaitsForLaunchdUnload(t *testing.T) {
	body := readPackagingInstallShell(t)
	functions := shellFunction(t, body, "wait_for_launchd_unload") + "\n" +
		shellFunction(t, body, "stop_launchd_service")

	tests := []struct {
		name       string
		launchctl  string
		wantStatus int
		wantOutput string
	}{
		{
			name: "delayed unload succeeds",
			launchctl: `
launchctl() {
	case "$1" in
	print)
		PRINT_COUNT=$((PRINT_COUNT + 1))
		[[ "${PRINT_COUNT}" -lt 4 ]]
		;;
	bootout) return 0 ;;
	esac
}`,
			wantOutput: "OK:stopped launchd service com.hyperfilelens.agent",
		},
		{
			name: "primary bootout falls back to plist",
			launchctl: `
launchctl() {
	case "$1" in
	print)
		PRINT_COUNT=$((PRINT_COUNT + 1))
		[[ "${PRINT_COUNT}" -eq 1 ]]
		;;
	bootout)
		BOOTOUT_COUNT=$((BOOTOUT_COUNT + 1))
		[[ "$#" -eq 3 ]]
		;;
	esac
}`,
			wantOutput: "BOOTOUT_COUNT=2",
		},
		{
			name: "already unloaded is idempotent",
			launchctl: `
launchctl() {
	case "$1" in
	print) return 1 ;;
	bootout) return 99 ;;
	esac
}`,
			wantOutput: "SKIP:stop launchd com.hyperfilelens.agent (not loaded)",
		},
		{
			name: "both bootout forms fail closed",
			launchctl: `
launchctl() {
	case "$1" in
	print) return 0 ;;
	bootout)
		BOOTOUT_COUNT=$((BOOTOUT_COUNT + 1))
		return 1
		;;
	esac
}`,
			wantStatus: 2,
			wantOutput: "could not be stopped; upgrade was aborted before deployment",
		},
		{
			name: "unload timeout fails closed",
			launchctl: `
launchctl() {
	case "$1" in
	print) return 0 ;;
	bootout) return 0 ;;
	esac
}`,
			wantStatus: 2,
			wantOutput: "did not unload within 1 seconds; upgrade was aborted before deployment",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			script := fmt.Sprintf(`
set -euo pipefail
LAUNCHD_DOMAIN=gui/501
LAUNCHD_LABEL=com.hyperfilelens.agent
LAUNCHD_PLIST=/tmp/com.hyperfilelens.agent.plist
UPGRADE_TRANSACTION_ACTIVE=1
HFL_LAUNCHD_UNLOAD_TIMEOUT_SECONDS=1
PRINT_COUNT=0
BOOTOUT_COUNT=0
log_ok() { printf 'OK:%%s\n' "$1"; }
log_skip() { printf 'SKIP:%%s\n' "$1"; }
log_warn() { printf 'WARN:%%s\n' "$1"; }
log_fail() { printf 'FAIL:%%s\n' "$1" >&2; exit "${2:-1}"; }
sleep() { command sleep 0.01; }
%s
%s
stop_launchd_service
printf 'PRINT_COUNT=%%s\nBOOTOUT_COUNT=%%s\n' "${PRINT_COUNT}" "${BOOTOUT_COUNT}"
`, functions, tt.launchctl)
			cmd := exec.Command("bash", "-c", script)
			output, err := cmd.CombinedOutput()
			status := 0
			if err != nil {
				var ok bool
				status, ok = shellExitStatus(err)
				if !ok {
					t.Fatalf("run launchd harness: %v\n%s", err, output)
				}
			}
			if status != tt.wantStatus {
				t.Fatalf("exit status = %d, want %d\n%s", status, tt.wantStatus, output)
			}
			if !strings.Contains(string(output), tt.wantOutput) {
				t.Fatalf("output missing %q:\n%s", tt.wantOutput, output)
			}
		})
	}
}

func shellFunction(t *testing.T, body, name string) string {
	t.Helper()
	start := strings.Index(body, name+"() {")
	if start < 0 {
		t.Fatalf("install.sh missing %s", name)
	}
	end := strings.Index(body[start:], "\n}\n")
	if end < 0 {
		t.Fatalf("install.sh function %s has no end", name)
	}
	return body[start : start+end+2]
}

func shellExitStatus(err error) (int, bool) {
	exitError, ok := err.(*exec.ExitError)
	if !ok {
		return 0, false
	}
	return exitError.ExitCode(), true
}
