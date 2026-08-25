package pathsize

import (
	"os"
	"path/filepath"
	"testing"
)

func TestEstimateWithExclusionsSkipsProtectedDirectory(t *testing.T) {
	root := t.TempDir()
	protected := filepath.Join(root, "agent")
	if err := os.MkdirAll(protected, 0o755); err != nil {
		t.Fatal(err)
	}
	writeSizeFile(t, filepath.Join(root, "included.bin"), 7)
	writeSizeFile(t, filepath.Join(protected, "internal.bin"), 101)
	size, err := EstimateWithExclusions(root, "directory", []string{protected})
	if err != nil {
		t.Fatal(err)
	}
	if size != 7 {
		t.Fatalf("expected protected data to be excluded, got %d", size)
	}
}

func TestWalkBytesDoesNotHideUnrelatedTraversalErrors(t *testing.T) {
	root := t.TempDir()
	missing := filepath.Join(root, "missing")
	if _, err := walkBytes(missing, []string{filepath.Join(root, "agent")}); !os.IsNotExist(err) {
		t.Fatalf("expected missing path error, got %v", err)
	}
}

func TestWalkBytesSkipsProtectedPathBeforeItsTraversalError(t *testing.T) {
	root := t.TempDir()
	protected := filepath.Join(root, "agent")
	size, err := walkBytes(protected, []string{protected})
	if err != nil {
		t.Fatalf("expected protected traversal error to be skipped: %v", err)
	}
	if size != 0 {
		t.Fatalf("unexpected size %d", size)
	}
}

func writeSizeFile(t *testing.T, path string, size int) {
	t.Helper()
	if err := os.WriteFile(path, make([]byte, size), 0o600); err != nil {
		t.Fatal(err)
	}
}
