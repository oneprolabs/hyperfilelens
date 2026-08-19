//go:build !windows

package nas

import (
	"fmt"
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

func TestIsBusyMountErrorDetectsCIFSError16(t *testing.T) {
	res := process.Result{Stderr: "mount error(16): Device or resource busy"}
	if !isBusyMountError(res, fmt.Errorf("exit 32")) {
		t.Fatal("expected CIFS error 16 to be treated as busy")
	}
}

func TestIsBusyMountErrorIgnoresPermissionDenied(t *testing.T) {
	res := process.Result{Stderr: "mount error(13): Permission denied"}
	if isBusyMountError(res, fmt.Errorf("exit 32")) {
		t.Fatal("did not expect permission denied to be treated as busy")
	}
}
