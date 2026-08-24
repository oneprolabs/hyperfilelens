package engine

import (
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"testing"

	"hyperfilelens/agent/internal/platform/vfs"
	"hyperfilelens/agent/internal/service/nas"
)

type fakeNASTestService struct {
	info              nas.SpaceInfo
	testErr           error
	writeTestErr      error
	writeTestCalls    int
	unmountErr        error
	unmountCalls      int
	unmountContextErr error
}

func (f *fakeNASTestService) Test(context.Context, nas.Spec) (nas.SpaceInfo, error) {
	return f.info, f.testErr
}

func (f *fakeNASTestService) TestForWrite(context.Context, nas.Spec) (nas.SpaceInfo, error) {
	f.writeTestCalls++
	return f.info, f.writeTestErr
}

func (f *fakeNASTestService) Unmount(ctx context.Context, _ string) error {
	f.unmountCalls++
	f.unmountContextErr = ctx.Err()
	return f.unmountErr
}

func nasValidationTestPayload(t *testing.T, cleanup bool) Payload {
	t.Helper()
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	return Payload{Extra: map[string]any{
		"cleanup_after_test": cleanup,
		"nas": map[string]any{
			"resource_id": 8,
			"protocol":    "nfs",
			"server":      "10.0.0.30",
			"export_path": "/backup",
			"mount_point": filepath.Join(vfs.UnixDataDir(), "mounts", "validations", "test", "repo-8"),
		},
	}}
}

func nasRestoreTestPayload(t *testing.T) (Payload, string) {
	t.Helper()
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	mountRoot := vfs.SourceMountPoint(dataDir, 4)
	if err := os.MkdirAll(mountRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	return Payload{Extra: map[string]any{
		"nas": map[string]any{
			"resource_id": 4,
			"protocol":    "nfs",
			"server":      "10.0.0.20",
			"export_path": "/restore",
			"mount_point": vfs.SourceMountPoint(vfs.UnixDataDir(), 4),
		},
	}}, mountRoot
}

func TestResolveNASRestoreTargetUsesRuntimeDataDirectory(t *testing.T) {
	payload, mountRoot := nasRestoreTestPayload(t)

	target, resolvedRoot, err := resolveNASRestoreTarget(
		payload,
		"/restored/data",
	)

	if err != nil {
		t.Fatalf("valid NAS restore target rejected: %v", err)
	}
	if resolvedRoot != mountRoot {
		t.Fatalf("resolved mount root = %q want %q", resolvedRoot, mountRoot)
	}
	want := filepath.Join(mountRoot, "restored", "data")
	if target != want {
		t.Fatalf("resolved target = %q want %q", target, want)
	}
}

func TestValidateNASRestoreTargetRequiresMountContainment(t *testing.T) {
	payload, mountRoot := nasRestoreTestPayload(t)

	if err := validateNASRestoreTarget(
		payload,
		filepath.Join(mountRoot, "restored", "data"),
	); err != nil {
		t.Fatalf("valid NAS restore target rejected: %v", err)
	}
	if err := validateNASRestoreTarget(payload, "/tmp/outside"); err == nil {
		t.Fatal("expected NAS restore target escape rejection")
	}
}

func TestValidateNASRestoreTargetRejectsSymlinkEscape(t *testing.T) {
	payload, mountRoot := nasRestoreTestPayload(t)
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(mountRoot, "escape")); err != nil {
		t.Fatal(err)
	}

	err := validateNASRestoreTarget(
		payload,
		filepath.Join(mountRoot, "escape", "restored.txt"),
	)

	if err == nil {
		t.Fatal("expected NAS restore symlink escape rejection")
	}
}

func TestRunNASTestWithCleanupUnmountsAfterSuccess(t *testing.T) {
	service := &fakeNASTestService{info: nas.SpaceInfo{TotalBytes: 1024, FreeBytes: 512}}

	status, result, message := runNasTestWithService(
		context.Background(),
		nasValidationTestPayload(t, true),
		service,
	)

	if status != "success" || message != "" {
		t.Fatalf("status=%q message=%q", status, message)
	}
	if service.unmountCalls != 1 {
		t.Fatalf("unmount calls=%d want 1", service.unmountCalls)
	}
	if result["mount_status"] != "unmounted" || result["cleanup_status"] != "success" {
		t.Fatalf("unexpected cleanup result: %#v", result)
	}
}

func TestRunNASTestWithoutCleanupPreservesExistingBehavior(t *testing.T) {
	service := &fakeNASTestService{info: nas.SpaceInfo{TotalBytes: 1024}}

	status, result, message := runNasTestWithService(
		context.Background(),
		nasValidationTestPayload(t, false),
		service,
	)

	if status != "success" || message != "" {
		t.Fatalf("status=%q message=%q", status, message)
	}
	if service.unmountCalls != 0 {
		t.Fatalf("unmount calls=%d want 0", service.unmountCalls)
	}
	if result["mount_status"] != "mounted" {
		t.Fatalf("mount status=%#v want mounted", result["mount_status"])
	}
	if _, ok := result["cleanup_status"]; ok {
		t.Fatalf("unexpected cleanup status: %#v", result)
	}
}

func TestRunNASTestUsesWriteProbeWhenRequired(t *testing.T) {
	service := &fakeNASTestService{info: nas.SpaceInfo{TotalBytes: 1024}}
	payload := nasValidationTestPayload(t, true)
	payload.Extra["require_write"] = true

	status, _, message := runNasTestWithService(
		context.Background(),
		payload,
		service,
	)

	if status != "success" || message != "" {
		t.Fatalf("status=%q message=%q", status, message)
	}
	if service.writeTestCalls != 1 {
		t.Fatalf("write test calls=%d want 1", service.writeTestCalls)
	}
}

