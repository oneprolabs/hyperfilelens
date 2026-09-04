package controller

import (
	"path/filepath"
	"testing"
	"time"

	"hyperfilelens/agent/internal/infra/database"
)

func TestPendingLifecycleStartedAtIgnoresOrdinaryTasks(t *testing.T) {
	ctx := t.Context()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := database.NewTaskRepo(db)
	started := time.Now().UTC().Add(-2 * time.Minute)
	for _, input := range []database.RecordInput{
		{TaskID: "ordinary", Kind: "backup.run", StartedAt: &started},
		{TaskID: "lifecycle", Kind: "agent.upgrade", StartedAt: &started},
	} {
		if err := repo.RecordCommand(ctx, input); err != nil {
			t.Fatal(err)
		}
	}

	fixer := NewTaskFixer(repo, nil, "", "")
	got, err := fixer.PendingLifecycleStartedAt(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if got == nil || got.Sub(started).Abs() > time.Second {
		t.Fatalf("started_at = %v, want %v", got, started)
	}
}

func TestPendingLifecycleStartedAtReturnsNilWhenEmpty(t *testing.T) {
	ctx := t.Context()
	db, err := database.Open(ctx, filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	got, err := NewTaskFixer(database.NewTaskRepo(db), nil, "", "").PendingLifecycleStartedAt(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if got != nil {
		t.Fatalf("started_at = %v, want nil", got)
	}
}
