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
	"hyperfilelens/agent/internal/platform/vfs"
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
	pendingInstallScript := installScript + ".pending"

	if _, err := os.Stat(agentBin); err != nil {
		return fmt.Errorf("agent binary missing at %s", agentBin)
	}
	if _, err := os.Stat(installScript); err != nil {
		return fmt.Errorf("agent installer missing at %s", installScript)
	}
	// PowerShell may defer replacing its own script until the upgrade process
	// exits. During enrollment's immediate post-upgrade start, use that verified
	// target script when a durable transaction is awaiting health confirmation;
	// otherwise the previous release could start the service without committing
	// or rolling back the staged upgrade.
	if _, err := os.Stat(install.LifecycleUpgradeStatePath(vfs.DefaultAgentDataDir())); err == nil {
		if _, pendingErr := os.Stat(pendingInstallScript); pendingErr == nil {
			installScript = pendingInstallScript
		} else if !os.IsNotExist(pendingErr) {
			return fmt.Errorf("inspect pending Windows Agent installer: %w", pendingErr)
		}
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect pending Windows Agent upgrade: %w", err)
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
