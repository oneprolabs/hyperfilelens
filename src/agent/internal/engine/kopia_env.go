package engine

import (
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/performance"
	"hyperfilelens/agent/internal/platform/vfs"
)

func agentDataDir(cfg *model.AgentConfig) string {
	dataDir := strings.TrimSpace(cfg.DataDir)
	if dataDir == "" {
		dataDir = vfs.DefaultAgentDataDir()
	}
	if absolute, err := filepath.Abs(dataDir); err == nil {
		return filepath.Clean(absolute)
	}
	return filepath.Clean(dataDir)
}

func managedRepositoryCacheRoot(cfg *model.AgentConfig) string {
	return filepath.Join(vfs.AgentCacheDir(agentDataDir(cfg)), "repositories")
}

// managedRepositoryCacheDir isolates Kopia's format and content caches by the
// config file that owns them. Kopia embeds this path in its config on create or
// connect, so reconnecting an older config also migrates it away from the
// legacy shared cache directory.
func managedRepositoryCacheDir(cfg *model.AgentConfig, configFile string) string {
	return kopialayout.CacheDir(agentDataDir(cfg), configFile)
}

// ensureKopiaProcessEnv fills cache/home variables required when Agent runs as a
// service (systemd, Windows Service, LaunchDaemon) without a user shell profile.
// KOPIA_CACHE_DIRECTORY is set for all platforms; OS-specific vars cover Kopia
// fallbacks and tools that read os.UserCacheDir().
func ensureKopiaProcessEnv(cfg *model.AgentConfig, env map[string]string) (map[string]string, error) {
	root := agentDataDir(cfg)
	logs := strings.TrimSpace(cfg.LogDir)
	if logs == "" {
		logs = vfs.AgentLogDir(root)
	}
	return kopialayout.ProcessEnv(root, logs, performance.KopiaLogEnv(env))
}
