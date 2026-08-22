//go:build linux

package enroll

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestEnsureUserSystemdUnitRepairsStaleDefinition(t *testing.T) {
	home := t.TempDir()
	configHome := filepath.Join(home, "config home")
	dataHome := filepath.Join(home, "data home")
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", configHome)
	t.Setenv("XDG_DATA_HOME", dataHome)
	t.Setenv("HFL_INSTALLATION_MODE", "user")
	installDir := filepath.Join(dataHome, "hyperfilelens-agent", "bin")
	dataDir := filepath.Join(dataHome, "hyperfilelens-agent")
	if err := os.MkdirAll(installDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(dataDir, "config"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(installDir, "hfl-agent"), []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dataDir, "config", "agent.env"), nil, 0o600); err != nil {
		t.Fatal(err)
	}

	unitPath := filepath.Join(configHome, "systemd", "user", userSystemdUnitName)
	if err := os.MkdirAll(filepath.Dir(unitPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(unitPath, []byte("invalid stale unit\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ensureUserSystemdUnit(); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(unitPath)
	if err != nil {
		t.Fatal(err)
	}
	unit := string(raw)
	for _, want := range []string{
		"EnvironmentFile=" + filepath.Join(dataDir, "config", "agent.env"),
		"WorkingDirectory=" + installDir,
		`ExecStart="` + filepath.Join(installDir, "hfl-agent") + `" run`,
		"WantedBy=default.target",
	} {
		if !strings.Contains(unit, want) {
			t.Fatalf("repaired unit missing %q:\n%s", want, unit)
		}
	}
	if strings.Contains(unit, `EnvironmentFile="`) || strings.Contains(unit, `WorkingDirectory="`) {
		t.Fatalf("single-path systemd directives must not retain stale quotes:\n%s", unit)
	}
	if mode := fileMode(t, unitPath); mode != 0o644 {
		t.Fatalf("unit mode = %o, want 644", mode)
	}

	if _, err := exec.LookPath("systemd-analyze"); err == nil {
		if output, verifyErr := exec.Command("systemd-analyze", "verify", unitPath).CombinedOutput(); verifyErr != nil {
			t.Fatalf("repaired unit is invalid: %v\n%s\n%s", verifyErr, unit, output)
		}
	}
}

func TestEnsureUserSystemdUnitDoesNotTouchSystemInstall(t *testing.T) {
	home := t.TempDir()
	configHome := filepath.Join(home, ".config")
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", configHome)
	t.Setenv("HFL_INSTALLATION_MODE", "system")

	if err := ensureUserSystemdUnit(); err != nil {
		t.Fatal(err)
	}
	unitPath := filepath.Join(configHome, "systemd", "user", userSystemdUnitName)
	if _, err := os.Stat(unitPath); !os.IsNotExist(err) {
		t.Fatalf("system install unexpectedly wrote user unit: %v", err)
	}
}

func TestRepairUnitMatchesBundleInstallerTemplate(t *testing.T) {
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve test source path")
	}
	installScript := filepath.Join(
		filepath.Dir(currentFile),
		"..", "..", "packaging", "install", "install.sh",
	)
	raw, err := os.ReadFile(installScript)
	if err != nil {
		t.Fatal(err)
	}
	body := string(raw)
	functionStart := strings.Index(body, "install_systemd_unit() {")
	if functionStart < 0 {
		t.Fatal("install.sh missing install_systemd_unit")
	}
	body = body[functionStart:]
	marker := `cat >"${UNIT_DST}" <<EOF` + "\n"
	heredocStart := strings.Index(body, marker)
	if heredocStart < 0 {
		t.Fatal("install.sh missing user unit template")
	}
	template := body[heredocStart+len(marker):]
	heredocEnd := strings.Index(template, "\nEOF")
	if heredocEnd < 0 {
		t.Fatal("install.sh user unit template is not terminated")
	}
	template = template[:heredocEnd]

	installDir := "/home/alice/.local/share/hyperfilelens-agent/bin"
	dataDir := "/home/alice/.local/share/hyperfilelens-agent"
	want := strings.NewReplacer(
		"${unit_env_file}", filepath.Join(dataDir, "config", "agent.env"),
		"${unit_working_dir}", installDir,
		"${unit_agent}", filepath.Join(installDir, "hfl-agent"),
	).Replace(template) + "\n"
	got, err := renderUserSystemdUnit(installDir, dataDir)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("repair unit drifted from install.sh template\n--- got ---\n%s--- want ---\n%s", got, want)
	}
}

func fileMode(t *testing.T, path string) os.FileMode {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode().Perm()
}
