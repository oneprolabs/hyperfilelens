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
