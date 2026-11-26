package kopialayout

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func cacheVerificationConfig(t *testing.T, root string, target string) (string, []byte) {
	t.Helper()
	config := filepath.Join(ConfigDir(root), "repo-42.config")
	if target == "" {
		target = CacheDir(root, config)
	}
	data, err := json.Marshal(map[string]any{
		"storage": map[string]any{"type": "filesystem", "config": map[string]any{"path": filepath.Join(root, "repository-not-available")}},
		"caching": map[string]any{"cacheDirectory": target},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := writeDurable(root, config, data); err != nil {
		t.Fatal(err)
	}
	return config, data
}

func TestCacheConnectionVerificationIsLocalAndDoesNotPersistSecrets(t *testing.T) {
	root, _ := managementFixture(t)
	config, before := cacheVerificationConfig(t, root, "")
	if err := VerifyCacheConnection(context.Background(), root, config); err != nil {
		t.Fatal(err)
	}
	after, err := os.ReadFile(config)
	if err != nil || string(after) != string(before) {
		t.Fatal("verification modified the managed connection")
	}
	expected := CacheDir(root, config)
	info, err := os.Stat(expected)
	if err != nil || !info.IsDir() {
		t.Fatal("new cache directory was not prepared")
	}
	probes, err := filepath.Glob(filepath.Join(expected, ".hfl-cache-probe-*"))
	if err != nil || len(probes) != 0 {
		t.Fatal("verification left a probe behind")
	}
	if _, err := os.Stat(filepath.Join(root, "repository-not-available")); !os.IsNotExist(err) {
		t.Fatal("verification touched repository data")
	}
}

func TestCacheConnectionVerificationRejectsWrongReferencesBeforeWriting(t *testing.T) {
	root, _ := managementFixture(t)
	foreign := filepath.Join(t.TempDir(), "must-not-create")
	config, _ := cacheVerificationConfig(t, root, foreign)
	if err := VerifyCacheConnection(context.Background(), root, config); err == nil {
		t.Fatal("foreign cache reference accepted")
	}
	if _, err := os.Stat(foreign); !os.IsNotExist(err) {
		t.Fatal("foreign directory was touched")
	}
	if _, err := os.Stat(CacheDir(root, config)); !os.IsNotExist(err) {
		t.Fatal("failed verification created an active cache namespace")
	}
	if err := VerifyCacheConnection(context.Background(), root, filepath.Join(root, "unmanaged.config")); err == nil {
		t.Fatal("unmanaged connection accepted")
	}
}

func TestCacheConnectionVerificationRejectsSymlinksAndKeepsExternalData(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation requires a privileged Windows fixture")
	}
	root, _ := managementFixture(t)
	config, _ := cacheVerificationConfig(t, root, "")
	external := t.TempDir()
	sentinel := filepath.Join(external, "sentinel")
	if err := os.WriteFile(sentinel, []byte("preserve me"), 0o600); err != nil {
		t.Fatal(err)
	}
	expected := CacheDir(root, config)
	if err := os.MkdirAll(filepath.Dir(expected), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(external, expected); err != nil {
		t.Fatal(err)
	}
	if err := VerifyCacheConnection(context.Background(), root, config); err == nil {
		t.Fatal("linked namespace accepted")
	}
	entries, err := os.ReadDir(external)
	if err != nil || len(entries) != 1 || entries[0].Name() != "sentinel" {
		t.Fatal("verification wrote outside the owned cache")
	}
	value, err := os.ReadFile(sentinel)
	if err != nil || string(value) != "preserve me" {
		t.Fatal("external data changed")
	}
}

func TestCacheConnectionVerificationHandlesCancellationAndMalformedConfig(t *testing.T) {
	root, _ := managementFixture(t)
	config, _ := cacheVerificationConfig(t, root, "")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := VerifyCacheConnection(ctx, root, config); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation: %v", err)
	}
	if err := writeDurable(root, config, []byte(`{"caching":"private-value-must-not-appear"}`)); err != nil {
		t.Fatal(err)
	}
	err := VerifyCacheConnection(context.Background(), root, config)
	if err == nil || strings.Contains(err.Error(), "private-value-must-not-appear") {
		t.Fatal("malformed config was accepted or leaked")
	}
}
