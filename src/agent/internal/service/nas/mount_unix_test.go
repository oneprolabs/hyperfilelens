//go:build !windows

package nas

import (
	"fmt"
	"reflect"
	"strings"
	"testing"

	"hyperfilelens/agent/internal/platform/process"
)

func TestMountHelperErrorMessagesDoNotAssumeNodeRole(t *testing.T) {
	tests := []struct {
		name string
		err  *MountHelperError
		want string
	}{
		{
			name: "missing NFS helper",
			err: &MountHelperError{
				Code: MountHelperMissing, Operation: "mount NFS export",
				Dependency: "nfs-common", Helper: "mount.nfs",
			},
			want: "mount NFS export: nfs-common is not installed (missing mount.nfs helper)",
		},
		{
			name: "missing SMB helper",
			err: &MountHelperError{
				Code: MountHelperMissing, Operation: "mount SMB share",
				Dependency: "cifs-utils", Helper: "mount.cifs",
			},
			want: "mount SMB share: cifs-utils is not installed (missing mount.cifs helper)",
		},
		{
			name: "unusable SMB helper",
			err: &MountHelperError{
				Code: MountHelperUnusable, Operation: "mount SMB share",
				Dependency: "cifs-utils", Helper: "mount.cifs", Cause: "permission denied",
			},
			want: "mount SMB share: cifs-utils is installed but not usable (mount.cifs failed to start: permission denied)",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := test.err.Error()
			if got != test.want {
				t.Fatalf("message = %q want %q", got, test.want)
			}
			lowered := strings.ToLower(got)
			if strings.Contains(lowered, "on this host") || strings.Contains(lowered, "proxy") {
				t.Fatalf("message assumes a node role: %q", got)
			}
		})
	}
}

func TestExpectedMountSourceAndComparison(t *testing.T) {
	nfs := Spec{Protocol: "nfs", Server: "10.0.0.8", ExportPath: "/backup/"}
	got := expectedMountSource(nfs)
	if got != "10.0.0.8:/backup/" {
		t.Fatalf("unexpected NFS source %q", got)
	}
	if !sameMountSource("nfs", "10.0.0.8:/backup", got) {
		t.Fatal("expected trailing slash normalization")
	}
	smb := Spec{Protocol: "smb", Server: "Files.EXAMPLE", Share: "Backups"}
	if !sameMountSource("smb", "//files.example/backups", expectedMountSource(smb)) {
		t.Fatal("expected SMB source comparison to be case-insensitive")
	}
}

func TestUnescapeProcMount(t *testing.T) {
	if got := unescapeProcMount(`/mnt/hfl\040repositories`); got != "/mnt/hfl repositories" {
		t.Fatalf("unexpected unescaped mount %q", got)
	}
}

func TestMountPointInProcMountsMatchesDecodedTarget(t *testing.T) {
	raw := []byte("192.168.10.35:/data /var/lib/hyperfilelens-agent/mounts/custom/nfs-192.168.10.35-data nfs rw,relatime 0 0\n")
	if !mountPointInProcMounts(raw, "/var/lib/hyperfilelens-agent/mounts/custom/nfs-192.168.10.35-data") {
		t.Fatal("expected mountpoint to be found from /proc/mounts")
	}
}

func TestMountPointInProcMountsDoesNotMatchPrefix(t *testing.T) {
	raw := []byte("192.168.10.35:/data /var/lib/hyperfilelens-agent/mounts/custom/nfs-192.168.10.350-data nfs rw,relatime 0 0\n")
	if mountPointInProcMounts(raw, "/var/lib/hyperfilelens-agent/mounts/custom/nfs-192.168.10.35-data") {
		t.Fatal("did not expect a prefix-only mountpoint match")
	}
}

func TestIsBusyMountErrorDetectsCIFSError16(t *testing.T) {
	res := process.Result{Stderr: "mount error(16): Device or resource busy"}
	if !isBusyMountError(res, fmt.Errorf("exit 32")) {
		t.Fatal("expected CIFS error 16 to be treated as busy")
	}
}

func TestParseNestedMountDetails(t *testing.T) {
	raw := `31 24 0:27 / /opt/hfl/data/mounts/sources/7 rw - nfs server:/source rw
32 31 0:28 / /opt/hfl/data/mounts/sources/7/nested rw - nfs server:/nested rw
33 31 0:29 / /opt/hfl/data/mounts/sources/7/folder\040with\040spaces rw - nfs server:/spaces rw
34 24 0:30 / /opt/hfl/data/mounts/sources/70 rw - nfs server:/other rw`
	want := []string{
		"nested_mount=/opt/hfl/data/mounts/sources/7/folder with spaces",
		"nested_mount=/opt/hfl/data/mounts/sources/7/nested",
	}
	got := parseNestedMountDetails(raw, "/opt/hfl/data/mounts/sources/7")
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("parseNestedMountDetails()=%v want=%v", got, want)
	}
}

func TestIsBusyMountErrorIgnoresPermissionDenied(t *testing.T) {
	res := process.Result{Stderr: "mount error(13): Permission denied"}
	if isBusyMountError(res, fmt.Errorf("exit 32")) {
		t.Fatal("did not expect permission denied to be treated as busy")
	}
}
