//go:build windows

package nas

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestWindowsDriveAllocationWaitHonorsCancellation(t *testing.T) {
	if err := lockWindowsDriveAllocation(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer unlockWindowsDriveAllocation()

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := lockWindowsDriveAllocation(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("lockWindowsDriveAllocation() error = %v", err)
	}
}

func TestWindowsMountMetadataUsesLocalSidecar(t *testing.T) {
	mountPoint := filepath.Join(t.TempDir(), "source-17")
	meta := windowsMountMeta{
		Drive:    "Z:",
		Remote:   `\\server\share`,
		Junction: mountPoint,
	}

	if err := writeMountMeta(mountPoint, meta); err != nil {
		t.Fatalf("writeMountMeta() error = %v", err)
	}
	if _, err := os.Stat(mountPoint); !os.IsNotExist(err) {
		t.Fatalf("metadata write created or touched mount point: %v", err)
	}
	got, path, ok, err := readMountMeta(mountPoint)
	if err != nil {
		t.Fatal(err)
	}
	if !ok || path != mountMetaPath(mountPoint) || got != meta {
		t.Fatalf("readMountMeta() = %#v, %q, %v", got, path, ok)
	}
	if isMounted(mountPoint) {
		t.Fatal("sidecar without a live junction was reported as mounted")
	}
	if !hasUnmountWork(mountPoint) {
		t.Fatal("sidecar was not retained as Agent-owned cleanup work")
	}
}

func TestWindowsMountMetadataReadsLegacyLocation(t *testing.T) {
	mountPoint := filepath.Join(t.TempDir(), "source-18")
	if err := os.MkdirAll(mountPoint, 0o755); err != nil {
		t.Fatal(err)
	}
	want := windowsMountMeta{
		Drive:    "Y:",
		Remote:   `\\server\share`,
		Junction: mountPoint,
	}
	legacy, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(legacyMountMetaPath(mountPoint), legacy, 0o600); err != nil {
		t.Fatal(err)
	}

	meta, path, ok, err := readMountMeta(mountPoint)
	if err != nil {
		t.Fatal(err)
	}
	if !ok || path != legacyMountMetaPath(mountPoint) || meta != want {
		t.Fatalf("readMountMeta() = %#v, %q, %v", meta, path, ok)
	}
}

func TestWindowsMountMetadataDoesNotOverwriteOwnedState(t *testing.T) {
	mountPoint := filepath.Join(t.TempDir(), "source-20")
	meta := windowsMountMeta{
		Drive:    "W:",
		Remote:   `\\server\share`,
		Junction: mountPoint,
	}
	if err := writeMountMeta(mountPoint, meta); err != nil {
		t.Fatal(err)
	}

	replacement := meta
	replacement.Drive = "V:"
	if err := writeMountMeta(mountPoint, replacement); err == nil {
		t.Fatal("writeMountMeta() overwrote existing Agent-owned state")
	}
	got, _, ok, err := readMountMeta(mountPoint)
	if err != nil || !ok || got != meta {
		t.Fatalf("readMountMeta() = %#v, %v, %v", got, ok, err)
	}
}

func TestWindowsMountMetadataRejectsAnotherJunction(t *testing.T) {
	mountPoint := filepath.Join(t.TempDir(), "source-19")
	want := windowsMountMeta{
		Drive:    "X:",
		Remote:   `\\server\share`,
		Junction: filepath.Join(t.TempDir(), "not-the-managed-mount"),
	}
	data, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(mountMetaPath(mountPoint), data, 0o600); err != nil {
		t.Fatal(err)
	}

	if _, _, _, err := readMountMeta(mountPoint); err == nil {
		t.Fatal("readMountMeta() accepted metadata for another junction")
	}
}

func TestNetUseOutputShowsManagedDriveAndRemote(t *testing.T) {
	output := `Local name        Z:
Remote name       \\server\share`
	if !netUseOutputShowsDrive(output, "z:") {
		t.Fatal("net use output did not identify the managed drive")
	}
	if !netUseOutputMatchesRemote(output, "z:", `\\server\share`) {
		t.Fatal("net use output did not match the managed remote")
	}
	if netUseOutputMatchesRemote(output, "z:", `\\server\other`) {
		t.Fatal("net use output matched another remote")
	}
	prefixOutput := `Local name        Z:
Remote name       \\server\share-archive`
	if netUseOutputMatchesRemote(prefixOutput, "z:", `\\server\share`) {
		t.Fatal("net use output matched a different share with the same prefix")
	}
	if netUseOutputShowsDrive("Remote name \\\\server\\z:archive", "z:") {
		t.Fatal("net use output mistook remote text for a local drive")
	}
}
