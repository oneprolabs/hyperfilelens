package install

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestInstallPs1PurgeDoesNotRecreateDataLogDirectory(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, want := range []string{
		"if (-not `$dir -or -not (Test-Path -LiteralPath `$dir)) { return }",
		`$uninstallLog = if (-not $PurgeAll -and $uninstallLogPath) { $uninstallLogPath } else { "" }`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 missing %q", want)
		}
	}
	if strings.Contains(source, "if (`$dir) { New-Item -ItemType Directory -Force -Path `$dir") {
		t.Fatal("deferred install-root cleanup must not recreate the uninstall log directory")
	}
}

func TestInstallPs1DoesNotRemoveInstallParent(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, forbidden := range []string{
		"`$parent = Split-Path -Parent `$target",
		"removed empty parent directory `$parent",
	} {
		if strings.Contains(source, forbidden) {
			t.Fatalf("install.ps1 must not remove the shared install parent: found %q", forbidden)
		}
	}
}

func TestInstallPs1SafeDataPathRequiresHyperFileLensDescendant(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, want := range []string{
		`$allowedRoot = Join-Path $pd "HyperFileLens"`,
		`$allowedRoot.TrimEnd('\') + '\'`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 safe data path check missing %q", want)
		}
	}
	if strings.Contains(source, `StartsWith($pd.TrimEnd('\') + '\HyperFileLens'`) {
		t.Fatal("safe data path check must enforce a path-component boundary")
	}
}

func TestInstallPs1RetiresIdentityBeforeRemovingAgent(t *testing.T) {
	source := readPackagingInstallScript(t)
	retire := `& $agentBinary config retire-installation --data-dir $dataRoot`
	remove := `Remove-HflInstallFile (Join-Path $InstallRoot "hfl-agent.exe")`
	if !strings.Contains(source, retire) {
		t.Fatalf("install.ps1 missing %q", retire)
	}
	if strings.Index(source, retire) > strings.Index(source, remove) {
		t.Fatal("install.ps1 removes hfl-agent before retiring installation identity")
	}
	if !strings.Contains(source, "the next install will create a new console record") {
		t.Fatal("install.ps1 does not explain the new-record uninstall behavior")
	}
	if !strings.Contains(source, "-KeepInstallationIdentity") {
		t.Fatal("install.ps1 missing incomplete-install rollback flag")
	}
	if !strings.Contains(source, `(-not $PurgeAll) -and (-not $KeepInstallationIdentity)`) {
		t.Fatal("install.ps1 must skip identity retirement during incomplete-install rollback")
	}
}

func readPackagingInstallScript(t *testing.T) string {
	t.Helper()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve current test file")
	}
	path := filepath.Clean(filepath.Join(
		filepath.Dir(currentFile),
		"..", "..", "..", "packaging", "install", "install.ps1",
	))
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		t.Skipf("packaging source is not available beside the compiled test: %s", path)
	}
	if err != nil {
		t.Fatalf("read install.ps1: %v", err)
	}
	return string(raw)
}
