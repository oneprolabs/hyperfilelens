//go:build !windows

package install

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

func TestWriteUnixUninstallScriptIncludesLogFile(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/run-uninstall.sh"
	err := writeUnixUninstallScript(
		"/opt/hyperfilelens-agent",
		"/var/lib/hyperfilelens-agent",
		"/var/lib/hyperfilelens-agent/logs",
		false,
		false,
		UninstallCompletion{
			APIBaseURL: "https://control.example",
			Path:       "/api/v1/node/agent-uninstall/completion/",
			Token:      "signed-test-token",
		},
		path,
	)
	if err != nil {
		t.Fatalf("writeUnixUninstallScript: %v", err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read script: %v", err)
	}
	body := string(raw)
	if !strings.Contains(body, "/var/lib/hyperfilelens-agent/logs/uninstall.log") {
		t.Fatalf("script missing uninstall log path:\n%s", body)
	}
	if !strings.Contains(body, `log "detached uninstall script started`) {
		t.Fatalf("script missing start log line:\n%s", body)
	}
	if !strings.Contains(body, `log "detached uninstall script finished"`) {
		t.Fatalf("script missing finish log line:\n%s", body)
	}
	if !strings.Contains(body, `removed install directory tree $INSTALL_DIR (including backup artifacts)`) {
		t.Fatalf("script should remove install dir tree including backup:\n%s", body)
	}
	if !strings.Contains(body, `script="$INSTALL_DIR/libexec/gateway-lifecycle.sh"`) {
		t.Fatalf("script should prefer the Agent-owned Gateway lifecycle helper:\n%s", body)
	}
	if !strings.Contains(body, `local env_file="$DATA_DIR/config/agent.env"`) {
		t.Fatalf("script should read Gateway credentials from the resolved data directory:\n%s", body)
	}
	if !strings.Contains(body, `removed gateway resource policy $RESOURCE_DROPIN`) {
		t.Fatalf("script should remove the Data Gateway systemd resource policy:\n%s", body)
	}
	if !strings.Contains(body, `gateway sidecar uninstall failed; keeping the Agent installed for retry`) {
		t.Fatalf("script should fail closed when LensNode removal fails:\n%s", body)
	}
	for _, want := range []string{
		`gateway_sidecar_uninstall_failed`,
		`"lensnode_sidecar"`,
		`managed_mount_cleanup_failed`,
		`"managed_nas_mounts"`,
		`agent_uninstall_failed`,
		`"agent_installation"`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("script should report structured cleanup residue %q:\n%s", want, body)
		}
	}
	if strings.Contains(body, `gateway sidecar uninstall reported errors; continuing agent uninstall`) {
		t.Fatalf("script must not remove the Agent after LensNode removal fails:\n%s", body)
	}
	if !strings.Contains(body, `unmount_agent_mounts "$DATA_DIR"`) {
		t.Fatalf("script must unmount Agent-managed NAS shares:\n%s", body)
	}
	if !strings.Contains(body, `for attempt in 1 2 3 4 5 6`) {
		t.Fatalf("script must retry the signed completion callback:\n%s", body)
	}
	if !strings.Contains(body, `rm -f -- "$0"`) {
		t.Fatalf("script must remove its callback-token runner after completion:\n%s", body)
	}
	if !strings.Contains(body, `verify_uninstall_artifacts`) ||
		!strings.Contains(body, `post-uninstall verification failed; Strict Cleanup remains retryable`) {
		t.Fatalf("script must verify service, files, and data before reporting success:\n%s", body)
	}
	if !strings.Contains(body, `Agent-managed NAS mount cleanup failed; preserving Agent files and data for manual retry`) {
		t.Fatalf("script must stop removal when managed mounts remain:\n%s", body)
	}
	if !strings.Contains(body, `config retire-installation --data-dir "$DATA_DIR"`) {
		t.Fatalf("script must retire installation identity when data is preserved:\n%s", body)
	}
	if !strings.Contains(body, "the existing console record is preserved and the next installation will register a new record") {
		t.Fatalf("script must preserve the console record during local uninstall:\n%s", body)
	}
	if strings.Contains(body, "remove the old console record") {
		t.Fatalf("script must not require local uninstall to change the console record:\n%s", body)
	}
	unmountAt := strings.Index(body, `unmount_agent_mounts "$DATA_DIR"`)
	stopAt := strings.Index(body, `hfl_systemctl stop "$SERVICE_NAME"`)
	retireAt := strings.Index(body, `config retire-installation --data-dir "$DATA_DIR"`)
	removeAt := strings.Index(body, `for target in "$INSTALL_DIR/hfl-agent"`)
	if unmountAt < 0 || stopAt < 0 || retireAt < 0 || removeAt < 0 ||
		unmountAt > stopAt || stopAt > retireAt || retireAt > removeAt {
		t.Fatalf("script must unmount managed shares before stopping and removing the Agent:\n%s", body)
	}
	if !strings.Contains(body, `report_uninstall_completion "$rc"`) {
		t.Fatalf("script must report the signed completion result:\n%s", body)
	}
	if !strings.Contains(body, `CALLBACK_TOKEN="signed-test-token"`) {
		t.Fatalf("script must embed the one-time completion token:\n%s", body)
	}
	if !strings.Contains(body, `if [[ -e "$DATA_DIR" ]]; then
            log "data directory $DATA_DIR remains after removal"
            AGENT_ARTIFACTS_FAILED=1
            exit 1`) {
		t.Fatalf("script must verify data directory removal:\n%s", body)
	}
	if out, err := exec.Command("bash", "-n", path).CombinedOutput(); err != nil {
		t.Fatalf("generated uninstall script is not valid bash: %v\n%s", err, out)
	}
}

