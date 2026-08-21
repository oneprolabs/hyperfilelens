//go:build linux

package enroll

import (
	"path/filepath"
	"testing"
)

func TestInstallLockPathUsesUserWritableStateDirectory(t *testing.T) {
	stateHome := t.TempDir()
	t.Setenv("HFL_INSTALLATION_MODE", "user")
	t.Setenv("XDG_STATE_HOME", stateHome)

	path, err := installLockPath()
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(stateHome, "hyperfilelens-agent", "install.lock")
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
