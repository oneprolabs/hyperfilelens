package engine

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestCaptureFilesystemEntriesDirectAndRecursive(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "root.txt"), []byte("root"), 0o600); err != nil {
		t.Fatal(err)
	}
	sub := filepath.Join(root, "sub")
	if err := os.Mkdir(sub, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(sub, "child.txt"), []byte("child"), 0o600); err != nil {
		t.Fatal(err)
	}
	empty := filepath.Join(root, "empty")
	if err := os.Mkdir(empty, 0o700); err != nil {
		t.Fatal(err)
	}

	direct, directoryCount, err := captureFilesystemEntries(context.Background(), root, false, 10)
	if err != nil {
		t.Fatal(err)
	}
	if directoryCount != 2 || len(direct) != 3 || direct[0].path != empty || !direct[0].isDir {
		t.Fatalf("direct capture = %#v", direct)
	}
	recursive, directoryCount, err := captureFilesystemEntries(context.Background(), root, true, 10)
	if err != nil {
		t.Fatal(err)
	}
	if directoryCount != 2 || len(recursive) != 3 || recursive[0].path != empty || !recursive[0].isDir {
		t.Fatalf("recursive capture = %#v", recursive)
	}
}

func TestCaptureFilesystemEntriesFailsWithoutPartialManifestAtLimit(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"a", "b"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte(name), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	if _, _, err := captureFilesystemEntries(context.Background(), root, false, 1); err == nil {
		t.Fatal("capture should fail when the manifest exceeds its limit")
	}
}
