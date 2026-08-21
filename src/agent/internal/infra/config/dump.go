package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

func resolveLayout(cfg *model.AgentConfig) (dataRoot, logDir, cacheDir string, err error) {
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
	cacheDir = filepath.Clean(vfs.AgentCacheDir(dataRoot))
	return dataRoot, logDir, cacheDir, nil
}

// DumpText prints human-readable effective configuration (secrets masked).
func DumpText(cfg *model.AgentConfig) (string, error) {
	if cfg == nil {
		cfg = &model.AgentConfig{}
	}
	dataRoot, logDir, cacheDir, err := resolveLayout(cfg)
	if err != nil {
		return "", err
	}
	mask := func(s string) string {
		if s == "" {
			return ""
		}
		if len(s) <= 6 {
			return "(set)"
		}
		return s[:4] + "..." + s[len(s)-2:]
	}
	tok := cfg.NodeToken
	if tok != "" {
		tok = mask(tok)
	}
	var b strings.Builder
	fmt.Fprintf(&b, "role=%s\n", cfg.Role)
	fmt.Fprintf(&b, "wss_url=%q\n", cfg.WSSURL)
	fmt.Fprintf(&b, "api_base_url=%q\n", cfg.APIBaseURL)
	fmt.Fprintf(&b, "data_dir=%q\n", dataRoot)
	fmt.Fprintf(&b, "log_dir=%q\n", logDir)
	fmt.Fprintf(&b, "cache_dir=%q\n", cacheDir)
	fmt.Fprintf(&b, "kopia_path=%q\n", cfg.KopiaPath)
	fmt.Fprintf(&b, "backup_snapshot_concurrency=%d\n", effectiveBackupSnapshotConcurrency(cfg))
	fmt.Fprintf(&b, "org_key=%q\n", cfg.OrgKey)
	fmt.Fprintf(&b, "node_id=%q\n", cfg.NodeID)
	fmt.Fprintf(&b, "installation_id=%q\n", cfg.InstallationID)
	fmt.Fprintf(&b, "installation_mode=%q\n", cfg.InstallationMode)
	fmt.Fprintf(&b, "node_token=%q\n", tok)
	return b.String(), nil
}

func effectiveBackupSnapshotConcurrency(cfg *model.AgentConfig) int {
	if cfg != nil && cfg.BackupSnapshotConcurrency > 0 {
		return cfg.BackupSnapshotConcurrency
	}
	return 2
}