func TestWriteUnixUserUninstallScriptUsesUserLifecycle(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", "")
	t.Setenv("XDG_DATA_HOME", "")
	path := home + "/run-uninstall.sh"
	err := writeUnixUninstallScript(
		filepath.Join(home, ".local", "share", "hyperfilelens-agent", "bin"),
		filepath.Join(home, ".local", "share", "hyperfilelens-agent", "state"),
		filepath.Join(home, ".local", "share", "hyperfilelens-agent", "state", "logs"),
		false,
		true,
		UninstallCompletion{
			APIBaseURL: "https://control.example",
			Path:       "/api/v1/node/agent-uninstall/completion/",
			Token:      "signed-test-token",
		},
		path,
	)
	if err != nil {
		t.Fatalf("writeUnixUninstallScript: %v", err)
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(body)
	for _, expected := range []string{
		"USER_INSTALL=1",
		"systemctl --user",
		filepath.Join(home, ".config", "systemd", "user", unixServiceUnit),
		fmt.Sprintf("USER_INSTALL_ROOT=%q", filepath.Join(home, ".local", "share", "hyperfilelens-agent", "bin")),
		`is_managed_install_path()`,
		`"$path" == "$USER_INSTALL_ROOT"`,
		`is_managed_data_path()`,
		`"$path" == "$DEFAULT_DATA_ROOT"`,
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("user uninstall script missing %q", expected)
		}
	}
	for _, unexpected := range []string{
		`"$path" == "$USER_HOME"/*`,
		`"${path##*/}" == "hyperfilelens-agent"`,
	} {
		if strings.Contains(text, unexpected) {
			t.Fatalf("user uninstall script must not trust broad path rule %q", unexpected)
		}
	}
	if out, err := exec.Command("bash", "-n", path).CombinedOutput(); err != nil {
		t.Fatalf("generated user uninstall script is not valid bash: %v\n%s", err, out)
	}
}

