package install

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStageUpgradeInstallerUsesValidatedBundleScript(t *testing.T) {
	dataDir := t.TempDir()
	bundleRoot := filepath.Join(t.TempDir(), "hfl-agent-0.2.14-darwin-arm64")
	if err := os.MkdirAll(bundleRoot, 0o750); err != nil {
		t.Fatal(err)
	}
	src := filepath.Join(bundleRoot, "install.sh")
	if err := os.WriteFile(src, []byte("#!/bin/sh\necho new-installer\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	dest, err := StageUpgradeInstaller(dataDir, bundleRoot)
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(LifecycleUpgradeDir(dataDir), "installer", "install.sh"); dest != want {
		t.Fatalf("destination = %q, want %q", dest, want)
	}
	body, err := os.ReadFile(dest)
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != "#!/bin/sh\necho new-installer\n" {
		t.Fatalf("staged installer content = %q", body)
	}
}

func TestStagedPackageFilename(t *testing.T) {
	tests := map[string]string{
		"/tmp/hfl-agent-bundle-1/package.tar.gz": "package.tar.gz",
		"/tmp/package.tar.gz":                    "package.tar.gz",
		"C:\\pending\\package.zip":               "package.zip",
		"/tmp/package.zip":                       "package.zip",
	}
	for input, want := range tests {
		if got := stagedPackageFilename(input); got != want {
			t.Fatalf("stagedPackageFilename(%q) = %q, want %q", input, got, want)
		}
	}
}
