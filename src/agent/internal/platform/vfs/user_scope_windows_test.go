//go:build windows

package vfs

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/windows"
)

func TestResolveUserScopedPathWindowsAllowsReadableFixedDrivePaths(t *testing.T) {
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

	resolved, err = ResolveUserScopedPath(outside, false)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.EqualFold(filepath.Clean(resolved), filepath.Clean(outside)) {
		t.Fatalf("outside-Home path resolved to %q, want %q", resolved, outside)
	}

	for _, path := range []string{`\\server\share`, `\\server\share\folder`, `\\?\UNC\server\share\folder`} {
		if _, err := ResolveUserScopedPath(path, false); !errors.Is(err, ErrLocalFixedDriveRequired) || !errors.Is(err, os.ErrPermission) {
			t.Fatalf("UNC path %q must report the drive policy as a permission error: %v", path, err)
		}
	}
}

func TestRequireFixedDriveTypeDistinguishesPolicyFromReadPermissions(t *testing.T) {
	for _, driveType := range []uint32{
		windows.DRIVE_UNKNOWN,
		windows.DRIVE_NO_ROOT_DIR,
		windows.DRIVE_REMOVABLE,
		windows.DRIVE_REMOTE,
		windows.DRIVE_CDROM,
		windows.DRIVE_RAMDISK,
	} {
		err := requireFixedDriveType(driveType)
		if !errors.Is(err, ErrLocalFixedDriveRequired) || !errors.Is(err, os.ErrPermission) {
			t.Fatalf("drive type %d must report the drive policy: %v", driveType, err)
		}
	}
	if err := requireFixedDriveType(windows.DRIVE_FIXED); err != nil {
		t.Fatalf("a fixed drive must remain allowed: %v", err)
	}
}
