//go:build !windows

package enroll

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"hyperfilelens/agent/internal/platform/install"
)

// verifyRunningAgentMatchesInstall confirms that a Linux systemd service is
// executing the binary that was just installed. A routable WebSocket alone is
// insufficient because an older process can remain online after an in-place
// binary replacement.
func verifyRunningAgentMatchesInstall(ctx context.Context) error {
	if runtime.GOOS != "linux" {
		return nil
	}
	if _, err := exec.LookPath("systemctl"); err != nil {
		return nil
	}
	bin := filepath.Join(install.DefaultInstallDir(), agentBinaryName())
	if _, err := os.Stat(bin); err != nil {
		if legacy := legacyAgentBinaryPath(); legacy != "" {
			if _, legacyErr := os.Stat(legacy); legacyErr == nil {
				bin = legacy
			}
		}
	}
	if _, err := os.Stat(bin); err != nil {
		return fmt.Errorf("installed Agent binary is missing at %s", bin)
	}
	want, err := filepath.EvalSymlinks(bin)
	if err != nil {
		want = filepath.Clean(bin)
	}
	deadline := time.Now().Add(15 * time.Second)
	var lastErr error
	for {
		pid, err := systemdAgentMainPID(ctx)
		if err != nil {
			lastErr = err
		} else if pid <= 0 {
			lastErr = fmt.Errorf("Agent service has no MainPID")
		} else {
			rawExe, readErr := os.Readlink(fmt.Sprintf("/proc/%d/exe", pid))
			if readErr != nil {
				lastErr = fmt.Errorf("inspect running Agent process %d: %w", pid, readErr)
			} else if runningExecutableMatches(rawExe, want) {
				return nil
			} else {
				lastErr = fmt.Errorf("running Agent process %d does not execute the installed binary (%s)", pid, rawExe)
			}
		}
		if time.Now().After(deadline) {
			if lastErr == nil {
				lastErr = fmt.Errorf("running Agent process could not be verified")
			}
			return lastErr
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
}

// runningExecutableMatches rejects the kernel's "(deleted)" marker. It means
// the process still has an old unlinked inode open after an atomic replacement.
func runningExecutableMatches(rawExe, installedExe string) bool {
	if strings.HasSuffix(rawExe, " (deleted)") {
		return false
	}
	running, err := filepath.EvalSymlinks(rawExe)
	if err != nil {
		running = filepath.Clean(rawExe)
	}
	return filepath.Clean(running) == filepath.Clean(installedExe)
}

func systemdAgentMainPID(ctx context.Context) (int, error) {
	args := []string{"show", "hyperfilelens-agent.service", "-p", "MainPID"}
	if installIsUserLevel() {
		args = append([]string{"--user"}, args...)
	}
	out, err := exec.CommandContext(ctx, "systemctl", args...).CombinedOutput()
	if err != nil {
		return 0, fmt.Errorf("systemctl show MainPID: %w (%s)", err, strings.TrimSpace(string(out)))
	}
	for _, line := range strings.Split(string(out), "\n") {
		if strings.HasPrefix(line, "MainPID=") {
			raw := strings.TrimSpace(strings.TrimPrefix(line, "MainPID="))
			if raw == "" || raw == "0" {
				return 0, nil
			}
			var pid int
			if _, err := fmt.Sscanf(raw, "%d", &pid); err != nil {
				return 0, fmt.Errorf("parse MainPID %q: %w", raw, err)
			}
			return pid, nil
		}
	}
	return 0, fmt.Errorf("systemctl show MainPID returned no MainPID")
}

func enrollmentServiceAction(serviceMayHaveProcess, pendingUpgrade bool) string {
	if pendingUpgrade {
		return "start"
	}
	if serviceMayHaveProcess {
		return "restart"
	}
	return "start"
}
