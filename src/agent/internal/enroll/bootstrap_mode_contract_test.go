package enroll

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestBootstrapAutomaticModeUsesActualExecutionIdentity(t *testing.T) {
	tests := map[string][]string{
		"agent-bootstrap-linux.sh": {
			`if [[ "${HFL_INSTALLATION_MODE}" == "auto" ]]; then`,
			`export HFL_INSTALLATION_MODE="user_continuous"`,
			`export HFL_INSTALLATION_MODE="system"`,
			"Execution identity resolved",
		},
		"agent-bootstrap-macos.sh": {
			`if [[ "${HFL_INSTALLATION_MODE}" == "auto" ]]; then`,
			`export HFL_INSTALLATION_MODE="user"`,
			`export HFL_INSTALLATION_MODE="system"`,
			"Execution identity resolved",
		},
		"agent-bootstrap-windows.ps1": {
			`HFL_INSTALLATION_MODE -eq "auto"`,
			`HFL_INSTALLATION_MODE = "user"`,
			`HFL_INSTALLATION_MODE = "system"`,
			"Execution identity resolved",
		},
	}
	for name, required := range tests {
		t.Run(name, func(t *testing.T) {
			source := readBootstrapSource(t, name)
			for _, want := range required {
				if !strings.Contains(source, want) {
					t.Fatalf("%s missing automatic-mode contract %q", name, want)
				}
			}
		})
	}
}

func TestLinuxBootstrapExplainsSudoBeforeUserContinuousAuthorization(t *testing.T) {
	source := readBootstrapSource(t, "agent-bootstrap-linux.sh")
	previous := -1
	for _, want := range []string{
		`if [[ "${HFL_INSTALLATION_MODE}" == "user_continuous" && "${HFL_USER_LINGER}" != "yes" ]]; then`,
		`hfl_step "Administrator authorization via sudo is needed to keep the Agent running after you sign out."`,
		`hfl_step "This does not make the Agent run as root or expand the files it can back up. The Agent will still use the current user's permissions to read files."`,
		`sudo loginctl enable-linger "$(id -un)"`,
		`[[ "${HFL_USER_LINGER}" == "yes" ]]`,
		`hfl_ok "The Agent can now continue running after you sign out, using the current user's permissions."`,
	} {
		index := strings.Index(source, want)
		if index < 0 {
			t.Fatalf("Linux bootstrap missing sudo explanation contract %q", want)
		}
		if index <= previous {
			t.Fatalf("Linux bootstrap sudo explanation is out of order: %q", want)
		}
		previous = index
	}
	if strings.Contains(source, `hfl_step "Enabling systemd user lingering`) {
		t.Fatal("Linux bootstrap must explain the authorization instead of only naming systemd lingering")
	}
}

func readBootstrapSource(t *testing.T, name string) string {
	t.Helper()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve test source path")
	}
	path := filepath.Join(
		filepath.Dir(currentFile),
		"..", "..", "..", "..",
		"deploy", "bootstrap", name,
	)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}
