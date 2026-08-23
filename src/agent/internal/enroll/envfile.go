package enroll

import (
	"bufio"
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	agentconfig "hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/platform/atomicfile"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/vfs"
)

var managedSentryEnvKeys = []string{
	"HFL_SENTRY_POLICY_MANAGED",
	"SENTRY_ENABLED",
	"SENTRY_BACKEND_DSN",
	"SENTRY_ENVIRONMENT",
	"SENTRY_RELEASE",
	"SENTRY_TRACES_SAMPLE_RATE",
	"HFL_SENTRY_LENSNODE_RELEASE",
}

// bundledKopiaBinaryName is the install-dir Kopia filename for this OS.
// Windows packages ship kopia.exe; Unix packages ship kopia.
func bundledKopiaBinaryName() string {
	if runtime.GOOS == "windows" {
		return "kopia.exe"
	}
	return "kopia"
}

func bundledKopiaPath() string {
	return filepath.Join(install.DefaultInstallDir(), bundledKopiaBinaryName())
}

// WriteNodeID updates or appends HFL_NODE_ID in agent.env.
func WriteNodeID(envPath, nodeID string) error {
	return writeOrReplaceEnvKey(envPath, "HFL_NODE_ID", nodeID)
}

// WriteInstallationID persists the identity for the current installation lifetime.
func WriteInstallationID(envPath, installationID string) error {
	return writeOrReplaceEnvKey(envPath, "HFL_INSTALLATION_ID", installationID)
}

