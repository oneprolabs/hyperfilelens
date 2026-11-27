package cli

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"hyperfilelens/agent/internal/infra/database"
)

func TestRollbackVerificationPreservesMaterialsAndRejectsMismatch(t *testing.T) {
	root := t.TempDir()
	ctx := context.Background()
	write := func(path string, raw []byte) {
		t.Helper()
		path = filepath.Join(root, path)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, raw, 0600); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{"hfl-agent", "kopia", "install.sh"} {
		write("bin/"+name, []byte("saved binary"))
		write("backup/rollback/bin/"+name, []byte("saved binary"))
	}
	for _, name := range []string{"MANIFEST.json", "INSTALLED_VERSION"} {
		write(name, []byte("identity"))
		write("backup/rollback/bin/"+name, []byte("identity"))
	}
	meta, _ := json.Marshal(map[string]any{"previous_version": "old"})
	write("backup/rollback/meta.json", meta)
	write("config/agent.env", []byte("unchanged test config"))
	db, err := database.Open(ctx, database.DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	db.Close()
	raw, err := os.ReadFile(database.DefaultPath(root))
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "backup/rollback/latest.tar.gz")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for name, content := range map[string][]byte{"./config/agent.env": []byte("unchanged test config"), "./data/agent.db": raw} {
		if err := tw.WriteHeader(&tar.Header{Name: name, Typeflag: tar.TypeReg, Mode: 0600, Size: int64(len(content))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(content); err != nil {
			t.Fatal(err)
		}
	}
	tw.Close()
	gz.Close()
	f.Close()
	if err := verifyRollback(ctx, root, "old"); err != nil {
		t.Fatal(err)
	}
	write("config/agent.env", []byte("different config"))
	var verification *rollbackVerificationError
	if err := verifyRollback(ctx, root, "old"); !errors.As(err, &verification) || verification.code != "AGENT_UPGRADE_ROLLBACK_FILES_UNVERIFIED" {
		t.Fatalf("config mismatch not reported: %v", err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal("recovery materials removed")
	}
	if err := verifyRollback(ctx, root, "other"); err == nil {
		t.Fatal("accepted different old version")
	}
}

func TestRollbackVerificationTimeoutIsDistinctAndKeepsDeadlineCause(t *testing.T) {
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	err := verifyRollback(ctx, t.TempDir(), "old")
	var verification *rollbackVerificationError
	if !errors.As(err, &verification) || verification.code != "AGENT_UPGRADE_ROLLBACK_VERIFICATION_TIMEOUT" {
		t.Fatalf("deadline reported as data mismatch: %v", err)
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("deadline cause not retained: %v", err)
	}
}
