package nas

import (
	"os"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/platform/vfs"
)

func TestResolveMountPointRepository(t *testing.T) {
	t.Setenv("HFL_DATA_DIR", t.TempDir())
	dataDir := agentDataDirForMounts()
	got := ResolvedMountPoint(vfs.RepositoryMountPoint(vfs.UnixDataDir(), 9, 3))
	want := vfs.RepositoryMountPoint(dataDir, 9, 3)
	if got != want {
		t.Fatalf("ResolvedMountPoint() = %q want %q", got, want)
	}
}

func TestResolveMountPointSource(t *testing.T) {
	t.Setenv("HFL_DATA_DIR", t.TempDir())
	dataDir := agentDataDirForMounts()
	got := ResolvedMountPoint(vfs.SourceMountPoint(vfs.UnixDataDir(), 12))
	want := vfs.SourceMountPoint(dataDir, 12)
	if got != want {
		t.Fatalf("ResolvedMountPoint() = %q want %q", got, want)
	}
}

func TestResolveMountPointRewritesDefaultUnixDataDir(t *testing.T) {
	t.Setenv("HFL_DATA_DIR", t.TempDir())
	dataDir := agentDataDirForMounts()
	input := filepath.Join(vfs.UnixDataDir(), "mounts", "custom", "nas-data")
	got := ResolvedMountPoint(input)
	want := filepath.Join(dataDir, "mounts", "custom", "nas-data")
	if got != want {
		t.Fatalf("ResolvedMountPoint() = %q want %q", got, want)
	}
}

func TestResolveMountPointRejectsOutsideMounts(t *testing.T) {
	if got := ResolvedMountPoint("/mnt/nas/data"); got != "" {
		t.Fatalf("ResolvedMountPoint() = %q want empty", got)
	}
	if got := ResolvedMountPoint(`D:\backups\repo`); got != "" {
		t.Fatalf("ResolvedMountPoint() = %q want empty", got)
	}
}

func TestCleanupUnmountedMountPointRemovesOnlyEmptyManagedDirectory(t *testing.T) {
	t.Setenv("HFL_DATA_DIR", t.TempDir())
	service := NewService()
	empty := filepath.Join(agentDataDirForMounts(), "mounts", "repositories", "empty")
	if err := os.MkdirAll(empty, 0o755); err != nil {
		t.Fatal(err)
	}
	removed, err := service.CleanupUnmountedMountPoint(empty)
	if err != nil || !removed {
		t.Fatalf("empty mount point removed=%v err=%v", removed, err)
	}
	if _, err := os.Stat(empty); !os.IsNotExist(err) {
		t.Fatalf("empty mount point still exists: %v", err)
	}

	nonEmpty := filepath.Join(agentDataDirForMounts(), "mounts", "repositories", "non-empty")
	if err := os.MkdirAll(nonEmpty, 0o755); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(nonEmpty, "keep")
	if err := os.WriteFile(marker, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	removed, err = service.CleanupUnmountedMountPoint(nonEmpty)
	if err != nil || removed {
		t.Fatalf("non-empty mount point removed=%v err=%v", removed, err)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("non-empty mount point contents changed: %v", err)
	}
}
