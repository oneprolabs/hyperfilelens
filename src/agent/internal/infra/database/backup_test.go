package database

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"
)

func TestBackupIncludesCommittedWALData(t *testing.T) {
	ctx := context.Background()
	source := filepath.Join(t.TempDir(), "agent.db")
	db, err := Open(ctx, source)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.conn.ExecContext(ctx, `INSERT INTO tasks (id, created_at, updated_at) VALUES ('backup-test', 'now', 'now')`); err != nil {
		t.Fatal(err)
	}

	destination := filepath.Join(t.TempDir(), "rollback", "agent.db")
	if err := Backup(ctx, source, destination); err != nil {
		t.Fatal(err)
	}
	copyDB, err := sql.Open("sqlite", "file:"+destination+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer copyDB.Close()
	var count int
	if err := copyDB.QueryRowContext(ctx, `SELECT COUNT(*) FROM tasks WHERE id = 'backup-test'`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("backup task count = %d, want 1", count)
	}
}

func TestBackupRejectsExistingDestination(t *testing.T) {
	ctx := context.Background()
	source := filepath.Join(t.TempDir(), "agent.db")
	db, err := Open(ctx, source)
	if err != nil {
		t.Fatal(err)
	}
	_ = db.Close()
	if err := Backup(ctx, source, source); err == nil {
		t.Fatal("Backup() accepted identical source and destination")
	}
}
