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

// UnixProductSlug is the product directory basename used by Unix installs.
const UnixProductSlug = "hyperfilelens-agent"

// WindowsVendorDir is the vendor folder under the Windows Agent Root.
const WindowsVendorDir = "HyperFileLens"

// WindowsProductDir is the product folder under WindowsVendorDir.
const WindowsProductDir = "Agent"

// SystemAgentRoot returns the machine-wide Agent Root for new installations.
// All product directories (bin, config, data, logs, cache, mounts, runtime,
// lifecycle, and backup) are siblings directly below this root.
func SystemAgentRoot() string {
	switch runtime.GOOS {
	case "windows":
		programData := os.Getenv("ProgramData")
		if programData == "" {
			programData = `C:\ProgramData`
		}
		return filepath.Join(programData, WindowsVendorDir, WindowsProductDir)
	case "darwin":
		return filepath.Join("/Library", "Application Support", WindowsVendorDir, WindowsProductDir)
	default:
		return filepath.Join("/opt", UnixProductSlug)
	}
}

// LegacySystemInstallDir returns the original machine-wide program directory.
// It is used only while migrating a genuine pre-mode installation.
func LegacySystemInstallDir() string {
	switch runtime.GOOS {
	case "windows":
		programFiles := os.Getenv("ProgramFiles")
		if programFiles == "" {
			programFiles = `C:\Program Files`
		}
		return filepath.Join(programFiles, WindowsVendorDir, WindowsProductDir)
	default:
		return filepath.Join("/opt", UnixProductSlug)
	}
}

// LegacySystemDataDir returns the original machine-wide state directory.
// It is used only while migrating a genuine pre-mode installation.
func LegacySystemDataDir() string {
	if runtime.GOOS == "windows" {
		programData := os.Getenv("ProgramData")
		if programData == "" {
			programData = `C:\ProgramData`
		}
		return filepath.Join(programData, WindowsVendorDir, WindowsProductDir)
	}
	return filepath.Join("/var/lib", UnixProductSlug)
}

// UnixInstallDir is retained for callers that explicitly refer to the legacy
// machine program location. New code should use SystemAgentRoot or
// InstallDirForMode.
func UnixInstallDir() string { return filepath.Join("/opt", UnixProductSlug) }

// UnixDataDir is retained for callers that explicitly refer to the legacy
// machine state location. New code should use SystemDataDir.
func UnixDataDir() string { return filepath.Join("/var/lib", UnixProductSlug) }

// UserAgentRootForHome returns the per-user Agent Root for a home directory.
func UserAgentRootForHome(home string) string {
	home = filepath.Clean(strings.TrimSpace(home))
	switch runtime.GOOS {
	case "windows":
		localAppData := os.Getenv("LOCALAPPDATA")
		if localAppData == "" || !pathWithinHome(localAppData, home) {
			localAppData = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(localAppData, WindowsVendorDir, WindowsProductDir)
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", WindowsVendorDir, WindowsProductDir)
	default:
		dataHome := strings.TrimSpace(os.Getenv("XDG_DATA_HOME"))
		if dataHome == "" || !filepath.IsAbs(dataHome) {
			dataHome = filepath.Join(home, ".local", "share")
		}
		return filepath.Join(dataHome, UnixProductSlug)
	}
}

// UserInstallDir returns the per-user program directory for a user-level Agent.
func UserInstallDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		return "", errors.New("current user home directory is unavailable")
	}
	return UserInstallDirForHome(home), nil
}

// UserInstallDirForHome returns the per-user program directory under home.
func UserInstallDirForHome(home string) string {
	return filepath.Join(UserAgentRootForHome(home), "bin")
}

// LegacyUserInstallDirForHome returns the pre-unified per-user program path.
// It is used only to prevent two lifecycle modes during migration.
func LegacyUserInstallDirForHome(home string) string {
	home = filepath.Clean(strings.TrimSpace(home))
	switch runtime.GOOS {
	case "windows":
		localAppData := os.Getenv("LOCALAPPDATA")
		if localAppData == "" || !pathWithinHome(localAppData, home) {
			localAppData = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(localAppData, "Programs", WindowsVendorDir, WindowsProductDir)
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", WindowsVendorDir, WindowsProductDir, "bin")
	default:
		return filepath.Join(home, ".local", "lib", UnixProductSlug)
	}
}

// UserDataDir returns the per-user Agent Root. HFL_DATA_DIR is retained as the
// historical name for this canonical product root; the database and config
// files are placed in its data/ and config/ children by their owning packages.
func UserDataDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		return "", errors.New("current user home directory is unavailable")
	}
	return UserAgentRootForHome(home), nil
}

func pathWithinHome(path, home string) bool {
	path = filepath.Clean(path)
	home = filepath.Clean(home)
	relative, err := filepath.Rel(home, path)
	return err == nil && relative != ".." && !filepath.IsAbs(relative) &&
		!strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

// InstallDirForMode returns the new program directory for a persisted Agent mode.
func InstallDirForMode(mode model.InstallationMode) string {
	if mode == model.InstallationModeUser {
		if path, err := UserInstallDir(); err == nil {
			return path
		}
	}
	return filepath.Join(SystemAgentRoot(), "bin")
}

// AgentDataDirForMode returns the canonical Agent Root for a persisted mode.
// The historical DataDir name is kept for CLI and environment compatibility.
func AgentDataDirForMode(mode model.InstallationMode) string {
	if mode == model.InstallationModeUser {
		if path, err := UserDataDir(); err == nil {
			return path
		}
	}
	return SystemAgentRoot()
}

// AgentRootForMode returns the unified root for a persisted Agent mode.
func AgentRootForMode(mode model.InstallationMode) string {
	if mode == model.InstallationModeUser {
		if home, err := os.UserHomeDir(); err == nil && strings.TrimSpace(home) != "" {
			return UserAgentRootForHome(home)
		}
	}
	return SystemAgentRoot()
}

// SystemInstallDir and SystemDataDir remain named compatibility helpers. The
// latter returns the canonical Agent Root despite its historical name.
func SystemInstallDir() string { return filepath.Join(SystemAgentRoot(), "bin") }

func SystemDataDir() string { return SystemAgentRoot() }

// UserInstallation reports whether the process is configured for user-level lifecycle.
func UserInstallation() bool {
	mode, err := model.ParseInstallationMode(strings.TrimSpace(os.Getenv(installationModeEnv)))
	return err == nil && mode == model.InstallationModeUser
}

// DefaultInstallDir returns the platform default program directory.
func DefaultInstallDir() string {
	mode := model.InstallationModeSystem
	configured, err := model.ParseInstallationMode(strings.TrimSpace(os.Getenv(installationModeEnv)))
	if err == nil && configured == model.InstallationModeUser {
		mode = model.InstallationModeUser
	}
	return InstallDirForMode(mode)
}
