//go:build !windows

package install

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestInstallShellRetiresIdentityBeforeRemovingAgent(t *testing.T) {
	t.Parallel()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve current test file")
	}
	path := filepath.Clean(filepath.Join(
		filepath.Dir(currentFile),
		"..", "..", "..", "packaging", "install", "install.sh",
	))
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		t.Skipf("packaging source is not available beside the compiled test: %s", path)
	}
	if err != nil {
		t.Fatal(err)
	}
	body := string(raw)
	retire := `retire_installation_identity "${resolved_data}"`
	remove := `remove_install_file "${INSTALL_DIR}/hfl-agent"`
	if !strings.Contains(body, retire) {
		t.Fatalf("install.sh does not retire installation identity: %s", retire)
	}
	if strings.Index(body, retire) > strings.Index(body, remove) {
		t.Fatal("install.sh removes hfl-agent before retiring installation identity")
	}
	if !strings.Contains(body, "remove the old console record before reinstalling or changing run mode") {
		t.Fatal("install.sh does not explain the retired installation identity")
	}
	if !strings.Contains(body, "--keep-installation-identity") {
		t.Fatal("install.sh missing incomplete-install rollback flag")
	}
	if !strings.Contains(body, `[[ "${KEEP_INSTALLATION_IDENTITY}" -eq 0 ]] || return 0`) {
		t.Fatal("install.sh must skip identity retirement during incomplete-install rollback")
	}
}

func TestInstallShellDefinesUserLifecycleForLinuxAndMacOS(t *testing.T) {
	body := readPackagingInstallShell(t)
	for _, want := range []string{
		`UNIT_DST="${USER_CONFIG_HOME}/systemd/user/hyperfilelens-agent.service"`,
		`LAUNCHD_PLIST="${HOME}/Library/LaunchAgents/com.hyperfilelens.agent.plist"`,
		`LAUNCHD_DOMAIN="gui/$(id -u)"`,
		`systemctl --user`,
		`loginctl is required to verify that current-user mode stops after sign-out.`,
		`WantedBy=default.target`,
		`Description=HyperFileLens Agent (Current User)`,
		`EnvironmentFile="${unit_env_file}"`,
		`WorkingDirectory="${unit_working_dir}"`,
		`ExecStart="${unit_agent}" run`,
		`systemd_escape_unit_value`,
		`An active macOS user session is required for user-level installation.`,
		`systemd user lingering is enabled. Disable lingering or choose System Service mode.`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh missing user lifecycle rule %q", want)
		}
	}
}

func TestInstallShellEnforcesUserModeBoundary(t *testing.T) {
	body := readPackagingInstallShell(t)
	for _, want := range []string{
		`User-level installation must run as the current user without sudo.`,
		`User-level installation is only available for Source Agent.`,
		`User-level installation uses the fixed data directory ${DEFAULT_DATA}; --data-dir is not supported.`,
		`HFL_INSTALLATION_MODE=${INSTALLATION_MODE}`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh missing user-mode boundary %q", want)
		}
	}
}

func readPackagingInstallShell(t *testing.T) string {
	t.Helper()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve current test file")
	}
	path := filepath.Join(filepath.Dir(currentFile), "..", "..", "..", "packaging", "install", "install.sh")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func TestInstallShellDoesNotRemoveInstallParent(t *testing.T) {
	t.Parallel()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve current test file")
	}
	path := filepath.Clean(filepath.Join(
		filepath.Dir(currentFile),
		"..", "..", "..", "packaging", "install", "install.sh",
	))
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		t.Skipf("packaging source is not available beside the compiled test: %s", path)
	}
	if err != nil {
		t.Fatal(err)
	}
	body := string(raw)
	if strings.Contains(body, `rmdir "$(dirname "$INSTALL_DIR")"`) {
		t.Fatal("install.sh must not remove the parent of the Agent install directory")
	}
}

func TestGatewayHooksPreferLongLivedNodeCredential(t *testing.T) {
	t.Parallel()
	for name, hook := range map[string]string{
		"upgrade":   unixGatewaySidecarUpgradeHook,
		"uninstall": unixGatewaySidecarUninstallHook,
	} {
		t.Run(name, func(t *testing.T) {
			credential := strings.Index(hook, "^HFL_NODE_CREDENTIAL=")
			legacy := strings.Index(hook, "^HFL_NODE_TOKEN=")
			if credential < 0 || legacy < 0 || credential > legacy {
				t.Fatalf("gateway hook does not prefer node credential: %s", hook)
			}
		})
	}
}
