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
	if !strings.Contains(body, "the next install will create a new console record") {
		t.Fatal("install.sh does not explain the new-record uninstall behavior")
	}
	if !strings.Contains(body, "--keep-installation-identity") {
		t.Fatal("install.sh missing incomplete-install rollback flag")
	}
	if !strings.Contains(body, `[[ "${KEEP_INSTALLATION_IDENTITY}" -eq 0 ]] || return 0`) {
		t.Fatal("install.sh must skip identity retirement during incomplete-install rollback")
	}
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
