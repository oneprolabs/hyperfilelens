package config

import (
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestResolveDataDirUsesExplicitAgentRoot(t *testing.T) {
	t.Setenv("HFL_DATA_DIR", "")
	t.Setenv("HFL_AGENT_HOME", "")
	root := filepath.Join(t.TempDir(), "Agent")
	t.Setenv("HFL_AGENT_ROOT", root)

	got, err := ResolveDataDir(Overrides{})
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Clean(root); got != want {
		t.Fatalf("ResolveDataDir() = %q, want %q", got, want)
	}
}

func TestResolveDataDirKeepsHistoricalDataDirPrecedence(t *testing.T) {
	dataRoot := filepath.Join(t.TempDir(), "legacy-root")
	root := filepath.Join(t.TempDir(), "Agent")
	t.Setenv("HFL_DATA_DIR", dataRoot)
	t.Setenv("HFL_AGENT_ROOT", root)
	t.Setenv("HFL_AGENT_HOME", "")

	got, err := ResolveDataDir(Overrides{})
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Clean(dataRoot); got != want {
		t.Fatalf("ResolveDataDir() = %q, want historical HFL_DATA_DIR %q", got, want)
	}
}

func TestResolveDataDirOverrideWins(t *testing.T) {
	t.Setenv("HFL_DATA_DIR", filepath.Join(t.TempDir(), "env-root"))
	t.Setenv("HFL_AGENT_ROOT", filepath.Join(t.TempDir(), "agent-root"))
	override := filepath.Join(t.TempDir(), "override-root")

	got, err := ResolveDataDir(Overrides{DataDir: override, Role: model.RoleAgent})
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Clean(override); got != want {
		t.Fatalf("ResolveDataDir() = %q, want override %q", got, want)
	}
}
