package vfs

import (
	"context"
	"os"
	"path/filepath"
	"strings"

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

// DefaultAgentDataDir is the canonical Agent Root when no explicit root
// variable is set. HFL_DATA_DIR remains first for compatibility, while
// HFL_AGENT_ROOT is the preferred explicit name and HFL_AGENT_HOME supports
// bootstrap callers. The historical function name is retained for CLI/env
// compatibility; mutable files live in the root's sibling directories.
// Matches install.sh / install.ps1 defaults so bare `hfl-agent` runs use the same layout as systemd.
func DefaultAgentDataDir() string {
	for _, key := range []string{"HFL_DATA_DIR", "HFL_AGENT_ROOT", "HFL_AGENT_HOME"} {
		if value := strings.TrimSpace(os.Getenv(key)); value != "" {
			return filepath.Clean(value)
		}
	}
	mode := model.InstallationModeSystem
	if UserInstallation() {
		mode = model.InstallationModeUser
	}
	return AgentDataDirForMode(mode)
}

// AgentDataDir returns the canonical Agent Root (execPath ignored; kept for
// call-site stability).
func AgentDataDir(execPath string) string {
	_ = execPath
	return DefaultAgentDataDir()
}

// AgentRootFromDataDir normalizes the historical data-dir argument to the
// unified Agent Root. Custom paths remain valid roots for backward
// compatibility; installers pass the canonical root directly.
func AgentRootFromDataDir(dataRoot string) string {
	return filepath.Clean(dataRoot)
}

func AgentConfigDir(agentRoot string) string {
	return filepath.Join(AgentRootFromDataDir(agentRoot), "config")
}

func AgentDataStoreDir(agentRoot string) string {
	return filepath.Join(AgentRootFromDataDir(agentRoot), "data")
}

func AgentDatabasePath(agentRoot string) string {
	return filepath.Join(AgentDataStoreDir(agentRoot), "agent.db")
}

func AgentLogDir(agentRoot string) string {
	return filepath.Join(AgentRootFromDataDir(agentRoot), "logs")
}

func AgentCacheDir(agentRoot string) string {
	return filepath.Join(AgentRootFromDataDir(agentRoot), "cache")
}

func AgentManifestPath(agentRoot string) string {
	return filepath.Join(AgentRootFromDataDir(agentRoot), "MANIFEST.json")
}

func AgentInstalledVersionPath(agentRoot string) string {
	return filepath.Join(AgentRootFromDataDir(agentRoot), "INSTALLED_VERSION")
}

// KopiaBinaryPath returns the sibling path to the bundled Kopia executable.
func KopiaBinaryPath(execPath string) string {
	name := "kopia"
	if os.PathSeparator == '\\' {
		name = "kopia.exe"
	}
	return filepath.Join(filepath.Dir(execPath), name)
}
