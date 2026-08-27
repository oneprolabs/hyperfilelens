package vfs

import "testing"

func TestRepositoryMountPoint(t *testing.T) {
	got := RepositoryMountPoint(SystemDataDir(), 42, 3)
	want := "/opt/hyperfilelens-agent/mounts/repositories/repo-42-node-3"
	if got != want {
		t.Fatalf("RepositoryMountPoint() = %q want %q", got, want)
	}
}

func TestRestoreRepositoryMountPoint(t *testing.T) {
	got := RestoreRepositoryMountPoint("/test-agent", 42, 3)
	want := "/test-agent/mounts/restores/repo-42-node-3"
	if got != want {
		t.Fatalf("RestoreRepositoryMountPoint() = %q want %q", got, want)
	}
}

func TestSourceMountPoint(t *testing.T) {
	got := SourceMountPoint(SystemDataDir(), 12)
	want := "/opt/hyperfilelens-agent/mounts/sources/source-12"
	if got != want {
		t.Fatalf("SourceMountPoint() = %q want %q", got, want)
	}
}
