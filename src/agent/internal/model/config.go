package model

import "fmt"

// Role is how this binary participates in topology (aligned with backend node role enums).
type Role string

// InstallationMode defines the privilege and lifecycle boundary of an Agent install.
type InstallationMode string

const (
	RoleAgent   Role = "agent"
	RoleProxy   Role = "proxy"
	RoleGateway Role = "gateway"
)

const (
	InstallationModeSystem InstallationMode = "system"
	InstallationModeUser   InstallationMode = "user"
)

// ParseRole validates CLI role strings.
func ParseRole(s string) (Role, error) {
	switch Role(s) {
	case RoleAgent, RoleProxy, RoleGateway:
		return Role(s), nil
	default:
		return "", fmt.Errorf("invalid role %q", s)
	}
}

// ParseInstallationMode validates installation mode strings.
func ParseInstallationMode(s string) (InstallationMode, error) {
	switch InstallationMode(s) {
	case InstallationModeSystem, InstallationModeUser:
		return InstallationMode(s), nil
	default:
		return "", fmt.Errorf("invalid installation mode %q", s)
	}
}

// AgentConfig holds durable and runtime configuration fields.
// Values are populated from flags and HFL_* environment variables (see cmd/agent).
type AgentConfig struct {
	// WSSURL is the outbound WebSocket URL for runtime control (required for steady runtime).
	WSSURL string `json:"wss_url"`
	// APIBaseURL is optional HTTPS base for bootstrap and enrollment helpers.
	APIBaseURL       string           `json:"api_base_url"`
	OrgKey           string           `json:"org_key"`
	NodeID           string           `json:"node_id"`
	NodeToken        string           `json:"node_token"`
	InstallationID   string           `json:"installation_id"`
	InstallationMode InstallationMode `json:"installation_mode"`
	DataDir          string           `json:"data_dir"`
	// LogDir overrides the default rolling log directory ({DataDir}/logs). Empty uses DataDir/logs.
	LogDir string `json:"log_dir"`
	// KopiaPath is the path to the Kopia CLI; empty means rely on PATH.
	KopiaPath string `json:"kopia_path"`
	// BackupSnapshotConcurrency limits simultaneously running prepared snapshots.
	BackupSnapshotConcurrency int  `json:"backup_snapshot_concurrency"`
	Role                      Role `json:"role"`
}
