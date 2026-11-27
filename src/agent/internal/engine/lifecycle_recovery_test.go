package engine

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestUpgradeRejectsStagedTransactionBeforeDownloading(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "lifecycle"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "lifecycle", "upgrade-state.json"), []byte(`{"phase":"awaiting_restart"}`), 0600); err != nil {
		t.Fatal(err)
	}
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: root, InstallationMode: model.InstallationModeSystem}})
	status, result, _ := engine.runAgentUpgrade(context.Background(), ReporterSink{}, "retry", Payload{Extra: map[string]any{"target_version": "new"}})
	if status != "failed" || result["error_code"] != "AGENT_UPGRADE_RECOVERY_REQUIRED" {
		t.Fatalf("unsafe retry accepted: %s %v", status, result)
	}
	if _, err := os.Stat(filepath.Join(root, "runtime", "download")); !os.IsNotExist(err) {
		t.Fatalf("download started before recovery check: %v", err)
	}
}