func TestWriteUnixUserUninstallScriptDoesNotTrustConfiguredExternalDataDir(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_DATA_HOME", "")
	externalData := filepath.Join(t.TempDir(), "hyperfilelens-agent")
	path := filepath.Join(home, "run-uninstall.sh")
	err := writeUnixUninstallScript(
		filepath.Join(home, ".local", "share", "hyperfilelens-agent", "bin"),
		externalData,
		filepath.Join(externalData, "logs"),
		false,
		true,
		UninstallCompletion{
			APIBaseURL: "https://control.example",
			Path:       "/api/v1/node/agent-uninstall/completion/",
			Token:      "signed-test-token",
		},
		path,
	)
	if err != nil {
		t.Fatalf("writeUnixUninstallScript: %v", err)
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(body)
	trustedDefault := filepath.Join(home, ".local", "share", "hyperfilelens-agent")
	if !strings.Contains(text, fmt.Sprintf("DEFAULT_DATA_ROOT=%q", trustedDefault)) {
		t.Fatalf("script must use trusted default data root %q", trustedDefault)
	}
	if strings.Contains(text, fmt.Sprintf("DEFAULT_DATA_ROOT=%q", externalData)) {
		t.Fatalf("configured external data directory became a trusted deletion root")
	}
}

func TestValidateUnixUninstallPaths(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_DATA_HOME", "")

	systemInstall := filepath.Clean(vfs.InstallDirForMode(model.InstallationModeSystem))
	systemData := filepath.Clean(vfs.AgentDataDirForMode(model.InstallationModeSystem))
	userInstall := filepath.Clean(vfs.InstallDirForMode(model.InstallationModeUser))
	userData := filepath.Clean(vfs.AgentDataDirForMode(model.InstallationModeUser))

	tests := []struct {
		name        string
		installDir  string
		dataDir     string
		keepData    bool
		userInstall bool
		wantErr     bool
	}{
		{
			name:       "system defaults with purge",
			installDir: systemInstall,
			dataDir:    systemData,
		},
		{
			name:       "system managed child with purge",
			installDir: systemInstall,
			dataDir:    filepath.Join(systemData, "custom-state"),
		},
		{
			name:        "user defaults with purge",
			installDir:  userInstall,
			dataDir:     userData,
			userInstall: true,
		},
		{
			name:       "relative traversal",
			installDir: filepath.Join(systemInstall, "..", "..", "..", "etc"),
			dataDir:    systemData,
			wantErr:    true,
		},
		{
			name:        "arbitrary user home data purge",
			installDir:  userInstall,
			dataDir:     filepath.Join(home, "Documents"),
			userInstall: true,
			wantErr:     true,
		},
		{
			name:       "custom data retained",
			installDir: systemInstall,
			dataDir:    filepath.Join(t.TempDir(), "custom-data"),
			keepData:   true,
		},
		{
			name:       "external system data purge",
			installDir: systemInstall,
			dataDir:    filepath.Join(t.TempDir(), "custom-data"),
			wantErr:    true,
		},
		{
			name:       "unexpected install directory",
			installDir: filepath.Join(t.TempDir(), "hyperfilelens-agent"),
			dataDir:    systemData,
			wantErr:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateUnixUninstallPaths(
				filepath.Clean(tt.installDir),
				filepath.Clean(tt.dataDir),
				tt.keepData,
				tt.userInstall,
			)
			if (err != nil) != tt.wantErr {
				t.Fatalf("validateUnixUninstallPaths() error = %v, wantErr %t", err, tt.wantErr)
			}
		})
	}
}

func TestCanonicalRemovalPathResolvesIntermediateSymlinkOnly(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(root, "outside")
	inside := filepath.Join(root, "inside")
	if err := os.Mkdir(outside, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(inside, 0o700); err != nil {
		t.Fatal(err)
	}
	intermediate := filepath.Join(inside, "link")
	if err := os.Symlink(outside, intermediate); err != nil {
		t.Fatal(err)
	}

	resolved, err := canonicalRemovalPath(filepath.Join(intermediate, "data"))
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(outside, "data"); resolved != want {
		t.Fatalf("intermediate symlink resolved to %q, want %q", resolved, want)
	}

	finalLink := filepath.Join(inside, "final-link")
	if err := os.Symlink(outside, finalLink); err != nil {
		t.Fatal(err)
	}
	resolved, err = canonicalRemovalPath(finalLink)
	if err != nil {
		t.Fatal(err)
	}
	if resolved != finalLink {
		t.Fatalf("final symlink resolved to %q, want link path %q", resolved, finalLink)
	}
}
