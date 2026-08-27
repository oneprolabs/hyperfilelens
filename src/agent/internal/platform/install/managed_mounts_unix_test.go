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

func TestUnixManagedMountCleanupUnmountsDeepestFirst(t *testing.T) {
	if strings.Contains(unixManagedMountCleanupScript, "mapfile -t") ||
		strings.Contains(unixManagedMountCleanupScript, "readarray -t") {
		t.Fatal("managed mount cleanup must remain compatible with macOS Bash 3.2")
	}
	result := runUnixManagedMountCleanupTest(t, `
last="${@: -1}"
printf '%s\n' "$*" >>"$HFL_TEST_UMOUNT_LOG"
grep -Fvx -- "$last" "$HFL_TEST_MOUNT_STATE" >"$HFL_TEST_MOUNT_STATE.tmp"
mv "$HFL_TEST_MOUNT_STATE.tmp" "$HFL_TEST_MOUNT_STATE"
`)
	if result.err != nil {
		t.Fatalf("cleanup failed: %v", result.err)
	}
	if strings.TrimSpace(result.mounts) != "/mnt/unrelated" {
		t.Fatalf("cleanup touched an unrelated mount or left a managed mount: %q", result.mounts)
	}
	nestedAt := strings.Index(result.umountLog, "/nested")
	parentAt := strings.LastIndex(result.umountLog, "/repo-3-node-7")
	if nestedAt < 0 || parentAt < 0 || nestedAt > parentAt {
		t.Fatalf("managed mounts were not unmounted deepest-first: %q", result.umountLog)
	}
}

func TestUnixManagedMountCleanupLazyUnmountsBusyMount(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("lazy unmount is a Linux-only fallback")
	}
	result := runUnixManagedMountCleanupTest(t, `
last="${@: -1}"
printf '%s\n' "$*" >>"$HFL_TEST_UMOUNT_LOG"
if [[ "${1:-}" != "-l" ]]; then
  exit 1
fi
grep -Fvx -- "$last" "$HFL_TEST_MOUNT_STATE" >"$HFL_TEST_MOUNT_STATE.tmp"
mv "$HFL_TEST_MOUNT_STATE.tmp" "$HFL_TEST_MOUNT_STATE"
`)
	if result.err != nil {
		t.Fatalf("cleanup failed: %v", result.err)
	}
	if !strings.Contains(result.umountLog, "-l ") {
		t.Fatalf("cleanup did not use lazy unmount for a busy mount: %q", result.umountLog)
	}
	if strings.TrimSpace(result.mounts) != "/mnt/unrelated" {
		t.Fatalf("cleanup left a managed mount: %q", result.mounts)
	}
}

func TestUnixManagedMountCleanupFailsWhenMountRemains(t *testing.T) {
	result := runUnixManagedMountCleanupTest(t, `
printf '%s\n' "$*" >>"$HFL_TEST_UMOUNT_LOG"
exit 1
`)
	if result.err == nil {
		t.Fatal("cleanup unexpectedly succeeded while managed mounts remained")
	}
	if !strings.Contains(result.mounts, "/repo-3-node-7") {
		t.Fatalf("failure fixture lost the managed mount unexpectedly: %q", result.mounts)
	}
}

type unixManagedMountCleanupResult struct {
	err       error
	mounts    string
	umountLog string
}

func runUnixManagedMountCleanupTest(t *testing.T, umountBody string) unixManagedMountCleanupResult {
	t.Helper()
	dir := t.TempDir()
	binDir := filepath.Join(dir, "bin")
	dataDir := filepath.Join(dir, "data")
	mountsRoot := filepath.Join(dataDir, "mounts")
	statePath := filepath.Join(dir, "mounts.txt")
	umountLogPath := filepath.Join(dir, "umount.log")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(mountsRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	parent := filepath.Join(mountsRoot, "repositories", "repo-3-node-7")
	nested := filepath.Join(parent, "nested")
	state := strings.Join([]string{parent, nested, "/mnt/unrelated", ""}, "\n")
	if err := os.WriteFile(statePath, []byte(state), 0o600); err != nil {
		t.Fatal(err)
	}
	writeExecutable(t, filepath.Join(binDir, "findmnt"), `#!/usr/bin/env bash
cat "$HFL_TEST_MOUNT_STATE"
`)
	writeExecutable(t, filepath.Join(binDir, "umount"), "#!/usr/bin/env bash\n"+umountBody)
	scriptPath := filepath.Join(dir, "cleanup.sh")
	script := `#!/usr/bin/env bash
set -u
log() { :; }
` + unixManagedMountCleanupScript + `
unmount_agent_mounts "$HFL_TEST_DATA_DIR"
`
	writeExecutable(t, scriptPath, script)
	cmd := exec.Command("bash", scriptPath)
	cmd.Env = append(os.Environ(),
		"PATH="+binDir+":/usr/bin:/bin",
		"HFL_TEST_DATA_DIR="+dataDir,
		"HFL_TEST_MOUNT_STATE="+statePath,
		"HFL_TEST_UMOUNT_LOG="+umountLogPath,
	)
	err := cmd.Run()
	mounts, readErr := os.ReadFile(statePath)
	if readErr != nil {
		t.Fatal(readErr)
	}
	umountLog, readErr := os.ReadFile(umountLogPath)
	if readErr != nil && !os.IsNotExist(readErr) {
		t.Fatal(readErr)
	}
	return unixManagedMountCleanupResult{err: err, mounts: string(mounts), umountLog: string(umountLog)}
}

func writeExecutable(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
}
