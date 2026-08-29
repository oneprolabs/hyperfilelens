package pathsize

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
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

func TestEstimateWithExclusionsKeepsNestedDirectoryWithSameName(t *testing.T) {
	root := t.TempDir()
	protected := filepath.Join(root, "proc")
	nested := filepath.Join(root, "data", "proc")
	if err := os.MkdirAll(protected, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(nested, 0o755); err != nil {
		t.Fatal(err)
	}
	writeSizeFile(t, filepath.Join(protected, "system.bin"), 101)
	writeSizeFile(t, filepath.Join(nested, "user.bin"), 7)
	size, err := EstimateWithExclusions(root, "directory", []string{protected})
	if err != nil {
		t.Fatal(err)
	}
	if size != 7 {
		t.Fatalf("expected same-named user directory to remain included, got %d", size)
	}
}

func TestWalkBytesDoesNotHideUnrelatedTraversalErrors(t *testing.T) {
	root := t.TempDir()
	missing := filepath.Join(root, "missing")
	if _, err := walkBytes(context.Background(), missing, []string{filepath.Join(root, "agent")}); !os.IsNotExist(err) {
		t.Fatalf("expected missing path error, got %v", err)
	}
}

func TestWalkBytesSkipsProtectedPathBeforeItsTraversalError(t *testing.T) {
	root := t.TempDir()
	protected := filepath.Join(root, "agent")
	size, err := walkBytes(context.Background(), protected, []string{protected})
	if err != nil {
		t.Fatalf("expected protected traversal error to be skipped: %v", err)
	}
	if size != 0 {
		t.Fatalf("unexpected size %d", size)
	}
}

func TestEstimateWithExclusionsContextHonorsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := EstimateWithExclusionsContext(ctx, t.TempDir(), "directory", nil)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context cancellation, got %v", err)
	}
}

func TestDuErrorDoesNotTriggerSecondWalk(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("du is not used on Windows")
	}
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "file"), []byte("data"), 0o600); err != nil {
		t.Fatal(err)
	}
	bin := t.TempDir()
	du := filepath.Join(bin, "du")
	if err := os.WriteFile(du, []byte("#!/bin/sh\necho 'generic failure' >&2\nexit 1\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin)

	_, err := EstimateWithExclusionsContext(context.Background(), root, "directory", nil)
	if err == nil || !strings.Contains(err.Error(), "du:") {
		t.Fatalf("expected du error without WalkDir fallback, got %v", err)
	}
}

func writeSizeFile(t *testing.T, path string, size int) {
	t.Helper()
	if err := os.WriteFile(path, make([]byte, size), 0o600); err != nil {
		t.Fatal(err)
	}
}
