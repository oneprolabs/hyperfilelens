package engine

import (
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/infra/kopialayout"
	"hyperfilelens/agent/internal/model"
)

func TestRawKopiaLayoutResolvesRedeliveredConfigAndIsolatesCache(t *testing.T) {
	root := t.TempDir()
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root}})
	old := filepath.Join(root, "cache", "repositories", "repo-42.config")
	args, config, env, err := e.rawKopiaLayout([]string{"--config-file=" + old, "snapshot", "list"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	next := filepath.Join(kopialayout.ConfigDir(root), "repo-42.config")
	if config != next || args[0] != "--config-file="+next || env["KOPIA_CONFIG_PATH"] != next {
		t.Fatal("redelivered task still references configuration in cache")
	}
	if managedRepositoryCacheDir(e.current(), next) != managedRepositoryCacheDir(e.current(), old) {
		t.Fatal("legacy entrypoint cannot reuse the warm cache")
	}
}

func TestRawKopiaLayoutRejectsHiddenFallbacksAndPathOverrides(t *testing.T) {
	root := t.TempDir()
	e := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root}})
	t.Setenv("KOPIA_CONFIG_PATH", "")
	for _, args := range [][]string{
		{"snapshot", "list"},
		{"--config-file=" + filepath.Join(root, "cache", "unmanaged.config"), "snapshot", "list"},
		{"--config-file=" + filepath.Join(root, "external.config"), "--cache-directory=" + t.TempDir(), "snapshot", "list"},
		{"--config-file=" + filepath.Join(root, "external.config"), "--log-dir=" + filepath.Join(root, "cache"), "snapshot", "list"},
	} {
		if _, _, _, err := e.rawKopiaLayout(args, nil); err == nil {
			t.Fatalf("unsafe raw entrypoint accepted: %v", args)
		}
	}
}
