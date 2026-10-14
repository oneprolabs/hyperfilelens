package logging

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestOptionalLogWriterIsBoundedAndKeepsDiagnostics(t *testing.T) {
	p := &policyLog{queue: make(chan []byte, 1), stop: make(chan struct{})}
	start := time.Now()
	for i := 0; i < 1000; i++ {
		data := []byte("startup diagnostic " + strconv.Itoa(i) + "\n")
		if n, err := p.Write(data); err != nil || n != len(data) {
			t.Fatal(n, err)
		}
	}
	if time.Since(start) > time.Second {
		t.Fatal("full logging queue blocked a business writer")
	}
	if !strings.Contains(RuntimeLogTail(p), "diagnostic 999") {
		t.Fatal("startup diagnostics were lost")
	}
	_ = p.Close()
	policyLogs.dropped.Store(0)
}

func TestUnavailableLogDirectoryDoesNotFailAgentStartup(t *testing.T) {
	path := filepath.Join(t.TempDir(), "not-a-directory")
	if err := os.WriteFile(path, []byte("occupied"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := Setup(path, "test"); err != nil {
		t.Fatal("optional logging stopped startup:", err)
	}
}

func TestKopiaCleanupPreservesLiveUnrelatedAndSymlinkFiles(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "cli-logs")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	dead := filepath.Join(dir, "kopia-20200101-000000-2147483647-snapshot-create.0.log")
	live := filepath.Join(dir, "kopia-20200101-000000-"+strconv.Itoa(os.Getpid())+"-snapshot-create.0.log")
	unrelated := filepath.Join(dir, "backup-data.log")
	for _, path := range []string{dead, live, unrelated} {
		if err := os.WriteFile(path, []byte("retained"), 0600); err != nil {
			t.Fatal(err)
		}
		old := time.Now().Add(-48 * time.Hour)
		if err := os.Chtimes(path, old, old); err != nil {
			t.Fatal(err)
		}
	}
	link := filepath.Join(dir, "kopia-20200101-000000-2147483646-snapshot-create.0.log")
	if err := os.Symlink(unrelated, link); err != nil {
		t.Log("symlink not available:", err)
	}
	if err := SweepKopiaLogs(root, 1, 32, time.Now()); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(dead); !os.IsNotExist(err) {
		t.Fatal("expired inactive segment retained")
	}
	for _, path := range []string{live, unrelated} {
		if _, err := os.Stat(path); err != nil {
			t.Fatal("protected file deleted:", path, err)
		}
	}
}
