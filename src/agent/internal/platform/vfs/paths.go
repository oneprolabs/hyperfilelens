package vfs

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/model"
)

const installationModeEnv = "HFL_INSTALLATION_MODE"

// UnixProductSlug is the FHS directory basename for Agent on Linux/macOS
// (/opt/hyperfilelens-agent, /var/lib/hyperfilelens-agent, hyperfilelens-agent.service).
const UnixProductSlug = "hyperfilelens-agent"

// WindowsVendorDir is the vendor folder under Program Files / ProgramData.
const WindowsVendorDir = "HyperFileLens"

// WindowsProductDir is the product folder under WindowsVendorDir (PascalCase per Windows convention).
const WindowsProductDir = "Agent"

// UnixInstallDir returns the FHS /opt install root (hfl-agent + bundled kopia).
func UnixInstallDir() string {
	return filepath.Join("/opt", UnixProductSlug)
}

// UnixDataDir returns the FHS /var/lib state root (agent.env, agent.db, logs, cache).
func UnixDataDir() string {
	return filepath.Join("/var/lib", UnixProductSlug)
}

// SystemInstallDir returns the machine-wide program directory.
func SystemInstallDir() string {
	switch runtime.GOOS {
	case "windows":
		pf := os.Getenv("ProgramFiles")
		if pf == "" {
			pf = `C:\Program Files`
		}
		return filepath.Join(pf, WindowsVendorDir, WindowsProductDir)
	default:
		return UnixInstallDir()
	}
}

// SystemDataDir returns the machine-wide Agent state directory.
func SystemDataDir() string {
	if runtime.GOOS == "windows" {
		programData := os.Getenv("ProgramData")
		if programData == "" {
			programData = `C:\ProgramData`
		}
		return filepath.Join(programData, WindowsVendorDir, WindowsProductDir)
	}
	return UnixDataDir()
}

// UserInstallDir returns the per-user program directory for user-level Agent installs.
func UserInstallDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return "", errors.New("current user home directory is unavailable")
	}
	return UserInstallDirForHome(home), nil
}

// UserInstallDirForHome returns the per-user program directory under home.
func UserInstallDirForHome(home string) string {
	switch runtime.GOOS {
	case "windows":
		localAppData := os.Getenv("LOCALAPPDATA")
		if localAppData == "" {
			localAppData = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(localAppData, "Programs", WindowsVendorDir, WindowsProductDir)
	case "darwin":
		return filepath.Join(
			home,
			"Library",
			"Application Support",
			WindowsVendorDir,
			WindowsProductDir,
			"bin",
		)
	default:
		return filepath.Join(home, ".local", "lib", UnixProductSlug)
	}
}

// UserDataDir returns the private state directory for user-level Agent installs.
func UserDataDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return "", errors.New("current user home directory is unavailable")
	}
	switch runtime.GOOS {
	case "windows":
		localAppData := os.Getenv("LOCALAPPDATA")
		if localAppData == "" || !pathWithinHome(localAppData, home) {
			localAppData = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(localAppData, WindowsVendorDir, "AgentData"), nil
	case "darwin":
		return filepath.Join(
			home,
			"Library",
			"Application Support",
			WindowsVendorDir,
			WindowsProductDir,
		), nil
	default:
		stateHome := os.Getenv("XDG_STATE_HOME")
		if stateHome == "" || !filepath.IsAbs(stateHome) {
			stateHome = filepath.Join(home, ".local", "state")
		}
		return filepath.Join(stateHome, UnixProductSlug), nil
	}
}

func pathWithinHome(path, home string) bool {
	path = filepath.Clean(path)
	home = filepath.Clean(home)
	relative, err := filepath.Rel(home, path)
	return err == nil && relative != ".." && !filepath.IsAbs(relative) &&
		!strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

// InstallDirForMode returns the program directory for a persisted Agent mode.
func InstallDirForMode(mode model.InstallationMode) string {
	if mode == model.InstallationModeUser {
		path, err := UserInstallDir()
		if err == nil {
			return path
		}
	}
	return SystemInstallDir()
}

// AgentDataDirForMode returns the state directory for a persisted Agent mode.
func AgentDataDirForMode(mode model.InstallationMode) string {
	if mode == model.InstallationModeUser {
		path, err := UserDataDir()
		if err == nil {
			return path
		}
	}
	return SystemDataDir()
}

// UserInstallation reports whether the process is configured for user-level lifecycle.
func UserInstallation() bool {
	return os.Getenv(installationModeEnv) == "user"
}

// DefaultInstallDir returns the platform default binary install directory.
func DefaultInstallDir() string {
	mode := model.InstallationModeSystem
	if UserInstallation() {
		mode = model.InstallationModeUser
	}
	return InstallDirForMode(mode)
}
