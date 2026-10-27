package install

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestUpgradeSpaceUsesAvailableBytesNotPercentage(t *testing.T) {
	root := t.TempDir()
	paths := []UpgradeSpaceCheck{{Scope: "agent_data", Path: root}, {Scope: "agent_install", Path: filepath.Join(root, "not-created", "bin")}}
	for _, free := range []uint64{DefaultUpgradeMinFreeBytes, 52 * 1024 * 1024 * 1024} {
		err := checkUpgradeSpace(paths, DefaultUpgradeMinFreeBytes, func(path string) (uint64, uint64, uint64, error) {
			if path != root {
				t.Fatalf("lookup path = %s", path)
			}
			// Even 99% used is allowed when available bytes meet the reserve.
			return free * 100, free * 99, free, nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
}

func TestUpgradeSpaceFailureKinds(t *testing.T) {
	root := t.TempDir()
	for _, tt := range []struct {
		name    string
		free    uint64
		readErr error
		code    string
	}{
		{"full", 0, nil, UpgradeSpaceInsufficient},
		{"below minimum", DefaultUpgradeMinFreeBytes - 1, nil, UpgradeSpaceInsufficient},
		{"unreadable", 0, os.ErrPermission, UpgradeSpaceUnavailable},
	} {
		t.Run(tt.name, func(t *testing.T) {
			err := checkUpgradeSpace([]UpgradeSpaceCheck{{Scope: "agent_data", Path: root}}, DefaultUpgradeMinFreeBytes, func(string) (uint64, uint64, uint64, error) { return 1024 * 1024 * 1024, 0, tt.free, tt.readErr })
			var failure *UpgradeSpaceError
			if !errors.As(err, &failure) || failure.Code != tt.code {
				t.Fatalf("failure = %v", err)
			}
			if failure.Result()["old_agent_unchanged"] != true || len(failure.Checks) != 1 {
				t.Fatalf("result = %#v", failure.Result())
			}
			if failure.Checks[0].Path != root || failure.Checks[0].RequiredBytes != DefaultUpgradeMinFreeBytes {
				t.Fatal(failure.Checks)
			}
			if tt.readErr == nil && (failure.Checks[0].FreeBytes == nil || *failure.Checks[0].FreeBytes != tt.free) {
				t.Fatal("missing available bytes")
			}
		})
	}
}

func TestUpgradeSpaceChecksEveryTargetIndependently(t *testing.T) {
	root := t.TempDir()
	installDir := filepath.Join(root, "bin")
	if err := os.Mkdir(installDir, 0700); err != nil {
		t.Fatal(err)
	}
	calls := 0
	err := checkUpgradeSpace([]UpgradeSpaceCheck{{Path: root}, {Path: installDir}}, DefaultUpgradeMinFreeBytes, func(path string) (uint64, uint64, uint64, error) {
		calls++
		free := DefaultUpgradeMinFreeBytes
		if path == installDir {
			free--
		}
		return free * 2, 0, free, nil
	})
	var failure *UpgradeSpaceError
	if calls != 2 || !errors.As(err, &failure) || len(failure.Checks) != 1 || failure.Checks[0].Path != installDir {
		t.Fatalf("calls=%d failure=%v", calls, err)
	}
}

func TestUpgradeSpaceRejectsUnknownAndInvalidPaths(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "file")
	if err := os.WriteFile(file, []byte("data"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"", "relative", file} {
		err := checkUpgradeSpace([]UpgradeSpaceCheck{{Path: path}}, DefaultUpgradeMinFreeBytes, func(string) (uint64, uint64, uint64, error) {
			t.Fatal("must not inspect invalid path")
			return 0, 0, 0, nil
		})
		var failure *UpgradeSpaceError
		if !errors.As(err, &failure) || failure.Code != UpgradeSpaceUnavailable {
			t.Fatal(err)
		}
	}
	err := checkUpgradeSpace([]UpgradeSpaceCheck{{Path: root}}, DefaultUpgradeMinFreeBytes, func(string) (uint64, uint64, uint64, error) { return 0, 0, 0, nil })
	var failure *UpgradeSpaceError
	if !errors.As(err, &failure) || failure.Code != UpgradeSpaceUnavailable {
		t.Fatal(err)
	}
}

func TestUpgradeMinimumFreeBytes(t *testing.T) {
	for _, tt := range []struct {
		name, mb, bytes string
		requested, want uint64
		invalid         bool
	}{
		{name: "default", want: DefaultUpgradeMinFreeBytes},
		{name: "floor", mb: "1", bytes: "1", requested: 1, want: DefaultUpgradeMinFreeBytes},
		{name: "local MiB", mb: "1024", want: 1024 * 1024 * 1024},
		{name: "server policy", mb: "512", requested: 2 * 1024 * 1024 * 1024, want: 2 * 1024 * 1024 * 1024},
		{name: "detached policy", bytes: "2147483648", want: 2 * 1024 * 1024 * 1024},
		{name: "invalid", mb: "broken", invalid: true},
		{name: "negative", mb: "-1", invalid: true},
		{name: "overflow", mb: "18446744073709551615", invalid: true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv("HFL_UPGRADE_MIN_FREE_MB", tt.mb)
			t.Setenv("HFL_UPGRADE_MIN_FREE_BYTES", tt.bytes)
			got, err := UpgradeMinimumFreeBytes(tt.requested)
			if (err != nil) != tt.invalid || !tt.invalid && got != tt.want {
				t.Fatalf("got=%d err=%v", got, err)
			}
		})
	}
}

func TestUpgradeSpaceFailureRecordMustBelongToCurrentAttempt(t *testing.T) {
	root := t.TempDir()
	now := time.Now().UTC()
	failure := &UpgradeSpaceError{Code: UpgradeSpaceInsufficient, CollectedAt: now, Checks: []UpgradeSpaceCheck{{Scope: "agent_data", Path: root, RequiredBytes: DefaultUpgradeMinFreeBytes}}}
	if err := RecordUpgradeSpaceFailure(root, failure); err != nil {
		t.Fatal(err)
	}
	before := now.Add(-time.Second)
	after := now.Add(time.Second)
	if got := ReadUpgradeSpaceFailure(root, &before); got == nil || got.Code != failure.Code {
		t.Fatalf("record=%v", got)
	}
	if ReadUpgradeSpaceFailure(root, &after) != nil || ReadUpgradeSpaceFailure(root, nil) != nil {
		t.Fatal("stale record accepted")
	}
	if err := os.WriteFile(filepath.Join(LifecycleUpgradeDir(root), "disk-preflight.json"), []byte("broken"), 0600); err != nil {
		t.Fatal(err)
	}
	if ReadUpgradeSpaceFailure(root, &before) != nil {
		t.Fatal("malformed record accepted")
	}
	if !strings.Contains(failure.Error(), "Upgrade deployment was not started") {
		t.Fatal("missing diagnostics")
	}
}

func TestUpgradeSpaceDoesNotFallBackPastDanglingSymlink(t *testing.T) {
	root := t.TempDir()
	link := filepath.Join(root, "runtime")
	if err := os.Symlink(filepath.Join(root, "missing-volume"), link); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	if _, err := existingUpgradeDirectory(filepath.Join(link, "download")); err == nil {
		t.Fatal("dangling upgrade path was treated as an ordinary missing directory")
	}
}
