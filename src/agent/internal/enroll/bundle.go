package enroll

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/vfs"
)

// RunBundleUpgrade upgrades an installed agent from a downloaded release archive.
// It is kept as a compatibility wrapper for callers that only have the
// archive path. Enrollment upgrades should prefer RunBundleUpgradeFromBundle
// so the newly verified installer is used even when the installed installer is
// from an older layout release.
func RunBundleUpgrade(ctx context.Context, archivePath string) error {
	return runBundleUpgrade(ctx, archivePath, "")
}

// RunBundleUpgradeFromBundle runs the installer shipped in the already
// extracted and validated release bundle. This avoids bootstrapping an
// upgrade through the previous installed install.sh, which may fail before it
// can deploy the new installer (notably during the unified Agent Root
// migration).
func RunBundleUpgradeFromBundle(ctx context.Context, archivePath, bundleRoot string, cfg Config) error {
	return runBundleUpgrade(ctx, archivePath, bundleRoot, cfg)
}

// RunBundleUpgradeFromArchive is the safe entry point for standalone upgrade
// commands. It validates the archive before selecting its installer, so an
// older installed installer cannot block the first upgrade to the current
// package layout.
func RunBundleUpgradeFromArchive(ctx context.Context, archivePath string, cfg Config) error {
	workDir, err := os.MkdirTemp("", "hfl-upgrade-")
	if err != nil {
		return fmt.Errorf("upgrade temp dir: %w", err)
	}
	defer os.RemoveAll(workDir)
	extractDir := filepath.Join(workDir, "extract")
	if err := install.ExtractArchive(ctx, archivePath, extractDir); err != nil {
		return fmt.Errorf("upgrade archive extract: %w", err)
	}
	bundleRoot, err := install.FindBundleRoot(extractDir)
	if err != nil {
		return fmt.Errorf("upgrade archive validation: %w", err)
	}
	if err := validateAgentPackage(bundleRoot, cfg.NodeRole, ""); err != nil {
		return fmt.Errorf("upgrade package validation: %w", err)
	}
	return RunBundleUpgradeFromBundle(ctx, archivePath, bundleRoot, cfg)
}

func runBundleUpgrade(ctx context.Context, archivePath, bundleRoot string, cfg ...Config) error {
	installDir := install.DefaultInstallDir()
	commandEnv := os.Environ()
	if len(cfg) > 0 {
		commandEnv = append(commandEnv,
			"HFL_INSTALLATION_MODE="+string(cfg[0].InstallationMode),
			"HFL_RUN_AS_USER="+cfg[0].RunAsUser,
			"HFL_RUN_AS_HOME="+cfg[0].RunAsHome,
		)
	}
	if runtime.GOOS == "windows" {
		script := filepath.Join(installDir, "install.ps1")
		if bundleRoot != "" {
			candidate := filepath.Join(bundleRoot, "install.ps1")
			if _, err := os.Stat(candidate); err == nil {
				script = candidate
			}
		}
		args := []string{
			"-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
			"upgrade", "-From", archivePath, "-NoRestart", "-QuietFooter",
		}
		cmd := exec.CommandContext(ctx, "powershell.exe", args...)
		if bundleRoot != "" {
			cmd.Dir = bundleRoot
		}
		cmd.Env = commandEnv
		return runStreamingCommand(cmd, "Agent upgrade")
	}
	script := filepath.Join(installDir, "install.sh")
	if bundleRoot != "" {
		candidate := filepath.Join(bundleRoot, "install.sh")
		if _, err := os.Stat(candidate); err == nil {
			script = candidate
		}
	}
	cmd := exec.CommandContext(
		ctx,
		script,
		"upgrade",
		"--from",
		archivePath,
		"--yes",
		"--no-restart",
		"--quiet-footer",
	)
	if bundleRoot != "" {
		cmd.Dir = bundleRoot
	}
	cmd.Env = commandEnv
	return runStreamingCommand(cmd, "Agent upgrade")
}

// RunBundleInstall invokes the distribution install script with --no-start.
func RunBundleInstall(ctx context.Context, bundleRoot string, cfg Config) error {
	if runtime.GOOS == "windows" {
		return runBundleInstallWindows(ctx, bundleRoot, cfg)
	}
	return runBundleInstallUnix(ctx, bundleRoot, cfg)
}

