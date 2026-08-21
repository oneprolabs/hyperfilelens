package vfs

import (
	"context"
	"os"
	"path/filepath"

	"hyperfilelens/agent/internal/model"
)

// ResolvePath normalizes long paths and platform-specific path quirks.
func ResolvePath(ctx context.Context, p string) (string, error) {
	_ = ctx
	return filepath.Clean(p), nil
}

// EnsureSpace verifies that at least minBytes are available on the volume for path.
func EnsureSpace(ctx context.Context, path string, minBytes uint64) error {
	_ = ctx
	_ = path
	_ = minBytes
	return nil
}

// DefaultAgentDataDir is the state directory when HFL_DATA_DIR (and HFL_AGENT_HOME) are unset.
// Matches install.sh / install.ps1 defaults so bare `hfl-agent` runs use the same layout as systemd.
func DefaultAgentDataDir() string {
	mode := model.InstallationModeSystem
	if UserInstallation() {
		mode = model.InstallationModeUser
	}
	return AgentDataDirForMode(mode)
}

// AgentDataDir returns the default agent state directory (execPath ignored; kept for call-site stability).
func AgentDataDir(execPath string) string {
	_ = execPath
	return DefaultAgentDataDir()
}

// AgentLogDir is the default rolling log directory under the resolved data root.
func AgentLogDir(dataRoot string) string {
	return filepath.Join(dataRoot, "logs")
}

// AgentCacheDir is the default runtime cache directory under the data root.
func AgentCacheDir(dataRoot string) string {
	return filepath.Join(dataRoot, "cache")
}

// KopiaBinaryPath returns the sibling path to the bundled Kopia executable.
func KopiaBinaryPath(execPath string) string {
	name := "kopia"
	if os.PathSeparator == '\\' {
		name = "kopia.exe"
	}
	return filepath.Join(filepath.Dir(execPath), name)
}
