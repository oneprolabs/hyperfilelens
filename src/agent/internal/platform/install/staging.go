package install

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

const (
	pendingUpgradeRunnerName   = "run-upgrade.sh"
	pendingUpgradeRunnerPS1    = "run-upgrade.ps1"
	pendingUninstallRunnerName = "run-uninstall.sh"
	// PendingUpgradeFailedMarker is written when detached upgrade fails.
	PendingUpgradeFailedMarker = "FAILED"
)

// StageUpgradeArchive copies archivePath into dataDir/lifecycle/upgrade for detached upgrade.
func StageUpgradeArchive(dataDir, archivePath string) (string, error) {
	dataDir = strings.TrimSpace(dataDir)
	if dataDir == "" {
		return "", fmt.Errorf("data dir required to stage upgrade archive")
	}
	archivePath = strings.TrimSpace(archivePath)
	if archivePath == "" {
		return "", fmt.Errorf("archive path required")
	}
	pendingDir := LifecycleUpgradeDir(dataDir)
	if err := os.RemoveAll(pendingDir); err != nil {
		return "", fmt.Errorf("clear staged upgrade directory: %w", err)
	}
	if err := os.MkdirAll(pendingDir, 0o750); err != nil {
		return "", err
	}
	dest := StagedUpgradePackagePath(dataDir, archivePath)
	if err := copyFile(archivePath, dest); err != nil {
		return "", err
	}
	return dest, nil
}

// StageUpgradeInstaller copies the validated bundle installer alongside the
// archive so the detached runner can bootstrap the upgrade with new logic.
func StageUpgradeInstaller(dataDir, bundleRoot string) (string, error) {
	dataDir = strings.TrimSpace(dataDir)
	bundleRoot = strings.TrimSpace(bundleRoot)
	if dataDir == "" || bundleRoot == "" {
		return "", fmt.Errorf("data dir and bundle root required to stage upgrade installer")
	}
	name := "install.sh"
	if runtime.GOOS == "windows" {
		name = "install.ps1"
	}
	src := filepath.Join(bundleRoot, name)
	if runtime.GOOS == "windows" {
		src = filepath.Join(bundleRoot, "install.ps1")
	}
	if _, err := os.Stat(src); err != nil {
		return "", fmt.Errorf("validated upgrade installer missing: %w", err)
	}
	dest := filepath.Join(LifecycleUpgradeDir(dataDir), "installer", name)
	if err := os.MkdirAll(filepath.Dir(dest), 0o750); err != nil {
		return "", err
	}
	if err := copyFile(src, dest); err != nil {
		return "", fmt.Errorf("stage upgrade installer: %w", err)
	}
	if err := os.Chmod(dest, 0o700); err != nil {
		return "", err
	}
	return dest, nil
}

// stagedPackageFilename preserves compound extensions such as .tar.gz for install.sh.
func stagedPackageFilename(archivePath string) string {
	base := strings.ToLower(filepath.Base(archivePath))
	switch {
	case strings.HasSuffix(base, ".tar.gz"):
		return "package.tar.gz"
	case strings.HasSuffix(base, ".zip"):
		return "package.zip"
	default:
		ext := filepath.Ext(archivePath)
		if ext == "" {
			return "package"
		}
		return "package" + ext
	}
}

func copyFile(src, dest string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dest)
	if err != nil {
		return err
	}
	defer func() {
		_ = out.Close()
	}()
	if _, err := io.Copy(out, in); err != nil {
		_ = os.Remove(dest)
		return err
	}
	return out.Close()
}
