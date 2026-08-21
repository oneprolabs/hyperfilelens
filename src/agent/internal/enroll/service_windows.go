//go:build windows

package enroll

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/platform/install"
)

// StartInstalledService registers and starts HyperFileLensAgent after enrollment.
func StartInstalledService(ctx context.Context) error {
	return startWindowsService(ctx)
}

// RestartInstalledService reloads the persisted Agent runtime environment.
func RestartInstalledService(ctx context.Context) error {
	return startWindowsService(ctx)
}

func startWindowsService(ctx context.Context) error {
	installRoot := install.DefaultInstallDir()
	agentBin := filepath.Join(installRoot, "hfl-agent.exe")
	installScript := filepath.Join(installRoot, "install.ps1")

	if _, err := os.Stat(agentBin); err != nil {
		return fmt.Errorf("agent binary missing at %s", agentBin)
	}
	if _, err := os.Stat(installScript); err != nil {
		return fmt.Errorf("agent installer missing at %s", installScript)
	}
	cmd := exec.CommandContext(
		ctx,
		"powershell.exe",
		"-NoProfile",
		"-ExecutionPolicy",
		"Bypass",
		"-File",
		installScript,
		"start",
		"-QuietFooter",
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("Windows Agent lifecycle start failed: %w (%s)", err, strings.TrimSpace(string(out)))
	}
	return nil
}
