package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestStoreReloadFromEnvFile(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, agentEnvFileName)
	if err := WriteEnvFile(envPath, map[string]string{
		"HFL_WSS_URL":                     "wss://first.example/ws/node/agent/",
		"HFL_NODE_ROLE":                   "agent",
		"HFL_BACKUP_SNAPSHOT_CONCURRENCY": "4",
	}); err != nil {
		t.Fatal(err)
	}

	s := &Store{
		base:     &model.AgentConfig{},
		envPath:  envPath,
		jsonPath: filepath.Join(dir, configJSONName),
	}
	if err := s.reloadLocked(); err != nil {
		t.Fatal(err)
	}
	if got := s.Current().WSSURL; got != "wss://first.example/ws/node/agent/" {
		t.Fatalf("wss_url = %q", got)
	}
	if got := s.Current().BackupSnapshotConcurrency; got != 4 {
		t.Fatalf("backup_snapshot_concurrency = %d", got)
	}

	if err := WriteEnvFile(envPath, map[string]string{
		"HFL_WSS_URL":   "wss://second.example/ws/node/agent/",
		"HFL_NODE_ROLE": "proxy",
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Reload(t.Context()); err != nil {
		t.Fatal(err)
	}
	cfg := s.Current()
	if cfg.WSSURL != "wss://second.example/ws/node/agent/" {
		t.Fatalf("wss_url after reload = %q", cfg.WSSURL)
	}
	if cfg.Role != model.RoleAgent {
		t.Fatalf("role after reload = %q", cfg.Role)
	}
}

func TestParseEnvFileRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.env")
	values := map[string]string{
		"HFL_WSS_URL":    "wss://x/ws/",
		"HFL_NODE_TOKEN": "secret-token",
	}
	if err := WriteEnvFile(path, values); err != nil {
		t.Fatal(err)
	}
	got, err := ParseEnvFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if got["HFL_WSS_URL"] != values["HFL_WSS_URL"] {
		t.Fatalf("got %q", got["HFL_WSS_URL"])
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("mode = %o", info.Mode().Perm())
	}
}

func TestSetNodeCredentialRemovesStaleJSONOverride(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, agentEnvFileName)
	jsonPath := filepath.Join(dir, configJSONName)
	if err := WriteEnvFile(envPath, map[string]string{
		"HFL_NODE_CREDENTIAL": "old-credential",
		"HFL_NODE_ROLE":       "agent",
	}); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		jsonPath,
		[]byte("{\n  \"node_token\": \"stale-token\",\n  \"role\": \"agent\"\n}\n"),
		0o600,
	); err != nil {
		t.Fatal(err)
	}

	store := &Store{
		base:     &model.AgentConfig{},
		envPath:  envPath,
		jsonPath: jsonPath,
	}
	if err := store.reloadLocked(); err != nil {
		t.Fatal(err)
	}
	if got := store.Current().NodeToken; got != "stale-token" {
		t.Fatalf("initial node credential = %q", got)
	}

	if err := store.SetNodeCredential(t.Context(), "new-credential"); err != nil {
		t.Fatal(err)
	}
	if got := store.Current().NodeToken; got != "new-credential" {
		t.Fatalf("rotated node credential = %q", got)
	}
	jsonContent, err := os.ReadFile(jsonPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(jsonContent), "node_token") {
		t.Fatalf("stale JSON credential remains: %s", jsonContent)
	}
}

