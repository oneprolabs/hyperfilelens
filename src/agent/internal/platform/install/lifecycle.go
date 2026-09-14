package install

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

// RunUpgrade runs the installed upgrade script against a release archive (requires root/admin).
func RunUpgrade(ctx context.Context, archivePath string) error {
	script, args := upgradeCommand(archivePath)
	cmd := exec.CommandContext(ctx, script, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s upgrade: %w", script, err)
	}
	// The upgrade command may have been executed by the previous release's
	// installer. Run the newly deployed installer once so a first upgrade can
	// reconcile legacy paths without requiring a second user-triggered upgrade.
	if err := RunReconcileLegacyLayout(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "warning: legacy Agent reconciliation was deferred: %v\n", err)
	}
	return nil
}

// RunReconcileLegacyLayout asks the newly installed lifecycle script to finish
// a pending old-layout migration. It is intentionally best-effort: failure
// preserves the old paths and must not turn a healthy binary upgrade into a
// failed Agent upgrade.
func RunReconcileLegacyLayout(ctx context.Context) error {
	if runtime.GOOS == "windows" {
		return nil
	}
	script := filepath.Join(DefaultInstallDir(), "install.sh")
	cmd := exec.CommandContext(ctx, script, "reconcile-legacy", "--quiet-footer")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s reconcile-legacy: %w", script, err)
	}
	return nil
}

// RunUninstall invokes bundled uninstall (keepData=false).
func RunUninstall(ctx context.Context, bundleDir string) error {
	return RunUninstallWithDataPolicy(ctx, bundleDir, false)
}

// RunUninstallWithDataPolicy invokes bundled uninstall with explicit data retention.
func RunUninstallWithDataPolicy(ctx context.Context, bundleDir string, keepData bool) error {
	return runUninstall(ctx, bundleDir, keepData, false)
}

// RunRollbackIncompleteInstall removes an incomplete Agent install while
// preserving the data directory and installation identity for a retry.
func RunRollbackIncompleteInstall(ctx context.Context, bundleDir string) error {
	return runUninstall(ctx, bundleDir, true, true)
}

func runUninstall(ctx context.Context, bundleDir string, keepData, keepInstallationIdentity bool) error {
	script, args := uninstallCommand(bundleDir, keepData, keepInstallationIdentity)
	cmd := exec.CommandContext(ctx, script, args...)
	cmd.Dir = bundleDir
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s uninstall: %w", script, err)
	}
	return nil
}

func upgradeCommand(archivePath string) (string, []string) {
	installDir := vfs.LifecycleInstallDirForMode(model.InstallationModeSystem)
	if runtime.GOOS == "windows" {
		return filepath.Join(installDir, "install.ps1"), []string{"upgrade", "-From", archivePath}
	}
	// Remote upgrade has no TTY; --yes allows same-version reinstall (warn + continue).
	return filepath.Join(installDir, "install.sh"), []string{"upgrade", "--from", archivePath, "--yes"}
}

func uninstallCommand(bundleDir string, keepData, keepInstallationIdentity bool) (string, []string) {
	if runtime.GOOS == "windows" {
		args := []string{"uninstall", "-QuietFooter"}
		if !keepData {
			args = append(args, "-PurgeAll")
		} else if keepInstallationIdentity {
			args = append(args, "-KeepInstallationIdentity")
		} else {
			args = append(args, "-KeepData")
		}
		return filepath.Join(bundleDir, "install.ps1"), args
	}
	args := []string{"uninstall", "--quiet-footer"}
	if !keepData {
		args = append(args, "--purge-all")
	} else if keepInstallationIdentity {
		args = append(args, "--keep-installation-identity")
	} else {
		args = append(args, "--keep-data")
	}
	return filepath.Join(bundleDir, "install.sh"), args
}

// ExtractArchive unpacks tar.gz (Unix) or zip (Windows) into destDir.
func ExtractArchive(ctx context.Context, archivePath, destDir string) error {
	if err := os.MkdirAll(destDir, 0o755); err != nil {
		return err
	}
	switch {
	case strings.HasSuffix(strings.ToLower(archivePath), ".zip"):
		return extractZip(ctx, archivePath, destDir)
	default:
		return extractTarGz(ctx, archivePath, destDir)
	}
}

func extractTarGz(ctx context.Context, archivePath, destDir string) error {
	cmd := exec.CommandContext(ctx, "tar", "xzf", archivePath, "-C", destDir)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("tar: %w (%s)", err, strings.TrimSpace(string(out)))
	}
	return nil
}

func extractZip(ctx context.Context, archivePath, destDir string) error {
	script := fmt.Sprintf(
		"Expand-Archive -LiteralPath %q -DestinationPath %q -Force",
		archivePath,
		destDir,
	)
	cmd := exec.CommandContext(ctx, "powershell", "-NoProfile", "-Command", script)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("Expand-Archive: %w (%s)", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// FindBundleRoot returns the single top-level hfl-agent-* directory inside workDir.
func FindBundleRoot(workDir string) (string, error) {
	entries, err := os.ReadDir(workDir)
	if err != nil {
		return "", err
	}
	for _, e := range entries {
		if !e.IsDir() || !strings.HasPrefix(e.Name(), "hfl-agent-") {
			continue
		}
		root := filepath.Join(workDir, e.Name())
		if runtime.GOOS == "windows" {
			if _, err := os.Stat(filepath.Join(root, "install.ps1")); err == nil {
				return root, nil
			}
		} else if _, err := os.Stat(filepath.Join(root, "install.sh")); err == nil {
			return root, nil
		}
	}
	return "", fmt.Errorf("bundle root not found under %s", workDir)
}

// DefaultInstallDir returns the platform install root for bundled scripts.
func DefaultInstallDir() string {
	return vfs.DefaultInstallDir()
}
