package enroll

import (
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

// Config holds enrollment credentials from HFL_* environment variables.
type Config struct {
	OrgKey           string
	NodeRole         model.Role
	NodeToken        string
	InstallationID   string
	InstallationMode model.InstallationMode
	RunAsUser        string
	RunAsHome        string
	GatewayScope     string
	APIBase          string
	WSSURL           string
	InsecureTLS      bool
	// AgentRoot is the installer-owned root used for Gateway sidecar state.
	// It is persisted in agent.env so lifecycle commands can use the same
	// paths as the running Agent instead of relying on a global /etc location.
	AgentRoot string
}

// LoadConfigFromEnv reads enrollment settings injected by bootstrap stubs.
func LoadConfigFromEnv() (Config, error) {
	roleRaw := strings.TrimSpace(os.Getenv("HFL_NODE_ROLE"))
	if roleRaw == "" {
		roleRaw = "agent"
	}
	role, err := model.ParseRole(roleRaw)
	if err != nil {
		return Config{}, err
	}
	installationModeRaw := strings.TrimSpace(os.Getenv("HFL_INSTALLATION_MODE"))
	if installationModeRaw == "" {
		installationModeRaw = string(model.InstallationModeSystem)
	}
	installationMode, err := model.ParseInstallationMode(installationModeRaw)
	if err != nil {
		return Config{}, err
	}
	if installationMode.UserScoped() && role != model.RoleAgent {
		return Config{}, fmt.Errorf("user-scoped installation is only available for Source Agent")
	}
	runAsUser := strings.TrimSpace(os.Getenv("HFL_RUN_AS_USER"))
	runAsHome := strings.TrimSpace(os.Getenv("HFL_RUN_AS_HOME"))
	// Account mode is commonly started by a non-root user with `sudo` (the
	// bootstrap command shown in the console).  The elevated process must
	// retain that user's identity so the non-interactive bootstrap can install
	// the service without waiting for an answer on stdin, which is occupied by
	// the piped shell script.  Never infer root as the protected account.
	if installationMode == model.InstallationModeAccount && runAsUser == "" {
		if sudoUser := strings.TrimSpace(os.Getenv("SUDO_USER")); sudoUser != "" && sudoUser != "root" {
			runAsUser = sudoUser
		}
	}
	if installationMode == model.InstallationModeAccount && runAsHome == "" && runAsUser != "" {
		if account, lookupErr := user.Lookup(runAsUser); lookupErr == nil {
			runAsHome = account.HomeDir
		}
	}
	cfg := Config{
		OrgKey:           strings.TrimSpace(os.Getenv("HFL_ORG_KEY")),
		NodeRole:         role,
		NodeToken:        firstNonEmptyValue(os.Getenv("HFL_NODE_CREDENTIAL"), os.Getenv("HFL_NODE_TOKEN")),
		InstallationID:   strings.TrimSpace(os.Getenv("HFL_INSTALLATION_ID")),
		InstallationMode: installationMode,
		RunAsUser:        runAsUser,
		RunAsHome:        runAsHome,
		GatewayScope:     strings.TrimSpace(os.Getenv("HFL_GATEWAY_SCOPE")),
		APIBase:          strings.TrimRight(strings.TrimSpace(os.Getenv("HFL_API_BASE")), "/"),
		WSSURL:           strings.TrimSpace(os.Getenv("HFL_WSS_URL")),
		InsecureTLS:      os.Getenv("HFL_INSECURE_TLS") != "0",
		AgentRoot:        strings.TrimSpace(os.Getenv("HFL_AGENT_ROOT")),
	}
	if cfg.AgentRoot != "" && (!filepath.IsAbs(cfg.AgentRoot) || filepath.Clean(cfg.AgentRoot) == string(filepath.Separator)) {
		return Config{}, fmt.Errorf("HFL_AGENT_ROOT must be an absolute non-root path")
	}
	if cfg.OrgKey == "" || cfg.NodeToken == "" || cfg.APIBase == "" {
		return Config{}, fmt.Errorf("HFL_ORG_KEY, HFL_NODE_TOKEN, and HFL_API_BASE are required")
	}
	if !cfg.InsecureTLS {
		_ = os.Setenv("HFL_INSECURE_TLS", "0")
	} else {
		_ = os.Setenv("HFL_INSECURE_TLS", "1")
	}
	return cfg, nil
}

func firstNonEmptyValue(values ...string) string {
	for _, value := range values {
		if value = strings.TrimSpace(value); value != "" {
			return value
		}
	}
	return ""
}

// LoadInstalledCommandEnv supplies local lifecycle commands from agent.env.
func LoadInstalledCommandEnv() {
	envPath := installedEnvPath()
	for _, key := range []string{
		"HFL_WSS_URL",
		"HFL_API_BASE",
		"HFL_ORG_KEY",
		"HFL_NODE_ROLE",
		"HFL_NODE_CREDENTIAL",
		"HFL_NODE_TOKEN",
		"HFL_INSTALLATION_ID",
		"HFL_INSTALLATION_MODE",
		"HFL_AGENT_ROOT",
		"HFL_RUN_AS_USER",
		"HFL_RUN_AS_HOME",
		"HFL_INSECURE_TLS",
		"HFL_GATEWAY_SCOPE",
	} {
		if strings.TrimSpace(os.Getenv(key)) != "" {
			continue
		}
		if value := readEnvKey(envPath, key); value != "" {
			_ = os.Setenv(key, value)
		}
	}
}

// AgentConfig converts to model.AgentConfig for release/register APIs.
func (c Config) AgentConfig() *model.AgentConfig {
	envPath := installedEnvPath()
	return &model.AgentConfig{
		WSSURL:           c.WSSURL,
		APIBaseURL:       c.APIBase,
		OrgKey:           c.OrgKey,
		NodeToken:        c.NodeToken,
		InstallationID:   c.InstallationID,
		InstallationMode: c.InstallationMode,
		AgentRoot:        c.AgentRoot,
		RunAsUser:        c.RunAsUser,
		RunAsHome:        c.RunAsHome,
		NodeID:           ReadNodeID(envPath),
		Role:             c.NodeRole,
		DataDir:          dataDirForAgent(),
	}
}

// EnvFilePath returns the platform default agent.env path.
func EnvFilePath() string {
	return filepath.Join(vfs.AgentConfigDir(dataDirForAgent()), "agent.env")
}
