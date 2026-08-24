package config

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

// installationModeForExecutable binds standard installations to their
// installer-selected privilege boundary. Non-standard paths (including tests
// and development builds) retain the explicitly configured mode.
func installationModeForExecutable(configured model.InstallationMode) model.InstallationMode {
	if configured == "" {
		configured = model.InstallationModeSystem
	}
	// Account-scoped continuous installs intentionally use the machine-wide
	// binary path while retaining their selected ordinary-user identity.
	if configured == model.InstallationModeAccount {
		return configured
	}
	executable, err := os.Executable()
	if err != nil {
		return configured
	}
	executable = canonicalExistingPath(executable)
	if userRoot, userErr := vfs.UserInstallDir(); userErr == nil && pathWithinRoot(executable, canonicalExistingPath(userRoot)) {
		if configured == model.InstallationModeUserContinuous {
			return configured
		}
		return model.InstallationModeUser
	}
	if pathWithinRoot(executable, canonicalExistingPath(vfs.SystemInstallDir())) {
		return model.InstallationModeSystem
	}
	return configured
}

func canonicalExistingPath(path string) string {
	path = filepath.Clean(path)
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		return filepath.Clean(resolved)
	}
	return path
}

func pathWithinRoot(path, root string) bool {
	if runtime.GOOS == "windows" {
		path = strings.ToLower(path)
		root = strings.ToLower(root)
	}
	relative, err := filepath.Rel(root, path)
	return err == nil && relative != ".." && !filepath.IsAbs(relative) &&
		!strings.HasPrefix(relative, ".."+string(filepath.Separator))
}
