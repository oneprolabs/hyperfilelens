package nas

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/platform/vfs"
)

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
