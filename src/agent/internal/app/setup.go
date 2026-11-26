package app

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/identity"
	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/infra/logging"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/vfs"
	"log/slog"
)

// ResolveLayout returns canonical data, log, and cache directories without creating them.
func ResolveLayout(cfg *model.AgentConfig) (dataRoot, logDir, cacheDir string, err error) {
	if cfg == nil {
		cfg = &model.AgentConfig{}
	}
	if strings.TrimSpace(cfg.DataDir) != "" {
		dataRoot = filepath.Clean(strings.TrimSpace(cfg.DataDir))
	} else {
		execPath, exErr := os.Executable()
		if exErr != nil {
			return "", "", "", exErr
		}
		dataRoot = filepath.Clean(vfs.AgentDataDir(execPath))
	}
	if strings.TrimSpace(cfg.LogDir) != "" {
		logDir = filepath.Clean(strings.TrimSpace(cfg.LogDir))
	} else {
		logDir = filepath.Clean(vfs.AgentLogDir(dataRoot))
	}
	active, err := kopialayout.ReadActiveRoot(dataRoot)
	if err != nil {
		return "", "", "", err
	}
	cacheDir = active.Path
	return dataRoot, logDir, cacheDir, nil
}

// InitRuntimeDirsAndLogging resolves paths, writes cfg.DataDir, creates data/cache dirs, and configures rolling logs.
func InitRuntimeDirsAndLogging(cfg *model.AgentConfig) error {
	if cfg == nil {
		return fmt.Errorf("nil config")
	}
	dataRoot, logDir, cacheDir, err := ResolveLayout(cfg)
	if err != nil {
		return err
	}
	cfg.DataDir = dataRoot
	if err := os.MkdirAll(dataRoot, 0o755); err != nil {
		return err
	}
	if err := ensureRuntimeCache(dataRoot, cacheDir); err != nil {
		return err
	}
	if err := logging.Setup(logDir, cfg.OrgKey); err != nil {
		return err
	}
	slog.Info("agent logging initialized", "log_dir", logDir)
	return nil
}

// Setup performs environment self-checks and initialization before steady runtime.
func Setup(ctx context.Context, cfg *model.AgentConfig) error {
	if cfg == nil {
		cfg = &model.AgentConfig{}
	}

	dataRoot, _, cacheDir, err := ResolveLayout(cfg)
	if err != nil {
		return err
	}
	cfg.DataDir = dataRoot

	if err := os.MkdirAll(dataRoot, 0o755); err != nil {
		return err
	}
	if err := ensureRuntimeCache(dataRoot, cacheDir); err != nil {
		return err
	}

	if _, err := identity.MachineID(ctx); err != nil {
		return err
	}

	resolved, err := kopia.Resolve(cfg.KopiaPath)
	if err != nil {
		return err
	}
	cfg.KopiaPath = resolved
	return nil
}

func ensureRuntimeCache(root, expected string) error {
	a, err := kopialayout.EnsureActiveRoot(root)
	if err != nil {
		return err
	}
	if a.Path != expected {
		return fmt.Errorf("active cache root changed during setup")
	}
	return nil
}
