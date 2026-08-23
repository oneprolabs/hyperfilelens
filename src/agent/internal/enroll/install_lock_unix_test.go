//go:build linux

package enroll

import (
	"path/filepath"
	"testing"
)

func TestInstallLockPathUsesUserWritableStateDirectory(t *testing.T) {
	dataHome := t.TempDir()
	t.Setenv("HFL_INSTALLATION_MODE", "user")
	t.Setenv("XDG_DATA_HOME", dataHome)

	path, err := installLockPath()
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(dataHome, "hyperfilelens-agent", "lifecycle", "install.lock")
	if path != want {
		t.Fatalf("install lock path = %q, want %q", path, want)
	}
}

func TestInstallLockPathKeepsSystemLockForSystemMode(t *testing.T) {
	t.Setenv("HFL_INSTALLATION_MODE", "system")

	path, err := installLockPath()
	if err != nil {
		t.Fatal(err)
	}
	if path != systemInstallLockPath {
		t.Fatalf("install lock path = %q, want %q", path, systemInstallLockPath)
	}
}