func TestRunNASTestClassifiesWriteProbeFailure(t *testing.T) {
	service := &fakeNASTestService{
		writeTestErr: &nas.WriteProbeError{Cause: fs.ErrPermission},
	}
	payload := nasValidationTestPayload(t, true)
	payload.Extra["require_write"] = true

	status, result, message := runNasTestWithService(
		context.Background(),
		payload,
		service,
	)

	if status != "failed" || message == "" {
		t.Fatalf("status=%q message=%q", status, message)
	}
	if result["error_code"] != "NAS_WRITE_PERMISSION_DENIED" {
		t.Fatalf("unexpected write probe result: %#v", result)
	}
	if result["remediation"] != "grant_write_access" {
		t.Fatalf("unexpected write probe remediation: %#v", result)
	}
}

func TestRunNASTestCleanupFailureFailsSuccessfulProbe(t *testing.T) {
	service := &fakeNASTestService{
		info:       nas.SpaceInfo{TotalBytes: 1024},
		unmountErr: errors.New("mount remains active"),
	}

	status, result, message := runNasTestWithService(
		context.Background(),
		nasValidationTestPayload(t, true),
		service,
	)

	if status != "failed" || message != "cleanup failed: mount remains active" {
		t.Fatalf("status=%q message=%q", status, message)
	}
	if result["cleanup_status"] != "failed" || result["mount_status"] != "cleanup_failed" {
		t.Fatalf("unexpected cleanup failure result: %#v", result)
	}
}

func TestRunNASTestReportsProbeAndCleanupFailures(t *testing.T) {
	service := &fakeNASTestService{
		testErr:    errors.New("cannot read filesystem space"),
		unmountErr: errors.New("mount remains active"),
	}

	status, result, message := runNasTestWithService(
		context.Background(),
		nasValidationTestPayload(t, true),
		service,
	)

	if status != "failed" || message != "cannot read filesystem space; cleanup failed: mount remains active" {
		t.Fatalf("status=%q message=%q", status, message)
	}
	if service.unmountCalls != 1 {
		t.Fatalf("unmount calls=%d want 1", service.unmountCalls)
	}
	if result["cleanup_status"] != "failed" || result["mount_status"] != "cleanup_failed" {
		t.Fatalf("unexpected combined failure result: %#v", result)
	}
}

func TestRunNASTestReportsSMBCharsetUnavailable(t *testing.T) {
	service := &fakeNASTestService{
		testErr: &nas.SMBCharsetUnavailableError{
			Charset: "utf8",
			Kernel:  "6.8.0-test-generic",
			Cause:   "iocharset utf8 not found",
		},
	}
	payload := nasValidationTestPayload(t, true)
	nasPayload := payload.Extra["nas"].(map[string]any)
	nasPayload["protocol"] = "smb"
	nasPayload["share"] = "media"
	nasPayload["options"] = "rw,iocharset=utf8"
	nasPayload["username"] = "backup"
	nasPayload["password"] = "secret"
	delete(nasPayload, "export_path")

	status, result, message := runNasTestWithService(
		context.Background(),
		payload,
		service,
	)

	if status != "failed" || message == "" {
		t.Fatalf("status=%q message=%q", status, message)
	}
	if result["error_code"] != "SMB_CHARSET_UNAVAILABLE" {
		t.Fatalf("error code=%#v", result["error_code"])
	}
	if result["charset"] != "utf8" || result["kernel"] != "6.8.0-test-generic" {
		t.Fatalf("unexpected charset details: %#v", result)
	}
	if service.unmountCalls != 1 || result["cleanup_status"] != "success" {
		t.Fatalf("cleanup result=%#v calls=%d", result, service.unmountCalls)
	}
}

func TestRunNASTestReportsMountHelperRemediation(t *testing.T) {
	tests := []struct {
		name        string
		err         *nas.MountHelperError
		remediation string
	}{
		{
			name: "missing NFS helper",
			err: &nas.MountHelperError{
				Code:       nas.MountHelperMissing,
				Operation:  "mount NFS export",
				Dependency: "nfs-common",
				Helper:     "mount.nfs",
			},
			remediation: "install_nas_mount_helper",
		},
		{
			name: "unusable SMB helper",
			err: &nas.MountHelperError{
				Code:       nas.MountHelperUnusable,
				Operation:  "mount SMB share",
				Dependency: "cifs-utils",
				Helper:     "mount.cifs",
				Cause:      "permission denied",
			},
			remediation: "repair_nas_mount_helper",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			service := &fakeNASTestService{testErr: test.err}
			status, result, message := runNasTestWithService(
				context.Background(),
				nasValidationTestPayload(t, true),
				service,
			)

			if status != "failed" || message != test.err.Error() {
				t.Fatalf("status=%q message=%q", status, message)
			}
			if result["error_code"] != test.err.Code || result["remediation"] != test.remediation {
				t.Fatalf("unexpected helper result: %#v", result)
			}
			if result["dependency"] != test.err.Dependency || result["helper"] != test.err.Helper {
				t.Fatalf("unexpected dependency result: %#v", result)
			}
		})
	}
}

func TestRunNASTestCleanupIgnoresCanceledProbeContext(t *testing.T) {
	service := &fakeNASTestService{testErr: context.Canceled}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	status, result, _ := runNasTestWithService(
		ctx,
		nasValidationTestPayload(t, true),
		service,
	)

	if status != "failed" {
		t.Fatalf("status=%q want failed", status)
	}
	if service.unmountCalls != 1 || service.unmountContextErr != nil {
		t.Fatalf("cleanup did not use independent context: calls=%d err=%v", service.unmountCalls, service.unmountContextErr)
	}
	if result["cleanup_status"] != "success" {
		t.Fatalf("unexpected cleanup result: %#v", result)
	}
}
