package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

// BootstrapAgentHome loads the unified root's config/agent.env into the
// environment (without overriding existing vars), then sets HFL_DATA_DIR to
// that root when still unset. The legacy root/agent.env location remains a
// read-only compatibility fallback for older installations.
func BootstrapAgentHome() error {
	home := strings.TrimSpace(os.Getenv("HFL_AGENT_HOME"))
	if home == "" {
		return nil
	}
	home = filepath.Clean(home)
	envPath := filepath.Join(vfs.AgentConfigDir(home), "agent.env")
	if _, err := os.Stat(envPath); os.IsNotExist(err) {
		envPath = filepath.Join(home, "agent.env")
	}
	if err := LoadEnvFile(envPath); err != nil {
		return err
	}
	if strings.TrimSpace(os.Getenv("HFL_DATA_DIR")) == "" {
		return os.Setenv("HFL_DATA_DIR", home)
	}
	return nil
}

// RuntimeFromEnv builds a config snapshot from HFL_* environment variables.
func RuntimeFromEnv() (*model.AgentConfig, error) {
	installationMode, err := installationModeFromEnv()
	if err != nil {
		return nil, err
	}
	role, err := roleFromEnv()
	if err != nil {
		return nil, err
	}
	return &model.AgentConfig{
		WSSURL:                    strings.TrimSpace(os.Getenv("HFL_WSS_URL")),
		APIBaseURL:                firstNonEmpty(strings.TrimSpace(os.Getenv("HFL_API_BASE")), strings.TrimSpace(os.Getenv("HFL_CONTROL_PLANE_API"))),
		OrgKey:                    strings.TrimSpace(os.Getenv("HFL_ORG_KEY")),
		NodeID:                    strings.TrimSpace(os.Getenv("HFL_NODE_ID")),
		InstallationID:            strings.TrimSpace(os.Getenv("HFL_INSTALLATION_ID")),
		InstallationMode:          installationMode,
		AgentRoot:                 strings.TrimSpace(os.Getenv("HFL_AGENT_ROOT")),
		RunAsUser:                 strings.TrimSpace(os.Getenv("HFL_RUN_AS_USER")),
		RunAsHome:                 strings.TrimSpace(os.Getenv("HFL_RUN_AS_HOME")),
		NodeToken:                 firstNonEmpty(strings.TrimSpace(os.Getenv("HFL_NODE_CREDENTIAL")), strings.TrimSpace(os.Getenv("HFL_NODE_TOKEN"))),
		DataDir:                   strings.TrimSpace(os.Getenv("HFL_DATA_DIR")),
		LogDir:                    strings.TrimSpace(os.Getenv("HFL_LOG_DIR")),
		KopiaPath:                 strings.TrimSpace(os.Getenv("HFL_KOPIA_PATH")),
		BackupSnapshotConcurrency: positiveEnvInt("HFL_BACKUP_SNAPSHOT_CONCURRENCY"),
		Role:                      role,
	}, nil
}

func installationModeFromEnv() (model.InstallationMode, error) {
	s := strings.TrimSpace(os.Getenv("HFL_INSTALLATION_MODE"))
	if s == "" {
		return model.InstallationModeSystem, nil
	}
	mode, err := model.ParseInstallationMode(s)
	if err != nil {
		return "", fmt.Errorf("HFL_INSTALLATION_MODE: %w", err)
	}
	return mode, nil
}

func positiveEnvInt(key string) int {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(key)))
	if err != nil || value < 1 {
		return 0
	}
	return value
}

func roleFromEnv() (model.Role, error) {
	s := strings.TrimSpace(os.Getenv("HFL_NODE_ROLE"))
	if s == "" {
		return "", nil
	}
	r, err := model.ParseRole(s)
	if err != nil {
		return "", fmt.Errorf("HFL_NODE_ROLE: %w", err)
	}
	return r, nil
}

func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}
