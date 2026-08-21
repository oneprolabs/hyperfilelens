//go:build windows

package vfs

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestResolveUserScopedPathWindowsHomeBoundary(t *testing.T) {
	root := t.TempDir()
	home := filepath.Join(root, "home")
	outside := filepath.Join(root, "outside")
	if err := os.Mkdir(home, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(outside, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("USERPROFILE", home)

	documents := filepath.Join(home, "Documents")
	if err := os.Mkdir(documents, 0o700); err != nil {
		t.Fatal(err)
	}
	resolved, err := ResolveUserScopedPath(documents, false)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.EqualFold(filepath.Clean(resolved), filepath.Clean(documents)) {
		t.Fatalf("Home path resolved to %q, want %q", resolved, documents)
	}

	restoreTarget := filepath.Join(home, "Restore", "document.txt")
	resolved, err = ResolveUserScopedPath(restoreTarget, true)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.EqualFold(filepath.Clean(resolved), filepath.Clean(restoreTarget)) {
		t.Fatalf("restore path resolved to %q, want %q", resolved, restoreTarget)
	}

	if _, err := ResolveUserScopedPath(outside, false); err == nil {
		t.Fatal("path outside Home should be rejected")
	}
}
