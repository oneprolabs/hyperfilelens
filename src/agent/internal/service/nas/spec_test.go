package nas

import (
	"testing"

	"hyperfilelens/agent/internal/platform/vfs"
)

const testCustomMount = "/opt/hyperfilelens-agent/mounts/custom/backup"

func TestParseSpecSMB(t *testing.T) {
	spec, err := ParseSpec(map[string]any{
		"resource_id": float64(12),
		"protocol":    "smb",
		"server":      "192.168.1.10",
		"share":       "backup",
		"mount_point": testCustomMount,
		"username":    "user",
		"password":    "secret",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if spec.Protocol != "smb" || spec.Server != "192.168.1.10" || spec.Share != "backup" {
		t.Fatalf("unexpected spec: %+v", spec)
	}
}

func TestParseSpecNFS(t *testing.T) {
	spec, err := ParseSpec(map[string]any{
		"protocol":    "nfs",
		"server":      "192.168.1.20",
		"export_path": "/export/data",
		"mount_point": vfs.SourceMountPoint(vfs.SystemDataDir(), 7),
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if spec.Protocol != "nfs" || spec.ExportPath != "/export/data" {
		t.Fatalf("unexpected spec: %+v", spec)
	}
}

func TestParseSpecRequiresSMBCredentials(t *testing.T) {
	_, err := ParseSpec(map[string]any{
		"protocol":    "smb",
		"server":      "192.168.1.10",
		"share":       "backup",
		"mount_point": testCustomMount,
	})
	if err == nil {
		t.Fatal("expected validation error")
	}
}
