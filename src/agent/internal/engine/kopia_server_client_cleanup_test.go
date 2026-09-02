package engine

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
)

func TestEphemeralKopiaServerClientCleansAfterLastUser(t *testing.T) {
	dataDir := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: dataDir}})
	spec := repositorySpec{ID: 27, Type: "kopia_server", SessionID: "backup-session-1"}
	configFile := engine.repositoryConfigPath(spec)
	cacheDir := managedRepositoryCacheDir(engine.current(), configFile)
	if err := os.MkdirAll(cacheDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configFile, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(cacheDir, "cache-entry"), []byte("cache"), 0o600); err != nil {
		t.Fatal(err)
	}

	releaseFirst := engine.acquireEphemeralKopiaServerClientState(context.Background(), spec)
	releaseSecond := engine.acquireEphemeralKopiaServerClientState(context.Background(), spec)
	releaseFirst()
	if _, err := os.Stat(configFile); err != nil {
		t.Fatalf("client config removed while another task was active: %v", err)
	}
	releaseSecond()

	deadline := time.Now().Add(2 * time.Second)
	for {
		_, configErr := os.Stat(configFile)
		_, cacheErr := os.Stat(cacheDir)
		if os.IsNotExist(configErr) && os.IsNotExist(cacheErr) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("ephemeral client state was not cleaned: config_err=%v cache_err=%v", configErr, cacheErr)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestEphemeralKopiaServerClientDoesNotCleanUnmanagedConfig(t *testing.T) {
	dataDir := t.TempDir()
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: dataDir}})
	configFile := filepath.Join(t.TempDir(), "external.config")
	if err := os.WriteFile(configFile, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	spec := repositorySpec{
		Type:       "kopia_server",
		SessionID:  "external-session",
		ConfigFile: configFile,
	}

	engine.acquireEphemeralKopiaServerClientState(context.Background(), spec)()
	if _, err := os.Stat(configFile); err != nil {
		t.Fatalf("unmanaged client config was removed: %v", err)
	}
}
