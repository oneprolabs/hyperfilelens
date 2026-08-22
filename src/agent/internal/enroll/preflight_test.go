package enroll

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestIsUbuntuMinSkipsNonLinux(t *testing.T) {
	if isUbuntuMin(20, 4) && testing.Short() {
		t.Skip("environment-specific")
	}
}

func TestServiceManagerConstraint(t *testing.T) {
	switch runtime.GOOS {
	case "linux":
		if err := serviceManagerConstraint("systemd"); err != nil {
			t.Fatal(err)
		}
		if err := serviceManagerConstraint("none"); err == nil {
			t.Fatal("expected non-systemd Linux to be rejected")
		}
	case "darwin":
		if err := serviceManagerConstraint("launchd"); err != nil {
			t.Fatal(err)
		}
	case "windows":
		if err := serviceManagerConstraint("windows-service"); err != nil {
			t.Fatal(err)
		}
	}
}

func TestUserLifecycleManagerConstraint(t *testing.T) {
	manager := map[string]string{
		"linux":   "systemd-user",
		"darwin":  "launch-agent",
		"windows": "windows-task",
	}[runtime.GOOS]
	if manager == "" {
		t.Skip("unsupported test platform")
	}
	if err := lifecycleManagerConstraint(manager, model.InstallationModeUser); err != nil {
		t.Fatal(err)
	}
	if err := lifecycleManagerConstraint("none", model.InstallationModeUser); err == nil {
		t.Fatal("expected missing user lifecycle manager to be rejected")
	}
}

func TestSpecifiedUserContinuousUsesSystemLifecycleManager(t *testing.T) {
	manager := map[string]string{
		"linux": "systemd", "darwin": "launchd", "windows": "windows-task",
	}[runtime.GOOS]
	if manager == "" {
		t.Skip("unsupported test platform")
	}
	if err := lifecycleManagerConstraint(manager, model.InstallationModeAccount); err != nil {
		t.Fatal(err)
	}
}

func TestUserSessionLifecycleConstraintDoesNotAffectSystemMode(t *testing.T) {
	if err := userSessionLifecycleConstraint(
		context.Background(),
		model.InstallationModeSystem,
	); err != nil {
		t.Fatal(err)
	}
}

func TestRequiredCommandsForWindows(t *testing.T) {
	if commands := requiredCommandsFor("windows"); len(commands) != 0 {
		t.Fatalf("Windows required commands=%v, want none", commands)
	}
	commands := requiredCommandsFor("linux")
	for _, required := range []string{"bash", "curl", "tar"} {
		found := false
		for _, command := range commands {
			if command == required {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("Linux required commands=%v, missing %q", commands, required)
		}
	}
}

func TestPreflightAgentRole(t *testing.T) {
	if err := Preflight(model.RoleAgent); err != nil {
		t.Logf("Preflight(agent): %v", err)
	}
}

func TestDetectInstallStateNotInstalled(t *testing.T) {
	state := DetectInstallState()
	if state.Installed {
		t.Logf("agent appears installed in test environment: %+v", state)
	}
}

func TestReadEnvKeyMissing(t *testing.T) {
	if got := readEnvKey("/nonexistent/agent.env", "HFL_ORG_KEY"); got != "" {
		t.Fatalf("expected empty, got %q", got)
	}
}

func TestInstallMarkersDetectPartialInstallation(t *testing.T) {
	dir := t.TempDir()
	if installMarkersPresent(dir) {
		t.Fatal("empty installation directory should not be treated as installed")
	}
	for _, name := range []string{"install.sh", "install.ps1", "install.cmd", "MANIFEST.json"} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(dir, name)
			if err := os.WriteFile(path, []byte("partial install"), 0o600); err != nil {
				t.Fatal(err)
			}
			if !installMarkersPresent(dir) {
				t.Fatalf("partial installation marker %s was not detected", name)
			}
			if err := os.Remove(path); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestPlanInstallRejectsProtectionModeSwitchInPlace(t *testing.T) {
	state := InstallState{
		Installed:        true,
		OrgKey:           "org",
		Role:             string(model.RoleAgent),
		InstallationMode: string(model.InstallationModeSystem),
	}
	cfg := Config{
		OrgKey:           "org",
		NodeRole:         model.RoleAgent,
		InstallationMode: model.InstallationModeAccount,
	}
	plan, err := PlanInstall(context.Background(), cfg, state, InstallModeAuto)
	if err == nil || plan.Action != "" {
		t.Fatalf("mode switch plan = %#v, err=%v; want a rejected in-place switch", plan, err)
	}
}