func runBundleInstallUnix(ctx context.Context, bundleRoot string, cfg Config) error {
	script := filepath.Join(bundleRoot, "install.sh")
	if _, err := os.Stat(script); err != nil {
		return fmt.Errorf("missing bundle install.sh: %w", err)
	}
	args := []string{
		"install",
		"--wss-url", cfg.WSSURL,
		"--api-base", cfg.APIBase,
		"--org-key", cfg.OrgKey,
		"--node-token", cfg.NodeToken,
		"--role", string(cfg.NodeRole),
		"--no-start",
		"--quiet-footer",
	}
	cmd := exec.CommandContext(ctx, script, args...)
	cmd.Dir = bundleRoot
	cmd.Env = append(
		os.Environ(),
		"HFL_INSTALLATION_MODE="+string(cfg.InstallationMode),
		"HFL_RUN_AS_USER="+cfg.RunAsUser,
		"HFL_RUN_AS_HOME="+cfg.RunAsHome,
	)
	return runStreamingCommand(cmd, "Agent installation")
}

func runBundleInstallWindows(ctx context.Context, bundleRoot string, cfg Config) error {
	script := filepath.Join(bundleRoot, "install.ps1")
	if _, err := os.Stat(script); err != nil {
		return fmt.Errorf("missing bundle install.ps1: %w", err)
	}
	args := []string{
		"-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
		"-WssUrl", cfg.WSSURL,
		"-ApiBase", cfg.APIBase,
		"-OrgKey", cfg.OrgKey,
		"-NodeToken", cfg.NodeToken,
		"-Role", string(cfg.NodeRole),
		"-NoService",
		"-QuietFooter",
	}
	if cfg.RunAsUser != "" {
		args = append(args, "-RunAsUser", cfg.RunAsUser)
	}
	cmd := exec.CommandContext(ctx, "powershell.exe", args...)
	cmd.Dir = bundleRoot
	cmd.Env = append(
		os.Environ(),
		"HFL_INSTALLATION_MODE="+string(cfg.InstallationMode),
		"HFL_RUN_AS_USER="+cfg.RunAsUser,
		"HFL_RUN_AS_HOME="+cfg.RunAsHome,
	)
	return runStreamingCommand(cmd, "Agent installation")
}

func runStreamingCommand(cmd *exec.Cmd, action string) error {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = io.MultiWriter(os.Stdout, &stdout)
	cmd.Stderr = io.MultiWriter(os.Stderr, &stderr)
	if err := cmd.Run(); err != nil {
		captured := append(append([]byte{}, stdout.Bytes()...), stderr.Bytes()...)
		return commandError(action, err, captured)
	}
	return nil
}

func commandError(action string, err error, out []byte) error {
	detail := extractLogDetail(string(out))
	logPath := agentInstallLogPath()
	if detail != "" {
		return fmt.Errorf("%s failed: %s See %s for details.", action, detail, logPath)
	}
	if text := strings.TrimSpace(string(out)); text != "" {
		return fmt.Errorf("%s failed. See %s for details.", action, logPath)
	}
	return fmt.Errorf("%s failed: %w", action, err)
}

func extractLogDetail(out string) string {
	lines := strings.Split(out, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		if idx := strings.Index(line, "] "); idx >= 0 && strings.HasPrefix(line, "[") {
			parts := strings.SplitN(line[idx+2:], "] ", 2)
			if len(parts) == 2 {
				return strings.TrimSpace(parts[1])
			}
		}
		if strings.Contains(line, "[FAIL") {
			return line
		}
		return line
	}
	return ""
}

func agentInstallLogPath() string {
	dataDir := dataDirForAgent()
	if dataDir == "" {
		return "the agent install log on this host"
	}
	return filepath.Join(vfs.AgentLogDir(dataDir), "install.log")
}

// InstalledAgentVersion runs the installed hfl-agent -version.
func InstalledAgentVersion(ctx context.Context) (string, error) {
	bin := filepath.Join(install.DefaultInstallDir(), agentBinaryName())
	cmd := exec.CommandContext(ctx, bin, "-version")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("%s -version: %w (%s)", bin, err, strings.TrimSpace(string(out)))
	}
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, "hyperfilelens-agent") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				return fields[1], nil
			}
		}
	}
	return "", fmt.Errorf("could not parse agent version from %q", strings.TrimSpace(string(out)))
}

func agentBinaryName() string {
	if runtime.GOOS == "windows" {
		return "hfl-agent.exe"
	}
	return "hfl-agent"
}
