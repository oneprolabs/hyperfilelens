package nas

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/platform/vfs"
)

func unmountTestService(
	mounted *bool,
	unmount func(context.Context, string) error,
	lazy func(context.Context, string) error,
) *Service {
	return &Service{
		isMountedFn:      func(string) bool { return *mounted },
		hasUnmountWorkFn: func(string) bool { return *mounted },
		unmountFn:        unmount,
		lazyUnmountFn:    lazy,
		removeMountDirFn: func(string) error {
			return nil
		},
		retryWaitFn: func(context.Context, time.Duration) error { return nil },
	}
}

func managedTestMountPoint(t *testing.T, leaf string) string {
	t.Helper()
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	return filepath.Join(vfs.MountSourcesDir(dataDir), leaf)
}

func TestUnmountRemovesEmptyManagedMountDirectory(t *testing.T) {
	mountPoint := managedTestMountPoint(t, "12")
	if err := os.MkdirAll(mountPoint, 0o755); err != nil {
		t.Fatal(err)
	}
	service := NewService()
	if err := service.Unmount(context.Background(), mountPoint); err != nil {
		t.Fatalf("Unmount() error = %v", err)
	}
	if _, err := os.Stat(mountPoint); !os.IsNotExist(err) {
		t.Fatalf("mount directory still exists: %v", err)
	}
	if err := service.Unmount(context.Background(), mountPoint); err != nil {
		t.Fatalf("idempotent Unmount() error = %v", err)
	}
}

func TestUnmountPreservesNonEmptyManagedMountDirectory(t *testing.T) {
	mountPoint := managedTestMountPoint(t, "13")
	if err := os.MkdirAll(mountPoint, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(mountPoint, "retained"), []byte("data"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := NewService().Unmount(context.Background(), mountPoint)
	if err == nil || !strings.Contains(err.Error(), "cleanup mount directory") {
		t.Fatalf("Unmount() error = %v", err)
	}
	if _, statErr := os.Stat(mountPoint); statErr != nil {
		t.Fatalf("non-empty mount directory was removed: %v", statErr)
	}
}

func TestUnmountRejectsManagedSymlink(t *testing.T) {
	mountPoint := managedTestMountPoint(t, "14")
	target := t.TempDir()
	if err := os.MkdirAll(filepath.Dir(mountPoint), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, mountPoint); err != nil {
		t.Fatal(err)
	}
	err := NewService().Unmount(context.Background(), mountPoint)
	if err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("Unmount() error = %v", err)
	}
	if _, statErr := os.Stat(target); statErr != nil {
		t.Fatalf("symlink target was affected: %v", statErr)
	}
}

func TestParseNetUseRemote(t *testing.T) {
	tests := []struct {
		name string
		text string
		want string
		ok   bool
	}{
		{
			name: "ordinary share",
			text: "Remote name       \\\\server\\backup\nStatus            OK\n",
			want: `\\server\backup`,
			ok:   true,
		},
		{
			name: "share name containing spaces",
			text: "Local name        Z:\r\nRemote name       \\\\server\\Finance Backup\r\nResource type     Disk\r\nStatus            OK\r\n",
			want: `\\server\Finance Backup`,
			ok:   true,
		},
		{
			name: "no UNC path",
			text: "There are no entries in the list.\r\n",
			want: "",
			ok:   false,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, ok := parseNetUseRemote(test.text)
			if got != test.want || ok != test.ok {
				t.Fatalf("parseNetUseRemote() = (%q, %t), want (%q, %t)", got, ok, test.want, test.ok)
			}
		})
	}
}

func TestUnmountRetriesTransientBusyMount(t *testing.T) {
	mounted := true
	attempts := 0
	service := unmountTestService(
		&mounted,
		func(context.Context, string) error {
			attempts++
			if attempts < 3 {
				return errors.New("device is busy")
			}
			mounted = false
			return nil
		},
		func(context.Context, string) error { return errors.New("unexpected lazy unmount") },
	)

	result, err := service.UnmountWithOptions(
		context.Background(),
		managedTestMountPoint(t, "15"),
		UnmountOptions{},
	)

	if err != nil {
		t.Fatalf("UnmountWithOptions() error = %v", err)
	}
	if result.Attempts != 3 || result.LazyUnmount || !result.CleanupComplete {
		t.Fatalf("unexpected result: %#v", result)
	}
}

