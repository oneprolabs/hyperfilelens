//go:build !windows

package install

import (
	"encoding/xml"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestDarwinUserLaunchdJobPreservesUserDomainAndPermissions(t *testing.T) {
	job := newDarwinLaunchdJob(true, 501, "/Users/ghw", 123)
	if job.domain != "gui/501" {
		t.Fatalf("domain = %q, want gui/501", job.domain)
	}
	wantPath := "/Users/ghw/Library/LaunchAgents/com.hyperfilelens.lifecycle-123.plist"
	if job.plistPath != wantPath {
		t.Fatalf("plistPath = %q, want %q", job.plistPath, wantPath)
	}
	if job.dirMode != 0o700 || job.fileMode != 0o600 {
		t.Fatalf("user modes = %o/%o, want 700/600", job.dirMode, job.fileMode)
	}
	wantArgs := []string{"bootstrap", "gui/501", wantPath}
	if args := darwinLaunchctlBootstrapArgs(job); !slices.Equal(args, wantArgs) {
		t.Fatalf("launchctl args = %q, want %q", args, wantArgs)
	}
}

func TestDarwinSystemLaunchdJobUsesSystemDomain(t *testing.T) {
	job := newDarwinLaunchdJob(false, 501, "/Users/ignored", 456)
	if job.domain != "system" {
		t.Fatalf("domain = %q, want system", job.domain)
	}
	if job.plistPath != "/Library/LaunchDaemons/com.hyperfilelens.lifecycle-456.plist" {
		t.Fatalf("unexpected plistPath: %q", job.plistPath)
	}
	if job.dirMode != 0o755 || job.fileMode != 0o644 {
		t.Fatalf("system modes = %o/%o, want 755/644", job.dirMode, job.fileMode)
	}
}

func TestDarwinLaunchdPlistIsValidAndDoesNotChangeUser(t *testing.T) {
	job := newDarwinLaunchdJob(true, 501, "/Users/ghw", 123)
	plist := darwinLaunchdPlist(job, "/tmp/upgrade & uninstall's runner.sh")
	decoder := xml.NewDecoder(strings.NewReader(plist))
	for {
		if _, err := decoder.Token(); err != nil {
			if err == io.EOF {
				break
			}
			t.Fatalf("invalid plist XML: %v\n%s", err, plist)
		}
	}
	for _, expected := range []string{
		"<string>com.hyperfilelens.lifecycle-123</string>",
		"launchctl bootout 'gui/501/com.hyperfilelens.lifecycle-123'",
		"rm -f '/Users/ghw/Library/LaunchAgents/com.hyperfilelens.lifecycle-123.plist'",
		"/tmp/upgrade &amp; uninstall'\\''s runner.sh",
	} {
		if !strings.Contains(plist, expected) {
			t.Fatalf("launchd plist missing %q:\n%s", expected, plist)
		}
	}
	if strings.Contains(plist, "<key>UserName</key>") {
		t.Fatal("user lifecycle job must inherit its gui domain identity, not override UserName")
	}
}

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
		filepath.Join(dir, "package.tar.gz"),
		filepath.Join(dir, "installer", "install.sh"),
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
