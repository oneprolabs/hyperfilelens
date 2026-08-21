package config

import (
	"fmt"
	"strconv"
	"strings"

	"hyperfilelens/agent/internal/model"
)

func cloneConfig(src *model.AgentConfig) *model.AgentConfig {
	if src == nil {
		return &model.AgentConfig{}
	}
	cp := *src
	return &cp
}

func applyEnvMap(cfg *model.AgentConfig, values map[string]string) error {
	if cfg == nil || len(values) == 0 {
		return nil
	}
	for env, val := range values {
		val = strings.TrimSpace(val)
		if val == "" {
			continue
		}
		f, ok := fieldByEnv(env)
		if !ok {
			continue
		}
		switch f.Key {
		case "wss_url":
			cfg.WSSURL = val
		case "api_base_url":
			cfg.APIBaseURL = val
		case "org_key":
			cfg.OrgKey = val
		case "node_id":
			cfg.NodeID = val
		case "installation_id":
			cfg.InstallationID = val
		case "installation_mode":
			mode, err := model.ParseInstallationMode(val)
			if err != nil {
				return fmt.Errorf("%s: %w", env, err)
			}
			cfg.InstallationMode = mode
		case "node_token":
			cfg.NodeToken = val
		case "data_dir":
			cfg.DataDir = val
		case "log_dir":
			cfg.LogDir = val
		case "kopia_path":
			cfg.KopiaPath = val
		case "backup_snapshot_concurrency":
			if parsed, err := strconv.Atoi(val); err == nil && parsed > 0 {
				cfg.BackupSnapshotConcurrency = parsed
			}
		case "role":
			r, err := model.ParseRole(val)
			if err != nil {
				return fmt.Errorf("%s: %w", env, err)
			}
			cfg.Role = r
		}
	}
	return nil
}

func applyOverrides(cfg *model.AgentConfig, o Overrides) {
	if cfg == nil {
		return
	}
	if s := strings.TrimSpace(o.WSSURL); s != "" {
		cfg.WSSURL = s
	}
	if s := strings.TrimSpace(o.APIBaseURL); s != "" {
		cfg.APIBaseURL = s
	}
	if s := strings.TrimSpace(o.OrgKey); s != "" {
		cfg.OrgKey = s
	}
	if s := strings.TrimSpace(o.NodeID); s != "" {
		cfg.NodeID = s
	}
	if s := strings.TrimSpace(o.NodeToken); s != "" {
		cfg.NodeToken = s
	}
	if s := strings.TrimSpace(o.DataDir); s != "" {
		cfg.DataDir = s
	}
	if s := strings.TrimSpace(o.LogDir); s != "" {
		cfg.LogDir = s
	}
	if s := strings.TrimSpace(o.KopiaPath); s != "" {
		cfg.KopiaPath = s
	}
	if o.Role != "" {
		if r, err := model.ParseRole(string(o.Role)); err == nil {
			cfg.Role = r
		}
	}
}

func configToEnvMap(cfg *model.AgentConfig) map[string]string {
	if cfg == nil {
		return nil
	}
	out := map[string]string{}
	set := func(key, val string) {
		env := envByKey(key)
		if env == "" || strings.TrimSpace(val) == "" {
			return
		}
		out[env] = strings.TrimSpace(val)
	}
	set("wss_url", cfg.WSSURL)
	set("api_base_url", cfg.APIBaseURL)
	set("org_key", cfg.OrgKey)
	set("node_id", cfg.NodeID)
	set("installation_id", cfg.InstallationID)
	set("installation_mode", string(cfg.InstallationMode))
	set("node_token", cfg.NodeToken)
	set("data_dir", cfg.DataDir)
	set("log_dir", cfg.LogDir)
	set("kopia_path", cfg.KopiaPath)
	if cfg.BackupSnapshotConcurrency > 0 {
		set("backup_snapshot_concurrency", strconv.Itoa(cfg.BackupSnapshotConcurrency))
	}
	if cfg.Role != "" {
		set("role", string(cfg.Role))
	}
	return out
}