func writeOrReplaceEnvKey(envPath, key, value string) error {
	value = strings.TrimSpace(value)
	if value == "" {
		return fmt.Errorf("empty %s", key)
	}
	lines := make([]string, 0)
	if data, err := os.ReadFile(envPath); err == nil {
		prefix := key + "="
		for _, line := range strings.Split(string(data), "\n") {
			if strings.HasPrefix(strings.TrimSpace(line), prefix) {
				continue
			}
			if strings.TrimSpace(line) != "" {
				lines = append(lines, line)
			}
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	lines = append(lines, key+"="+value)
	return writePrivateEnvAtomically(envPath, []byte(strings.Join(lines, "\n")+"\n"))
}

// WriteNodeCredential replaces temporary enrollment material in agent.env.
func WriteNodeCredential(envPath, credential string) error {
	credential = strings.TrimSpace(credential)
	if credential == "" {
		return fmt.Errorf("empty node credential")
	}
	if err := agentconfig.ClearNodeTokenJSONOverride(filepath.Dir(envPath)); err != nil {
		return fmt.Errorf("clear stale JSON credential override: %w", err)
	}
	lines := []string{}
	if data, err := os.ReadFile(envPath); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			trimmed := strings.TrimSpace(line)
			if strings.HasPrefix(trimmed, "HFL_NODE_TOKEN=") ||
				strings.HasPrefix(trimmed, "HFL_NODE_CREDENTIAL=") {
				continue
			}
			if trimmed != "" {
				lines = append(lines, line)
			}
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	lines = append(lines, "HFL_NODE_CREDENTIAL="+credential)
	if err := os.MkdirAll(dirOf(envPath), 0o755); err != nil {
		return err
	}
	return writePrivateEnvAtomically(envPath, []byte(strings.Join(lines, "\n")+"\n"))
}

func installedNodeCredential() string {
	envPath := EnvFilePath()
	return firstNonEmptyValue(
		readEnvKey(envPath, "HFL_NODE_CREDENTIAL"),
		readEnvKey(envPath, "HFL_NODE_TOKEN"),
	)
}

func dirOf(path string) string {
	return filepath.Dir(path)
}

type enrollmentEnvSnapshot struct {
	path    string
	content []byte
	exists  bool
}

func (snapshot enrollmentEnvSnapshot) withNodeCredential(credential string) enrollmentEnvSnapshot {
	credential = strings.TrimSpace(credential)
	if credential == "" || !snapshot.exists {
		return snapshot
	}
	lines := make([]string, 0)
	for _, line := range strings.Split(string(snapshot.content), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "HFL_NODE_TOKEN=") ||
			strings.HasPrefix(trimmed, "HFL_NODE_CREDENTIAL=") {
			continue
		}
		if trimmed != "" {
			lines = append(lines, line)
		}
	}
	lines = append(lines, "HFL_NODE_CREDENTIAL="+credential)
	snapshot.content = []byte(strings.Join(lines, "\n") + "\n")
	return snapshot
}

func (snapshot enrollmentEnvSnapshot) withInstallationID(installationID string) enrollmentEnvSnapshot {
	installationID = strings.TrimSpace(installationID)
	if installationID == "" || !snapshot.exists {
		return snapshot
	}
	lines := make([]string, 0)
	for _, line := range strings.Split(string(snapshot.content), "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "HFL_INSTALLATION_ID=") {
			continue
		}
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	lines = append(lines, "HFL_INSTALLATION_ID="+installationID)
	snapshot.content = []byte(strings.Join(lines, "\n") + "\n")
	return snapshot
}

func captureEnrollmentEnv() (enrollmentEnvSnapshot, error) {
	return captureEnrollmentEnvAt(EnvFilePath())
}

func captureEnrollmentEnvAt(path string) (enrollmentEnvSnapshot, error) {
	content, err := os.ReadFile(path)
	if err == nil {
		return enrollmentEnvSnapshot{path: path, content: content, exists: true}, nil
	}
	if os.IsNotExist(err) {
		return enrollmentEnvSnapshot{path: path}, nil
	}
	return enrollmentEnvSnapshot{}, err
}

func (snapshot enrollmentEnvSnapshot) restore() error {
	if snapshot.exists {
		return writePrivateEnvAtomically(snapshot.path, snapshot.content)
	}
	if err := os.Remove(snapshot.path); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

// WriteEnrollmentEnv writes a complete first-time enrollment env file.
//
// Existing-install paths must not call this with a temporary installation
// session secret: use syncEnrollmentConsoleSettings instead so a durable
// HFL_NODE_CREDENTIAL is never replaced while the Agent may still be running.
func WriteEnrollmentEnv(cfg Config) error {
	envPath := EnvFilePath()
	dataDir := dataDirForAgent()
	kopiaPath := bundledKopiaPath()
	agentRoot := strings.TrimSpace(os.Getenv("HFL_AGENT_ROOT"))
	if agentRoot == "" {
		agentRoot = vfs.AgentRootForMode(cfg.InstallationMode)
	}
	insecure := "1"
	if !cfg.InsecureTLS {
		insecure = "0"
	}
	lines := []string{
		"HFL_WSS_URL=" + cfg.WSSURL,
		"HFL_API_BASE=" + cfg.APIBase,
		"HFL_ORG_KEY=" + cfg.OrgKey,
		"HFL_NODE_TOKEN=" + cfg.NodeToken,
		"HFL_DATA_DIR=" + dataDir,
		"HFL_AGENT_ROOT=" + agentRoot,
		"HFL_NODE_ROLE=" + string(cfg.NodeRole),
		"HFL_INSTALLATION_MODE=" + string(cfg.InstallationMode),
		"HFL_KOPIA_PATH=" + kopiaPath,
		"HFL_INSECURE_TLS=" + insecure,
	}
	if cfg.InstallationID != "" {
		lines = append(lines, "HFL_INSTALLATION_ID="+cfg.InstallationID)
	}
	if cfg.GatewayScope != "" {
		lines = append(lines, "HFL_GATEWAY_SCOPE="+cfg.GatewayScope)
	}
	if existing := ReadNodeID(envPath); existing != "" {
		lines = append(lines, "HFL_NODE_ID="+existing)
	}
	content := strings.Join(lines, "\n") + "\n"
	if err := os.MkdirAll(filepath.Dir(envPath), 0o755); err != nil {
		return err
	}
	return writePrivateEnvAtomically(envPath, []byte(content))
}

// syncEnrollmentConsoleSettings updates console connection settings in agent.env
// without replacing durable authentication material. Installation session secrets
// stay in-memory for control-plane calls while the Agent service may still run.
func syncEnrollmentConsoleSettings(cfg Config) error {
	return syncEnrollmentConsoleSettingsAt(EnvFilePath(), cfg)
}

func syncEnrollmentConsoleSettingsAt(envPath string, cfg Config) error {
	dataDir := dataDirForAgent()
	kopiaPath := bundledKopiaPath()
	agentRoot := strings.TrimSpace(os.Getenv("HFL_AGENT_ROOT"))
	if agentRoot == "" {
		agentRoot = vfs.AgentRootForMode(cfg.InstallationMode)
	}
	insecure := "1"
	if !cfg.InsecureTLS {
		insecure = "0"
	}
	updates := map[string]string{
		"HFL_WSS_URL":           cfg.WSSURL,
		"HFL_API_BASE":          cfg.APIBase,
		"HFL_ORG_KEY":           cfg.OrgKey,
		"HFL_DATA_DIR":          dataDir,
		"HFL_AGENT_ROOT":        agentRoot,
		"HFL_NODE_ROLE":         string(cfg.NodeRole),
		"HFL_INSTALLATION_MODE": string(cfg.InstallationMode),
		"HFL_KOPIA_PATH":        kopiaPath,
		"HFL_INSECURE_TLS":      insecure,
	}
	if cfg.InstallationID != "" {
		updates["HFL_INSTALLATION_ID"] = cfg.InstallationID
	}
	if cfg.GatewayScope != "" {
		updates["HFL_GATEWAY_SCOPE"] = cfg.GatewayScope
	}
	return upsertEnvKeysPreservingOthers(envPath, updates, map[string]struct{}{
		"HFL_NODE_TOKEN":      {},
		"HFL_NODE_CREDENTIAL": {},
		"HFL_NODE_ID":         {},
	})
}

func upsertEnvKeysPreservingOthers(
	envPath string,
	updates map[string]string,
	preserve map[string]struct{},
) error {
	existing := make([]string, 0)
	seen := make(map[string]bool, len(updates))
	if data, err := os.ReadFile(envPath); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			trimmed := strings.TrimSpace(line)
			if trimmed == "" {
				continue
			}
			key, _, found := strings.Cut(trimmed, "=")
			if !found {
				existing = append(existing, line)
				continue
			}
			if _, keep := preserve[key]; keep {
				existing = append(existing, line)
				continue
			}
			if value, ok := updates[key]; ok {
				existing = append(existing, key+"="+value)
				seen[key] = true
				continue
			}
			existing = append(existing, line)
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	for key, value := range updates {
		if seen[key] {
			continue
		}
		if _, keep := preserve[key]; keep {
			continue
		}
		existing = append(existing, key+"="+value)
	}
	if err := os.MkdirAll(filepath.Dir(envPath), 0o755); err != nil {
		return err
	}
	return writePrivateEnvAtomically(envPath, []byte(strings.Join(existing, "\n")+"\n"))
}

// SyncManagedObservabilityPolicy converges a server-verified platform policy.
// A disabled/private policy removes all previously managed Sentry credentials.
func SyncManagedObservabilityPolicy(policy ObservabilityPolicy) (bool, error) {
	return SyncManagedObservabilityPolicyAt(EnvFilePath(), policy)
}

// SyncManagedObservabilityPolicyAt converges policy in a resolved Agent env file.
func SyncManagedObservabilityPolicyAt(
	envPath string,
	policy ObservabilityPolicy,
) (bool, error) {
	return syncManagedSentryValues(envPath, policy.agentEnvValues())
}

func syncManagedSentryValues(envPath string, desired map[string]string) (bool, error) {
	current, err := os.ReadFile(envPath)
	if err != nil {
		return false, err
	}
	managed := make(map[string]struct{}, len(managedSentryEnvKeys))
	for _, name := range managedSentryEnvKeys {
		managed[name] = struct{}{}
	}
	written := make(map[string]bool, len(desired))
	lines := make([]string, 0, len(strings.Split(string(current), "\n"))+len(desired))
	for _, raw := range strings.Split(strings.TrimSuffix(string(current), "\n"), "\n") {
		key, _, found := strings.Cut(raw, "=")
		if _, controlled := managed[key]; !found || !controlled {
			lines = append(lines, raw)
			continue
		}
		if value, present := desired[key]; present && !written[key] {
			lines = append(lines, key+"="+value)
			written[key] = true
		}
	}
	for _, name := range managedSentryEnvKeys {
		if value, present := desired[name]; present && !written[name] {
			lines = append(lines, name+"="+value)
		}
	}
	updated := []byte(strings.Join(lines, "\n") + "\n")
	if bytes.Equal(current, updated) {
		return false, nil
	}
	if err := writePrivateEnvAtomically(envPath, updated); err != nil {
		return false, err
	}
	return true, nil
}

func writePrivateEnvAtomically(path string, content []byte) error {
	return atomicfile.Write(path, content, 0o600)
}

// ReadNodeID returns HFL_NODE_ID from agent.env if present.
func ReadNodeID(envPath string) string {
	f, err := os.Open(envPath)
	if err != nil {
		return ""
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if strings.HasPrefix(line, "HFL_NODE_ID=") {
			return strings.TrimSpace(strings.TrimPrefix(line, "HFL_NODE_ID="))
		}
	}
	return ""
}
