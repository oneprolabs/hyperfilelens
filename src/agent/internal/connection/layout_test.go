package connection

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

func runtimeLayoutFixture(t *testing.T) (string, string, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HFL_INSTALLATION_MODE", "user")
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("XDG_DATA_HOME", t.TempDir())
	t.Setenv("LOCALAPPDATA", filepath.Join(home, "AppData", "Local"))
	root, err := vfs.UserDataDir()
	if err != nil {
		t.Fatal(err)
	}
	exe := filepath.Join(root, "bin", "hfl-agent")
	script := filepath.Join(root, "bin", "install.sh")
	if runtime.GOOS == "windows" {
		exe += ".exe"
		script = filepath.Join(root, "bin", "install.ps1")
	}
	if err := os.MkdirAll(filepath.Dir(exe), 0700); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{exe, script} {
		if err := os.WriteFile(path, []byte("layout fixture; never executed"), 0700); err != nil {
			t.Fatal(err)
		}
	}
	return root, exe, script
}

func TestInstalledRuntimePathsAcceptUnifiedRoot(t *testing.T) {
	root, exe, script := runtimeLayoutFixture(t)
	resolved, installer, err := installedRuntimePaths(root, exe)
	want, resolveErr := filepath.EvalSymlinks(exe)
	if err != nil || resolveErr != nil || resolved != want || installer != script {
		t.Fatal("valid unified installation was rejected", err, resolveErr)
	}
}

func TestInstalledRuntimePathsRejectUnverifiableLayouts(t *testing.T) {
	for _, kind := range []string{"missing-binary", "missing-script", "binary-directory", "script-directory", "split-root", "downloaded-binary"} {
		t.Run(kind, func(t *testing.T) {
			root, exe, script := runtimeLayoutFixture(t)
			current := exe
			switch kind {
			case "missing-binary", "binary-directory":
				if err := os.Remove(exe); err != nil {
					t.Fatal(err)
				}
				if kind == "binary-directory" {
					if err := os.Mkdir(exe, 0700); err != nil {
						t.Fatal(err)
					}
				}
			case "missing-script", "script-directory":
				if err := os.Remove(script); err != nil {
					t.Fatal(err)
				}
				if kind == "script-directory" {
					if err := os.Mkdir(script, 0700); err != nil {
						t.Fatal(err)
					}
				}
			case "split-root":
				// Even a duplicate bin/ tree in a custom data root is not
				// evidence that this is the installed runtime.
				other := filepath.Join(t.TempDir(), "bin")
				if err := os.Mkdir(other, 0700); err != nil {
					t.Fatal(err)
				}
				for _, path := range []string{exe, script} {
					if err := os.WriteFile(filepath.Join(other, filepath.Base(path)), []byte("duplicate"), 0700); err != nil {
						t.Fatal(err)
					}
				}
				root = filepath.Dir(other)
			case "downloaded-binary":
				current = filepath.Join(t.TempDir(), filepath.Base(exe))
				if err := os.WriteFile(current, []byte("downloaded copy"), 0700); err != nil {
					t.Fatal(err)
				}
			}
			if _, _, err := installedRuntimePaths(root, current); !errors.Is(err, errRuntimeLayout) {
				t.Fatal("unverifiable layout was accepted", err)
			}
		})
	}
}

func TestUnverifiableRuntimeCannotBeReportedStoppedOrModifyFiles(t *testing.T) {
	root, _, _ := fixture(t, model.RoleAgent)
	before := captureFiles(t, root, false)
	if running, err := agentRunning(root); running || !errors.Is(err, errRuntimeLayout) {
		t.Fatal("unknown installation was reported as safely stopped", running, err)
	}
	if err := agentService(context.Background(), root, "stop"); !errors.Is(err, errRuntimeLayout) {
		t.Fatal("unknown installation reached lifecycle execution", err)
	}
	if err := Configure(context.Background(), "https://new.invalid"); !errors.Is(err, errRuntimeLayout) {
		t.Fatal("unknown installation reached configuration maintenance", err)
	}
	assertFilesUnchanged(t, before)
	for _, path := range []string{statePath(root), configureLockPath(root), filepath.Join(root, "backup")} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatal("rejected layout created maintenance state", path, err)
		}
	}
}
