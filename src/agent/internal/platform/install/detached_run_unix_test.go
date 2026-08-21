//go:build !windows

package install

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestShellSingleQuote(t *testing.T) {
	got := shellSingleQuote("/var/lib/hyperfilelens-agent/lifecycle/upgrade/run-upgrade.sh")
	want := "'/var/lib/hyperfilelens-agent/lifecycle/upgrade/run-upgrade.sh'"
	if got != want {
		t.Fatalf("shellSingleQuote() = %q, want %q", got, want)
	}
	got = shellSingleQuote("/tmp/it's fine.sh")
	want = "'/tmp/it'\\''s fine.sh'"
	if got != want {
		t.Fatalf("shellSingleQuote(escaped) = %q, want %q", got, want)
	}
}

func TestSystemdRunArgsAreCentOS7Compatible(t *testing.T) {
	args := systemdRunArgs("hfl-agent-upgrade-123", "/tmp/run-upgrade.sh", false)
	if slices.Contains(args, "--collect") {
		t.Fatal("systemd-run arguments must not use --collect; CentOS 7 systemd 219 does not support it")
	}
	if slices.Contains(args, "--property=Type=oneshot") {
		t.Fatal("systemd-run arguments must not set Type=oneshot; CentOS 7 systemd-run rejects it")
	}
	want := []string{
		"--unit=hfl-agent-upgrade-123",
		"--property=KillMode=process",
		"/bin/bash", "/tmp/run-upgrade.sh",
	}
	if !slices.Equal(args, want) {
		t.Fatalf("systemdRunArgs() = %q, want %q", args, want)
	}
}

func TestSystemdRunArgsUseUserManagerForUserInstall(t *testing.T) {
	args := systemdRunArgs(
		"hfl-agent-uninstall-123",
		"/tmp/run-uninstall.sh",
		true,
	)
	want := []string{
		"--user",
		"--unit=hfl-agent-uninstall-123",
		"--property=KillMode=process",
		"/bin/bash", "/tmp/run-uninstall.sh",
	}
	if !slices.Equal(args, want) {
		t.Fatalf("systemdRunArgs(user) = %q, want %q", args, want)
	}
}

func TestUnixUserUpgradeScriptUsesUserLifecycleForRecovery(t *testing.T) {
	dir := t.TempDir()
	scriptPath := filepath.Join(dir, "run-upgrade.sh")
	err := writeUnixUpgradeScript(
		filepath.Join(dir, "install"),
		filepath.Join(dir, "package.tar.gz"),
		filepath.Join(dir, "logs"),
		true,
		scriptPath,
	)
	if err != nil {
		t.Fatalf("writeUnixUpgradeScript: %v", err)
	}
	body, err := os.ReadFile(scriptPath)
	if err != nil {
		t.Fatal(err)
	}
	text := string(body)
	for _, expected := range []string{
		"USER_INSTALL=1",
		`systemctl --user "$@"`,
		"hfl_systemctl start hyperfilelens-agent.service",
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("user upgrade script missing %q", expected)
		}
	}
	if out, err := exec.Command("bash", "-n", scriptPath).CombinedOutput(); err != nil {
		t.Fatalf("generated user upgrade script is not valid bash: %v\n%s", err, out)
	}
}
