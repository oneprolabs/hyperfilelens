//go:build linux

package enroll

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/platform/atomicfile"
	"hyperfilelens/agent/internal/platform/vfs"
)

const userSystemdUnitName = "hyperfilelens-agent.service"

func ensureUserSystemdUnit() error {
	if !installIsUserLevel() {
		return nil
	}
	installDir, err := vfs.UserInstallDir()
	if err != nil {
		return fmt.Errorf("resolve install directory: %w", err)
	}
	dataDir, err := vfs.UserDataDir()
	if err != nil {
		return fmt.Errorf("resolve data directory: %w", err)
	}
	unitPath, err := userSystemdUnitPath()
	if err != nil {
		return err
	}
	unit, err := renderUserSystemdUnit(installDir, dataDir)
	if err != nil {
		return err
	}
	if err := atomicfile.Write(unitPath, []byte(unit), 0o644); err != nil {
		return fmt.Errorf("write %s: %w", unitPath, err)
	}
	return nil
}

func userSystemdUnitPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		return "", fmt.Errorf("resolve current user Home directory")
	}
	configHome := strings.TrimSpace(os.Getenv("XDG_CONFIG_HOME"))
	if configHome == "" || !filepath.IsAbs(configHome) {
		configHome = filepath.Join(home, ".config")
	}
	configHome, err = absoluteSystemdPath(configHome)
	if err != nil {
		return "", fmt.Errorf("resolve current user config directory: %w", err)
	}
	return filepath.Join(configHome, "systemd", "user", userSystemdUnitName), nil
}

func renderUserSystemdUnit(installDir, dataDir string) (string, error) {
	installDir, err := absoluteSystemdPath(installDir)
	if err != nil {
		return "", fmt.Errorf("install directory: %w", err)
	}
	dataDir, err = absoluteSystemdPath(dataDir)
	if err != nil {
		return "", fmt.Errorf("data directory: %w", err)
	}
	agent := escapeSystemdUnitPath(filepath.Join(installDir, "hfl-agent"))
	return fmt.Sprintf(`[Unit]
Description=HyperFileLens Agent (Current User)
StartLimitIntervalSec=0

[Service]
Type=simple
EnvironmentFile=%s
WorkingDirectory=%s
ExecStart="%s" run
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=default.target
`, escapeSystemdUnitPath(filepath.Join(dataDir, "agent.env")), escapeSystemdUnitPath(installDir), agent), nil
}

func absoluteSystemdPath(path string) (string, error) {
	path = strings.TrimSpace(path)
	if path == "" || strings.ContainsAny(path, "\r\n\x00") {
		return "", fmt.Errorf("path is empty or contains unsupported characters")
	}
	absPath, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	return filepath.Clean(absPath), nil
}

func escapeSystemdUnitPath(path string) string {
	path = strings.ReplaceAll(path, `\`, `\\`)
	path = strings.ReplaceAll(path, `"`, `\"`)
	return strings.ReplaceAll(path, "%", "%%")
}