func TestForceUnmountLazilyDetachesPersistentBusyMount(t *testing.T) {
	mounted := true
	lazyCalls := 0
	service := unmountTestService(
		&mounted,
		func(context.Context, string) error { return errors.New("device is busy") },
		func(context.Context, string) error {
			lazyCalls++
			mounted = false
			return nil
		},
	)

	result, err := service.UnmountWithOptions(
		context.Background(),
		managedTestMountPoint(t, "16"),
		UnmountOptions{Force: true},
	)

	if err != nil {
		t.Fatalf("UnmountWithOptions() error = %v", err)
	}
	if lazyCalls != 1 || !result.LazyUnmount || result.CleanupComplete {
		t.Fatalf("unexpected result: %#v lazy_calls=%d", result, lazyCalls)
	}
	if len(result.RetainedResources) != 1 || len(result.Warnings) != 1 {
		t.Fatalf("force cleanup residue missing: %#v", result)
	}
}

func TestUnmountTrustsDetachedStateWhenCommandReportsError(t *testing.T) {
	mounted := true
	attempts := 0
	service := unmountTestService(
		&mounted,
		func(context.Context, string) error {
			attempts++
			mounted = false
			return errors.New("umount process exited after detaching the mount")
		},
		func(context.Context, string) error { return errors.New("unexpected lazy unmount") },
	)

	result, err := service.UnmountWithOptions(
		context.Background(),
		managedTestMountPoint(t, "18"),
		UnmountOptions{},
	)

	if err != nil {
		t.Fatalf("UnmountWithOptions() error = %v", err)
	}
	if attempts != 1 || result.Attempts != 1 || !result.CleanupComplete {
		t.Fatalf("unexpected result: %#v attempts=%d", result, attempts)
	}
}

func TestUnmountPreservesLocalCleanupErrorAfterDetach(t *testing.T) {
	mounted := true
	service := unmountTestService(
		&mounted,
		func(context.Context, string) error {
			mounted = false
			return localUnmountCleanupError(errors.New("remove SMB junction: access denied"))
		},
		func(context.Context, string) error { return errors.New("unexpected lazy unmount") },
	)

	_, err := service.UnmountWithOptions(
		context.Background(),
		managedTestMountPoint(t, "19"),
		UnmountOptions{},
	)

	if err == nil || !strings.Contains(err.Error(), "remove SMB junction") {
		t.Fatalf("UnmountWithOptions() error = %v", err)
	}
}

func TestUnmountCleansOwnedStateWhenMountIsNotLive(t *testing.T) {
	pending := true
	unmountCalls := 0
	service := &Service{
		isMountedFn:      func(string) bool { return false },
		hasUnmountWorkFn: func(string) bool { return pending },
		unmountFn: func(context.Context, string) error {
			unmountCalls++
			pending = false
			return nil
		},
		removeMountDirFn: func(string) error { return nil },
		retryWaitFn:      func(context.Context, time.Duration) error { return nil },
	}

	result, err := service.UnmountWithOptions(
		context.Background(),
		managedTestMountPoint(t, "20"),
		UnmountOptions{},
	)

	if err != nil {
		t.Fatalf("UnmountWithOptions() error = %v", err)
	}
	if unmountCalls != 1 || result.Attempts != 1 || !result.CleanupComplete {
		t.Fatalf("unexpected result: %#v unmount_calls=%d", result, unmountCalls)
	}
}

func TestCleanupUnmountedMountPointPreservesOwnedCleanupState(t *testing.T) {
	mountPoint := managedTestMountPoint(t, "21")
	if err := os.MkdirAll(mountPoint, 0o755); err != nil {
		t.Fatal(err)
	}
	service := &Service{
		isMountedFn:      func(string) bool { return false },
		hasUnmountWorkFn: func(string) bool { return true },
	}

	removed, err := service.CleanupUnmountedMountPoint(mountPoint)

	if err != nil || removed {
		t.Fatalf("CleanupUnmountedMountPoint() = %v, %v", removed, err)
	}
	if _, err := os.Stat(mountPoint); err != nil {
		t.Fatalf("owned mount state was removed: %v", err)
	}
}

func TestStrictUnmountReturnsPersistentBusyFailure(t *testing.T) {
	mounted := true
	service := unmountTestService(
		&mounted,
		func(context.Context, string) error { return errors.New("device is busy") },
		func(context.Context, string) error { return nil },
	)

	result, err := service.UnmountWithOptions(
		context.Background(),
		managedTestMountPoint(t, "17"),
		UnmountOptions{},
	)

	if err == nil || !strings.Contains(err.Error(), "device is busy") {
		t.Fatalf("UnmountWithOptions() error = %v", err)
	}
	if result.Attempts != unmountAttempts || result.LazyUnmount {
		t.Fatalf("unexpected result: %#v", result)
	}
}

func TestBusyUnmountErrorRecognizesTargetBusy(t *testing.T) {
	if !isBusyUnmountError(errors.New("umount: target is busy")) {
		t.Fatal("target-is-busy error was not recognized")
	}
}
