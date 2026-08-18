package engine

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestEnforceInsightRestoreContentKeepsOnlyDirectoriesAndRegularFiles(t *testing.T) {
	root := t.TempDir()
	regular := filepath.Join(root, "docs", "report.txt")
	if err := os.MkdirAll(filepath.Dir(regular), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(regular, []byte("report"), 0o600); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "outside.txt")
	if err := os.WriteFile(outside, []byte("outside"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "docs", "outside-link")
	if err := os.Symlink(outside, link); err != nil {
		if runtime.GOOS == "windows" {
			t.Skipf("symlink creation requires Windows privileges: %v", err)
		}
		t.Fatal(err)
	}

	skipped, err := enforceInsightRestoreContent(root)
	if err != nil {
		t.Fatal(err)
	}
	if skipped != 1 {
		t.Fatalf("skipped = %d, want 1", skipped)
	}
	if _, err := os.Stat(regular); err != nil {
		t.Fatalf("regular file was removed: %v", err)
	}
	if _, err := os.Lstat(link); !os.IsNotExist(err) {
		t.Fatalf("symlink still exists: %v", err)
	}
	if got, err := os.ReadFile(outside); err != nil || string(got) != "outside" {
		t.Fatalf("outside target changed: data=%q err=%v", got, err)
	}
}