func TestInstallationModeIsReadOnlyAndIgnoresJSONOverlay(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, agentEnvFileName)
	jsonPath := filepath.Join(dir, configJSONName)
	if err := WriteEnvFile(envPath, map[string]string{
		"HFL_INSTALLATION_MODE": "user",
	}); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(jsonPath, []byte(`{"installation_mode":"system"}`), 0o600); err != nil {
		t.Fatal(err)
	}

	store := &Store{
		base:     &model.AgentConfig{InstallationMode: model.InstallationModeSystem},
		envPath:  envPath,
		jsonPath: jsonPath,
	}
	if err := store.reloadLocked(); err != nil {
		t.Fatal(err)
	}
	if got := store.Current().InstallationMode; got != model.InstallationModeUser {
		t.Fatalf("initial installation mode = %q", got)
	}
	if err := store.SetEnv(t.Context(), "HFL_INSTALLATION_MODE", "system"); err == nil || !strings.Contains(err.Error(), "installer-owned") {
		t.Fatalf("SetEnv read-only mode error = %v", err)
	}

	if err := WriteEnvFile(envPath, map[string]string{
		"HFL_INSTALLATION_MODE": "system",
	}); err != nil {
		t.Fatal(err)
	}
	if err := store.Reload(t.Context()); err != nil {
		t.Fatal(err)
	}
	if got := store.Current().InstallationMode; got != model.InstallationModeUser {
		t.Fatalf("reloaded installation mode = %q", got)
	}
	if err := store.SaveSnapshot(t.Context()); err != nil {
		t.Fatal(err)
	}
	values, err := ParseEnvFile(envPath)
	if err != nil {
		t.Fatal(err)
	}
	if got := values["HFL_INSTALLATION_MODE"]; got != "user" {
		t.Fatalf("persisted installation mode = %q", got)
	}
	jsonContent, err := os.ReadFile(jsonPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(jsonContent), "installation_mode") {
		t.Fatalf("JSON overlay persisted installation mode: %s", jsonContent)
	}
}

func TestInstallationIdentityFieldsIgnoreRuntimeOverrides(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, agentEnvFileName)
	jsonPath := filepath.Join(dir, configJSONName)
	if err := WriteEnvFile(envPath, map[string]string{
		"HFL_INSTALLATION_MODE": "user",
		"HFL_DATA_DIR":          "/managed/data",
		"HFL_NODE_ROLE":         "agent",
	}); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		jsonPath,
		[]byte(`{"data_dir":"/outside","role":"gateway"}`),
		0o600,
	); err != nil {
		t.Fatal(err)
	}
	store := &Store{
		base:     &model.AgentConfig{},
		envPath:  envPath,
		jsonPath: jsonPath,
	}
	if err := store.reloadLocked(); err != nil {
		t.Fatal(err)
	}
	if got := store.Current(); got.DataDir != "/managed/data" || got.Role != model.RoleAgent {
		t.Fatalf("installation identity = data %q role %q", got.DataDir, got.Role)
	}
	for key, value := range map[string]string{
		"HFL_DATA_DIR":  "/outside",
		"HFL_NODE_ROLE": "gateway",
	} {
		if err := store.SetEnv(t.Context(), key, value); err == nil || !strings.Contains(err.Error(), "owned") {
			t.Fatalf("SetEnv(%s) read-only error = %v", key, err)
		}
	}
	if err := WriteEnvFile(envPath, map[string]string{
		"HFL_INSTALLATION_MODE": "user",
		"HFL_DATA_DIR":          "/changed",
		"HFL_NODE_ROLE":         "gateway",
	}); err != nil {
		t.Fatal(err)
	}
	if err := store.Reload(t.Context()); err != nil {
		t.Fatal(err)
	}
	if got := store.Current(); got.DataDir != "/managed/data" || got.Role != model.RoleAgent {
		t.Fatalf("reloaded installation identity = data %q role %q", got.DataDir, got.Role)
	}
}

func TestReloadRejectsInvalidInstallationMode(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, agentEnvFileName)
	if err := WriteEnvFile(envPath, map[string]string{
		"HFL_INSTALLATION_MODE": "automatic",
	}); err != nil {
		t.Fatal(err)
	}
	store := &Store{
		base:     &model.AgentConfig{InstallationMode: model.InstallationModeSystem},
		envPath:  envPath,
		jsonPath: filepath.Join(dir, configJSONName),
	}
	if err := store.reloadLocked(); err == nil {
		t.Fatal("invalid installation mode should fail reload")
	}
}

func TestRuntimeFromEnvRejectsInvalidInstallationMode(t *testing.T) {
	t.Setenv("HFL_INSTALLATION_MODE", "automatic")
	if _, err := RuntimeFromEnv(); err == nil {
		t.Fatal("invalid process installation mode should be rejected")
	}
}

func TestRuntimeFromEnvRejectsInvalidRole(t *testing.T) {
	t.Setenv("HFL_INSTALLATION_MODE", "system")
	t.Setenv("HFL_NODE_ROLE", "worker")
	if _, err := RuntimeFromEnv(); err == nil {
		t.Fatal("invalid process role should be rejected")
	}
}
