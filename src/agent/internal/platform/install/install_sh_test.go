//go:build !windows

package install

import (
	"os"
	"os/exec"
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
	if !strings.Contains(body, "the existing console record is preserved and the next installation will register a new record") {
		t.Fatal("install.sh does not explain that local uninstall preserves the console record")
	}
	if strings.Contains(body, "remove the old console record") {
		t.Fatal("install.sh must not require local uninstall to change the console record")
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
		`EnvironmentFile=${unit_env_file}`,
		`WorkingDirectory=${unit_working_dir}`,
		`ExecStart="${unit_agent}" run`,
		`systemd_escape_unit_value`,
		`An active macOS user session is required for user-level installation.`,
		`systemd user lingering is enabled. Disable lingering or choose Host files continuous protection.`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh missing user lifecycle rule %q", want)
		}
	}
}

func TestUserSystemdUnitTemplateIsValid(t *testing.T) {
	if _, err := exec.LookPath("systemd-analyze"); err != nil {
		t.Skip("systemd-analyze is not available")
	}

	body := readPackagingInstallShell(t)
	functionStart := strings.Index(body, "install_systemd_unit() {")
	if functionStart < 0 {
		t.Fatal("install.sh missing install_systemd_unit")
	}
	body = body[functionStart:]
	heredocStart := strings.Index(body, `cat >"${UNIT_DST}" <<EOF`+"\n")
	if heredocStart < 0 {
		t.Fatal("install.sh missing user systemd unit template")
	}
	template := body[heredocStart+len(`cat >"${UNIT_DST}" <<EOF`)+1:]
	heredocEnd := strings.Index(template, "\nEOF")
	if heredocEnd < 0 {
		t.Fatal("install.sh user systemd unit template is not terminated")
	}
	template = template[:heredocEnd]

	workingDir := filepath.Join(t.TempDir(), "agent home")
	if err := os.MkdirAll(workingDir, 0o755); err != nil {
		t.Fatal(err)
	}
	envFile := filepath.Join(workingDir, "agent.env")
	if err := os.WriteFile(envFile, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	unit := strings.NewReplacer(
		"${unit_env_file}", envFile,
		"${unit_working_dir}", workingDir,
		"${unit_agent}", "/bin/true",
	).Replace(template)
	unitPath := filepath.Join(t.TempDir(), "hyperfilelens-agent.service")
	if err := os.WriteFile(unitPath, []byte(unit), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("systemd-analyze", "verify", unitPath).CombinedOutput(); err != nil {
		t.Fatalf("invalid user systemd unit:\n%s\n%s", unit, output)
	}
}

func TestInstallShellEnforcesUserModeBoundary(t *testing.T) {
	body := readPackagingInstallShell(t)
	for _, want := range []string{
		`User-level installation must run as the current user without sudo.`,
		`User-scoped installation is only available for Source Agent.`,
		`User-level installation uses the fixed data directory ${DEFAULT_DATA}; --data-dir is not supported.`,
		`set_agent_env_key "${env_file}" HFL_INSTALLATION_MODE "${INSTALLATION_MODE}"`,
		`set_agent_env_key "${env_file}" HFL_AGENT_ROOT "${AGENT_ROOT}"`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh missing user-mode boundary %q", want)
		}
	}
}

func TestInstallShellDetectsUserModeFromCustomXDGDataHome(t *testing.T) {
	body := readPackagingInstallShell(t)
	for _, want := range []string{
		`USER_DATA_HOME="${XDG_DATA_HOME:-}"`,
		`"${USER_DATA_HOME}/hyperfilelens-agent/bin"`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh must detect custom XDG user root using %q", want)
		}
	}
}

