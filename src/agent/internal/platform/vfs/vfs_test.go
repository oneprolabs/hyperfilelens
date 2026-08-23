package vfs

import (
	"path/filepath"
	"runtime"
	"testing"
)

func TestDefaultAgentDataDir(t *testing.T) {
	t.Setenv("HFL_DATA_DIR", "")
	t.Setenv("HFL_AGENT_ROOT", "")
	t.Setenv("HFL_AGENT_HOME", "")
	t.Setenv("HFL_INSTALLATION_MODE", "")
	got := DefaultAgentDataDir()
	switch runtime.GOOS {
	case "windows":
		if got == "" {
			t.Fatal("expected non-empty Windows default")
		}
	default:
		if got != "/opt/hyperfilelens-agent" {
			t.Fatalf("DefaultAgentDataDir() = %q, want /opt/hyperfilelens-agent", got)
		}
	}
}

func TestUserLevelLinuxLayout(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux layout assertion")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_DATA_HOME", "")
	t.Setenv("HFL_INSTALLATION_MODE", "user")

	if got := DefaultInstallDir(); got != filepath.Join(home, ".local", "share", UnixProductSlug, "bin") {
		t.Fatalf("DefaultInstallDir() = %q", got)
	}
	if got := DefaultAgentDataDir(); got != filepath.Join(home, ".local", "share", UnixProductSlug) {
		t.Fatalf("DefaultAgentDataDir() = %q", got)
	}
}

func TestUserLevelLinuxLayoutHonorsXDGDataHome(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux layout assertion")
	}
	dataHome := t.TempDir()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_DATA_HOME", dataHome)
	t.Setenv("HFL_INSTALLATION_MODE", "user")

	if got := DefaultAgentDataDir(); got != filepath.Join(dataHome, UnixProductSlug) {
		t.Fatalf("DefaultAgentDataDir() = %q", got)
	}
}

func TestSpecifiedUserContinuousUsesMachineLifecycleLayout(t *testing.T) {
	t.Setenv("HFL_INSTALLATION_MODE", "account")
	if UserInstallation() {
		t.Fatal("specified-user continuous mode must not use the current-user lifecycle")
	}
	if got := DefaultInstallDir(); got != SystemInstallDir() {
		t.Fatalf("DefaultInstallDir() = %q, want machine path %q", got, SystemInstallDir())
	}
	if got := DefaultAgentDataDir(); got != SystemDataDir() {
		t.Fatalf("DefaultAgentDataDir() = %q, want machine path %q", got, SystemDataDir())
	}
}

func TestAgentDataDirMatchesDefault(t *testing.T) {
	t.Setenv("HFL_DATA_DIR", "")
	t.Setenv("HFL_AGENT_ROOT", "")
	t.Setenv("HFL_AGENT_HOME", "")
	t.Setenv("HFL_INSTALLATION_MODE", "")
	if AgentDataDir("/opt/hyperfilelens-agent/hfl-agent") != DefaultAgentDataDir() {
		t.Fatal("AgentDataDir should match DefaultAgentDataDir")
	}
}

func TestDefaultAgentDataDirUsesExplicitRoot(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Agent")
	t.Setenv("HFL_DATA_DIR", "")
	t.Setenv("HFL_AGENT_HOME", "")
	t.Setenv("HFL_AGENT_ROOT", root)
	if got := DefaultAgentDataDir(); got != filepath.Clean(root) {
		t.Fatalf("DefaultAgentDataDir() = %q, want %q", got, filepath.Clean(root))
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

func TestUnifiedAgentRootSiblings(t *testing.T) {
	root := "/opt/hyperfilelens-agent"
	if got := AgentConfigDir(root); got != root+"/config" {
		t.Fatalf("config dir = %q", got)
	}
	if got := AgentDataStoreDir(root); got != root+"/data" {
		t.Fatalf("data dir = %q", got)
	}
	if got := AgentLogDir(root); got != root+"/logs" {
		t.Fatalf("log dir = %q", got)
	}
	if got := AgentDatabasePath(root); got != root+"/data/agent.db" {
		t.Fatalf("database path = %q", got)
	}
	if got := AgentManifestPath(root); got != root+"/MANIFEST.json" {
		t.Fatalf("manifest path = %q", got)
	}
}
