package vfs

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestResolveUserScopedPathAllowsHomeAndMissingRestoreTarget(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test uses Unix HOME semantics")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)

	existing := filepath.Join(home, "Documents")
	if err := os.Mkdir(existing, 0o700); err != nil {
		t.Fatal(err)
	}
	resolved, err := ResolveUserScopedPath(existing, false)
	if err != nil || resolved != existing {
		t.Fatalf("existing path resolved to %q, err=%v", resolved, err)
	}

	target := filepath.Join(home, "Restore", "new-file.txt")
	resolved, err = ResolveUserScopedPath(target, true)
	if err != nil || resolved != target {
		t.Fatalf("missing target resolved to %q, err=%v", resolved, err)
	}
}

func TestResolveUserScopedPathAccountAllowsReadablePathOutsideHome(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test uses Unix account scope semantics")
	}
	root := t.TempDir()
	home := filepath.Join(root, "home")
	serviceData := filepath.Join(root, "service-data")
	if err := os.Mkdir(home, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(serviceData, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HFL_INSTALLATION_MODE", "account")
	t.Setenv("HFL_RUN_AS_HOME", home)

	resolved, err := ResolveUserScopedPath(serviceData, false)
	if err != nil || resolved != serviceData {
		t.Fatalf("account path resolved to %q, err=%v", resolved, err)
	}
}

func TestResolveUserScopedPathRejectsOutsideAndEscapingSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test uses Unix symlink semantics")
	}
	root := t.TempDir()
	home := filepath.Join(root, "home")
	outside := filepath.Join(root, "outside")
	if err := os.Mkdir(home, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(outside, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)

	if _, err := ResolveUserScopedPath(outside, false); !errors.Is(err, ErrOutsideUserHome) || !errors.Is(err, os.ErrPermission) {
		t.Fatalf("outside path should report the Home boundary as a permission error: %v", err)
	}
	link := filepath.Join(home, "outside-link")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}
	for _, request := range []struct {
		path         string
		allowMissing bool
	}{
		{link, false},
		{filepath.Join(link, "restore.txt"), true},
	} {
		if _, err := ResolveUserScopedPath(request.path, request.allowMissing); !errors.Is(err, ErrOutsideUserHome) {
			t.Fatalf("symlink escaping Home should report the Home boundary: %v", err)
		}
	}
}

func TestResolveUserScopedPathAllowsSymlinkedHome(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test uses Unix symlink semantics")
	}
	root := t.TempDir()
	canonicalHome := filepath.Join(root, "home-data")
	declaredHome := filepath.Join(root, "home")
	if err := os.Mkdir(canonicalHome, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(canonicalHome, declaredHome); err != nil {
		t.Fatal(err)
	}
	documents := filepath.Join(canonicalHome, "Documents")
	if err := os.Mkdir(documents, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", declaredHome)

	resolved, err := ResolveUserScopedPath(filepath.Join(declaredHome, "Documents"), false)
	if err != nil {
		t.Fatal(err)
	}
	if resolved != documents {
		t.Fatalf("symlinked Home path resolved to %q, want %q", resolved, documents)
	}
}
