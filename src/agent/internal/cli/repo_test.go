package cli

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestResolveKopiaConfigKeepsExternalConfigFastPath(t *testing.T) {
	dataDir := filepath.Join(t.TempDir(), "agent-root")
	configFile := filepath.Join(t.TempDir(), "external.config")

	got, err := resolveKopiaConfig(context.Background(), dataDir, "", configFile)
	if err != nil {
		t.Fatal(err)
	}
	if got != configFile {
		t.Fatalf("config path = %q, want %q", got, configFile)
	}
	if _, err := os.Stat(dataDir); !os.IsNotExist(err) {
		t.Fatalf("external config lookup initialized Agent state: %v", err)
	}
}
