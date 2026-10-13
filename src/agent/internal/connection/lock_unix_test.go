//go:build linux || darwin

package connection

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/enroll"
	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

func userFixture(t *testing.T, mode string) (string, *serverInfo, runtimeHooks) {
	t.Helper()
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_DATA_HOME", t.TempDir())
	root, err := vfs.UserDataDir()
	if err != nil {
		t.Fatal(err)
	}
	root, target, ops := fixtureAtRoot(t, model.RoleAgent, root)
	t.Setenv("HFL_INSTALLATION_MODE", mode)
	path := filepath.Join(root, "config", "agent.env")
	values, err := config.ParseEnvFile(path)
	if err != nil {
		t.Fatal(err)
	}
	values["HFL_INSTALLATION_MODE"] = mode
	if err := config.WriteEnvFile(path, values); err != nil {
		t.Fatal(err)
	}
	return root, target, ops
}

func TestUserConfigurePreservesEnrollmentLockAndMutualExclusion(t *testing.T) {
	for _, mode := range []string{"user", "user_continuous"} {
		t.Run(mode, func(t *testing.T) {
			root, target, ops := userFixture(t, mode)
			server := serve(t, target)
			lock := filepath.Join(root, "lifecycle", "install.lock")
			// First enrollment releases flock but leaves the ordinary file.
			if err := enroll.WithInstallLock(context.Background(), func() error { return nil }); err != nil {
				t.Fatal(err)
			}
			before, err := os.Stat(lock)
			if err != nil {
				t.Fatal(err)
			}
			checked := false
			ops.agentRunning = func(string) (bool, error) {
				checked = true
				err := enroll.WithInstallLock(context.Background(), func() error {
					t.Fatal("concurrent enrollment was admitted")
					return nil
				})
				if !errors.Is(err, enroll.ErrInstallLocked) {
					t.Fatalf("enrollment lock was not held: %v", err)
				}
				if err := configure(context.Background(), server.URL, ops); err == nil {
					t.Fatal("concurrent configure was admitted")
				}
				return false, nil
			}
			if err := enroll.WithInstallLock(context.Background(), func() error {
				return configure(context.Background(), server.URL, ops)
			}); err != nil {
				t.Fatal(err)
			}
			after, err := os.Stat(lock)
			if err != nil || !os.SameFile(before, after) || !checked {
				t.Fatal("enrollment lock inode was removed/replaced", err)
			}
			if _, err := os.Stat(configureLockPath(root)); !os.IsNotExist(err) {
				t.Fatal("lifecycle directory lock was not released", err)
			}
			if err := enroll.WithInstallLock(context.Background(), func() error { return nil }); err != nil {
				t.Fatal("enrollment lock was not released", err)
			}
		})
	}
}

func TestUserRecoveryRemovesOnlyOwnedLifecycleDirectory(t *testing.T) {
	root, _, _ := userFixture(t, "user")
	enrollment := filepath.Join(root, "lifecycle", "install.lock")
	if err := enroll.WithInstallLock(context.Background(), func() error { return nil }); err != nil {
		t.Fatal(err)
	}
	before, err := os.Stat(enrollment)
	if err != nil {
		t.Fatal(err)
	}
	var files []savedFile
	for _, path := range configPaths(root, false) {
		f, err := snapshot(path)
		if err != nil {
			t.Fatal(err)
		}
		files = append(files, f)
	}
	lock := configureLockPath(root)
	if err := os.Mkdir(lock, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(lock, "pid"), []byte("2147483646"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(lock, "operation"), []byte("configure"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := save(statePath(root), transaction{PID: 2147483646, Born: 1, Phase: "applying", Files: files}); err != nil {
		t.Fatal(err)
	}
	if err := Recover(root); err != nil {
		t.Fatal(err)
	}
	after, err := os.Stat(enrollment)
	if err != nil || !os.SameFile(before, after) {
		t.Fatal("recovery removed enrollment lock inode", err)
	}
	if _, err := os.Stat(lock); !os.IsNotExist(err) {
		t.Fatal("recovery did not release lifecycle directory", err)
	}
}