func TestInstallShellSupportsSpecifiedUserContinuousMode(t *testing.T) {
	body := readPackagingInstallShell(t)
	for _, want := range []string{
		`INSTALLATION_MODE}" == "account"`,
		`if [[ -t 0 ]]; then`,
		`RUN_AS_USER="${RUN_AS_USER:-${default_run_as_user}}"`,
		`sed -i "/^\[Service\]/a User=${RUN_AS_USER}"`,
		`<key>UserName</key>`,
		`set_agent_env_key "${env_file}" HFL_RUN_AS_USER "${RUN_AS_USER}"`,
		`PERSISTED_ENV="/opt/hyperfilelens-agent/config/agent.env"`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh missing specified-user continuous contract %q", want)
		}
	}
}

func TestInstallShellPreservesLegacyEnvironmentDuringRootMigration(t *testing.T) {
	body := readPackagingInstallShell(t)
	for _, want := range []string{
		`LEGACY_ENV_SOURCE="${LEGACY_DATA_DIR}/agent.env"`,
		`if [[ -f "${LEGACY_DATA_DIR}/agent.db" && ! -f "$(agent_data_store_dir "${DEFAULT_DATA}")/agent.db" ]]; then`,
		`cp -p "${LEGACY_ENV_SOURCE}" "${env_file}"`,
		`env_value_for_file`,
		`if [[ "\${value:0:1}" == '"' && "\${value: -1}" == '"' ]]`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh migration environment handling missing %q", want)
		}
	}
}

func TestInstallShellDoesNotArchiveTheNewRootIntoItself(t *testing.T) {
	body := readPackagingInstallShell(t)
	for _, want := range []string{
		`if [[ "${LEGACY_INSTALL_DIR}" != "${AGENT_ROOT}" ]]; then`,
		`copy_legacy_entry "${LEGACY_INSTALL_DIR}/backup" "${legacy_program}/backup"`,
		`copy_legacy_entry "${LEGACY_DATA_DIR}/backup/state"`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh legacy migration missing recursion guard %q", want)
		}
	}
}

func TestInstallShellCleansPartiallyMigratedSystemRootAfterHealthCheck(t *testing.T) {
	body := readPackagingInstallShell(t)
	for _, want := range []string{
		`A unified Agent Root may already contain the new bin/ tree`,
		`elif is_installed && legacy_layout_present; then`,
		`migrate_legacy_layout 1`,
		`archive-only mode prevents stale legacy state from overwriting valid data`,
		`HFL_LEGACY_MIGRATION_DIR=%s`,
		`reusing legacy Agent archive at`,
		`tasks list --data-dir`,
		`legacy layout retained because the new Agent database could not be opened`,
		`reconcile-legacy) cmd_reconcile_legacy`,
		`migrate_legacy_layout 1 0`,
		`previous release's`,
		`DATA_DIR="${data_dir}"`,
		`cleanup_upgrade_workspace "${upgrade_ws:-}"`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("install.sh missing partial-migration safety contract %q", want)
		}
	}
}

func TestInstallShellKeepsSpecifiedUserOutOfUserManager(t *testing.T) {
	body := readPackagingInstallShell(t)
	start := strings.Index(body, "hfl_systemctl() {")
	if start < 0 {
		t.Fatal("install.sh missing hfl_systemctl helper")
	}
	end := strings.Index(body[start:], "\n}\n")
	if end < 0 {
		t.Fatal("install.sh hfl_systemctl helper has no end")
	}
	helper := body[start : start+end]
	if strings.Contains(helper, `INSTALLATION_MODE}" == "account"`) {
		t.Fatal("specified-user continuous mode must not select systemctl --user")
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
	if !strings.Contains(unixGatewaySidecarUpgradeHook, `agent_root="${DATA_DIR:-}"`) {
		t.Fatal("gateway upgrade hook must resolve the persisted Agent Root")
	}
	if !strings.Contains(unixGatewaySidecarUpgradeHook, `agent_root="${INSTALL_SH%/bin/install.sh}"`) {
		t.Fatal("detached gateway upgrade hook must derive Agent Root from INSTALL_SH")
	}
}
