//go:build linux

package connection

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"

	"hyperfilelens/agent/internal/model"
)

func TestAccountRecoveryChild(t *testing.T) {
	root := os.Getenv("HFL_TEST_ACCOUNT_RECOVERY_ROOT")
	if root == "" {
		t.Skip("account-mode subprocess only")
	}
	err := Recover(root)
	if os.Getenv("HFL_TEST_EXPECT_RECOVERY_ERROR") == "1" {
		if err == nil {
			t.Fatal("unreadable recovery metadata was ignored")
		}
	} else if err != nil {
		t.Fatal(err)
	}
}

func TestAccountRecoveryHonorsRuntimeOwnershipAndRetainsFailures(t *testing.T) {
	if os.Getuid() != 0 {
		t.Skip("requires a root parent to exercise the ordinary-account boundary")
	}
	for _, shared := range []bool{true, false} {
		t.Run(map[bool]string{true: "owned", false: "unreadable"}[shared], func(t *testing.T) {
			root, _, _ := fixture(t, model.RoleAgent)
			for _, dir := range []string{root, filepath.Dir(root)} {
				if err := os.Chmod(dir, 0755); err != nil {
					t.Fatal(err)
				}
			}
			uid, gid := 65534, 65534
			for _, dir := range []string{"config", "lifecycle"} {
				path := filepath.Join(root, dir)
				if err := os.MkdirAll(path, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.Chown(path, uid, gid); err != nil {
					t.Fatal(err)
				}
			}
			for _, path := range configPaths(root, false) {
				if err := os.Chown(path, uid, gid); err != nil {
					t.Fatal(err)
				}
			}
			files := captureFiles(t, root, false)
			lock := configureLockPath(root)
			if err := os.Mkdir(lock, 0700); err != nil {
				t.Fatal(err)
			}
			for name, value := range map[string]string{"pid": "2147483646", "operation": "configure"} {
				if err := os.WriteFile(filepath.Join(lock, name), []byte(value), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if shared {
				if err := shareLockOwnership(root, lock); err != nil {
					t.Fatal(err)
				}
			}
			if err := save(statePath(root), transaction{PID: 2147483646, Born: 1, Phase: "applying", Files: files}); err != nil {
				t.Fatal(err)
			}
			exe, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			binary, err := os.ReadFile(exe)
			if err != nil {
				t.Fatal(err)
			}
			helper := filepath.Join(root, "recovery-test")
			if err := os.WriteFile(helper, binary, 0755); err != nil {
				t.Fatal(err)
			}
			cmd := exec.Command(helper, "-test.run=^TestAccountRecoveryChild$")
			expect := "0"
			if !shared {
				expect = "1"
			}
			cmd.Env = append(os.Environ(), "HFL_TEST_ACCOUNT_RECOVERY_ROOT="+root, "HFL_INSTALLATION_MODE=account", "HFL_TEST_EXPECT_RECOVERY_ERROR="+expect)
			cmd.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: uint32(uid), Gid: uint32(gid)}}
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("ordinary-account recovery: %v: %s", err, out)
			}
			for _, path := range []string{lock, statePath(root)} {
				_, err := os.Stat(path)
				if shared && !os.IsNotExist(err) || !shared && err != nil {
					t.Fatal("recovery lost state or left an owned lock", path, err)
				}
			}
			assertFilesUnchanged(t, files)
		})
	}
}
