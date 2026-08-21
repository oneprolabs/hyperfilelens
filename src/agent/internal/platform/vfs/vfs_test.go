package vfs

import (
	"path/filepath"
	"runtime"
	"testing"
)

func TestDefaultAgentDataDir(t *testing.T) {
	got := DefaultAgentDataDir()
	switch runtime.GOOS {
	case "windows":
		if got == "" {
			t.Fatal("expected non-empty Windows default")
		}
	default:
		if got != "/var/lib/hyperfilelens-agent" {
			t.Fatalf("DefaultAgentDataDir() = %q, want /var/lib/hyperfilelens-agent", got)
		}
	}
}

func TestUserLevelLinuxLayout(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux layout assertion")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_STATE_HOME", "")
	t.Setenv("HFL_INSTALLATION_MODE", "user")

	if got := DefaultInstallDir(); got != filepath.Join(home, ".local", "lib", UnixProductSlug) {
		t.Fatalf("DefaultInstallDir() = %q", got)
	}
	if got := DefaultAgentDataDir(); got != filepath.Join(home, ".local", "state", UnixProductSlug) {
		t.Fatalf("DefaultAgentDataDir() = %q", got)
	}
}

func TestUserLevelLinuxLayoutHonorsXDGStateHome(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux layout assertion")
	}
	stateHome := t.TempDir()
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", stateHome)
	t.Setenv("HFL_INSTALLATION_MODE", "user")

	if got := DefaultAgentDataDir(); got != filepath.Join(stateHome, UnixProductSlug) {
		t.Fatalf("DefaultAgentDataDir() = %q", got)
	}
}

func TestAgentDataDirMatchesDefault(t *testing.T) {
	if AgentDataDir("/opt/hyperfilelens-agent/hfl-agent") != DefaultAgentDataDir() {
		t.Fatal("AgentDataDir should match DefaultAgentDataDir")
	}
}

func TestUnixPaths(t *testing.T) {
	if UnixInstallDir() != "/opt/hyperfilelens-agent" {
		t.Fatalf("UnixInstallDir() = %q", UnixInstallDir())
	}
	if UnixDataDir() != "/var/lib/hyperfilelens-agent" {
		t.Fatalf("UnixDataDir() = %q", UnixDataDir())
	}
}
