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

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/install"
)

// StartInstalledService enables and starts the platform service after enrollment.
func StartInstalledService(ctx context.Context) error {
	if runtime.GOOS == "darwin" {
		return startUnixScript(ctx, "start")
	}
	return startSystemd(ctx)
}

// RestartInstalledService reloads the persisted Agent runtime environment.
func RestartInstalledService(ctx context.Context) error {
	return startUnixScript(ctx, "restart")
}

func startUnixScript(ctx context.Context, command string) error {
	script := filepath.Join(install.DefaultInstallDir(), "install.sh")
	cmd := exec.CommandContext(ctx, script, command)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return commandError("start service", err, out)
	}
	return nil
}

func startSystemd(ctx context.Context) error {
	if _, err := exec.LookPath("systemctl"); err != nil {
		return fmt.Errorf("systemctl not found")
	}
	// Rebind and repair can start from an older inactive installation without
	// running the bundle installer again. Recreate the user unit here so a stale
	// or previously invalid definition cannot survive into the start attempt.
	if err := ensureUserSystemdUnit(); err != nil {
		return fmt.Errorf("repair current-user systemd unit: %w", err)
	}
	for _, args := range [][]string{
		{"daemon-reload"},
		{"enable", "hyperfilelens-agent.service"},
		{"start", "hyperfilelens-agent.service"},
	} {
		if installIsUserLevel() {
			args = append([]string{"--user"}, args...)
		}
		cmd := exec.CommandContext(ctx, "systemctl", args...)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("systemctl %s: %w (%s)", strings.Join(args, " "), err, strings.TrimSpace(string(out)))
		}
	}
	activeArgs := []string{"is-active", "hyperfilelens-agent.service"}
	if installIsUserLevel() {
		activeArgs = append([]string{"--user"}, activeArgs...)
	}
	active, _ := exec.CommandContext(ctx, "systemctl", activeArgs...).Output()
	if strings.TrimSpace(string(active)) != "active" {
		return fmt.Errorf("service not active after start")
	}
	return nil
}

func installIsUserLevel() bool {
	// Current-user and Linux user-continuous modes use a per-user service
	// manager. Account-scoped continuous mode uses a system unit with
	// User=selected.
	mode := strings.TrimSpace(os.Getenv("HFL_INSTALLATION_MODE"))
	return mode == string(model.InstallationModeUser) || mode == string(model.InstallationModeUserContinuous)
}
