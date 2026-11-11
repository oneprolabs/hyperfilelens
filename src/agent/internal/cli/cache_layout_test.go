package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/infra/database"
	"hyperfilelens/agent/internal/model"
)

func TestCacheLayoutIsAShortLivedSubcommand(t *testing.T) {
	if !IsSubcommand("cache-layout") {
		t.Fatal("installer preflight must not start the Agent daemon")
	}
}

func TestUpgradeCachePreflightPreservesRunningRepositoryTasks(t *testing.T) {
	root := t.TempDir()
	db, err := database.Open(t.Context(), database.DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	tasks := database.NewTaskRepo(db)
	now := time.Now()
	if err := tasks.RecordCommand(t.Context(), database.RecordInput{
		TaskID: "active-backup", Kind: "backup", StartedAt: &now,
	}); err != nil {
		t.Fatal(err)
	}
	err = runCacheLayout([]string{"check-users", "--data-dir", root})
	if err == nil || !strings.Contains(err.Error(), "CACHE_LAYOUT_BUSY") {
		t.Fatalf("upgrade was not deferred: %v", err)
	}
	task, err := tasks.Get(t.Context(), "active-backup")
	if err != nil || task.Status != model.TaskStatusRunning {
		t.Fatal("upgrade preflight changed active work")
	}
	if err := tasks.Finish(t.Context(), "active-backup", model.TaskStatusSucceeded, nil, ""); err != nil {
		t.Fatal(err)
	}
	if err := runCacheLayout([]string{"check-users", "--data-dir", root}); err != nil {
		t.Fatal(err)
	}
}

func TestCachePreflightDoesNotCreatePersistentStateForAbsentInstallation(t *testing.T) {
	root := filepath.Join(t.TempDir(), "not-installed")
	if err := runCacheLayout([]string{"check-users", "--data-dir", root}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatal("read-only preflight created installation files")
	}
}
