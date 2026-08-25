package engine

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/service/nas"
)

const (
	nasTestExecutionTimeout = 30 * time.Second
	nasTestCleanupTimeout   = 25 * time.Second
)

type nasTestOutcome struct {
	info nas.SpaceInfo
	err  error
}

type nasTestService interface {
	Test(context.Context, nas.Spec) (nas.SpaceInfo, error)
	TestForWrite(context.Context, nas.Spec) (nas.SpaceInfo, error)
	Unmount(context.Context, string) error
}

func logNasTask(ctx context.Context, event string, taskID string, spec nas.Spec, extra ...any) {
	args := append([]any{
		"event", event,
		"protocol", spec.Protocol,
		"server", spec.Server,
		"mount_point", spec.MountPoint,
		"resource_id", spec.ResourceID,
	}, extra...)
	if taskID != "" {
		args = append(args, "task_id", taskID)
	}
	slog.InfoContext(ctx, "nas task", args...)
}

func parseNASSpec(raw any) (nas.Spec, bool, error) {
	data, ok := raw.(map[string]any)
	if !ok || len(data) == 0 {
		return nas.Spec{}, false, nil
	}
	spec, err := nas.ParseSpec(data)
	if err != nil {
		return nas.Spec{}, false, err
	}
	spec.MountPoint = nas.ResolvedMountPoint(spec.MountPoint)
	return spec, true, nil
}

func nasResult(spec nas.Spec, info nas.SpaceInfo) map[string]any {
	return map[string]any{
		"mount_point":  spec.MountPoint,
		"mount_status": "mounted",
		"protocol":     spec.Protocol,
		"server":       spec.Server,
		"space_info": map[string]any{
			"total_bytes": info.TotalBytes,
			"used_bytes":  info.UsedBytes,
			"free_bytes":  info.FreeBytes,
		},
	}
}

func (e *Engine) ensureNASMounted(ctx context.Context, p Payload) error {
	spec, ok, err := parseNASSpec(p.Extra["nas"])
	if err != nil {
		return err
	}
	if !ok {
		return nil
	}
	return nas.NewService().EnsureMounted(ctx, spec)
}

func validateNASRestoreTarget(p Payload, targetPath string) error {
	spec, ok, err := parseNASSpec(p.Extra["nas"])
	if err != nil {
		return err
	}
	if !ok {
		return nil
	}
	mountRoot := filepath.Clean(strings.TrimSpace(spec.MountPoint))
	target := filepath.Clean(strings.TrimSpace(targetPath))
	_, relative, err := secureRelativePath(target, mountRoot, true)
	if err != nil {
		return errors.New("NAS restore target must be inside its mount point")
	}
	rootFD, _, err := secureOpenDirectory(
		mountRoot,
		mountRoot,
		true,
		uint64(os.O_RDONLY),
	)
	if err != nil {
		return fmt.Errorf("NAS mount point is not a safe directory: %w", err)
	}
	rootDirectory := secureDirectoryFile(rootFD, mountRoot)
	if rootDirectory == nil {
		return errors.New("restricted Data Gateway filesystem operations require Linux")
	}
	_ = rootDirectory.Close()

	current := mountRoot
	for _, component := range strings.Split(relative, string(os.PathSeparator)) {
		if component == "." || component == "" {
			continue
		}
		current = filepath.Join(current, component)
		info, statErr := os.Lstat(current)
		if os.IsNotExist(statErr) {
			break
		}
		if statErr != nil {
			return statErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("NAS restore target contains a symlink: %s", current)
		}
	}
	return nil
}

func resolveNASRestoreTarget(p Payload, logicalPath string) (string, string, error) {
	spec, ok, err := parseNASSpec(p.Extra["nas"])
	if err != nil {
		return "", "", err
	}
	if !ok {
		return strings.TrimSpace(logicalPath), "", nil
	}
	raw := strings.TrimSpace(logicalPath)
	if raw == "" || strings.ContainsRune(raw, '\x00') || strings.Contains(raw, `\`) {
		return "", "", errors.New("NAS restore target is invalid")
	}
	for _, component := range strings.Split(raw, "/") {
		if component == "." || component == ".." {
			return "", "", errors.New("NAS restore target contains an unsafe path component")
		}
	}
	relative := strings.TrimPrefix(raw, "/")
	if relative == "" {
		relative = "."
	}
	target := filepath.Join(spec.MountPoint, filepath.FromSlash(relative))
	if err := validateNASRestoreTarget(p, target); err != nil {
		return "", "", err
	}
	return target, spec.MountPoint, nil
}

func (e *Engine) runNasMount(ctx context.Context, p Payload) (string, map[string]any, string) {
	spec, ok, err := parseNASSpec(p.Extra["nas"])
	if err != nil {
		return "failed", nil, err.Error()
	}
	if !ok {
		if _, hasRaw := p.Extra["nas"]; hasRaw {
			return "failed", nil, "invalid nas payload"
		}
		spec, err = nas.ParseSpec(p.Extra)
		if err != nil {
			return "failed", nil, err.Error()
		}
	}
	logNasTask(ctx, "mount_start", "", spec)
	info, err := nas.NewService().Mount(ctx, spec)
	if err != nil {
		logNasTask(ctx, "mount_failed", "", spec, "err", err.Error())
		if result := nasFailureResult(err); result != nil {
			return "failed", result, err.Error()
		}
		return "failed", nil, err.Error()
	}
	logNasTask(ctx, "mount_ok", "", spec, "total_bytes", info.TotalBytes)
	return "success", nasResult(spec, info), ""
}

func (e *Engine) runNasUnmount(ctx context.Context, p Payload) (string, map[string]any, string) {
	mountPoint := stringValue(p.Extra["mount_point"])
	if mountPoint == "" {
		if spec, ok, err := parseNASSpec(p.Extra["nas"]); err != nil {
			return "failed", nil, err.Error()
		} else if ok {
			mountPoint = spec.MountPoint
		}
	}
	if mountPoint == "" {
		spec, err := nas.ParseSpec(p.Extra)
		if err != nil {
			return "failed", nil, err.Error()
		}
		mountPoint = spec.MountPoint
	}
	if mountPoint == "" {
		return "failed", nil, "mount_point is required"
	}
	logNasTask(ctx, "unmount_start", "", nas.Spec{MountPoint: mountPoint}, "mount_point", mountPoint)
	forceCleanup, _ := payloadBoolValue(p.Extra["force_cleanup"])
	cleanup, err := nas.NewService().UnmountWithOptions(
		ctx,
		mountPoint,
		nas.UnmountOptions{Force: forceCleanup},
	)
	if err != nil {
		logNasTask(ctx, "unmount_failed", "", nas.Spec{MountPoint: mountPoint}, "err", err.Error())
		return "failed", nil, err.Error()
	}
	logNasTask(
		ctx,
		"unmount_ok",
		"",
		nas.Spec{MountPoint: mountPoint},
		"attempts",
		cleanup.Attempts,
		"lazy_unmount",
		cleanup.LazyUnmount,
		"cleanup_complete",
		cleanup.CleanupComplete,
	)
	result := map[string]any{
		"mount_point":        mountPoint,
		"mount_status":       "unmounted",
		"cleanup_complete":   cleanup.CleanupComplete,
		"lazy_unmount":       cleanup.LazyUnmount,
		"unmount_attempts":   cleanup.Attempts,
		"retained_resources": cleanup.RetainedResources,
		"warnings":           cleanup.Warnings,
	}
	return "success", result, ""
}

func (e *Engine) runNasTest(ctx context.Context, p Payload) (string, map[string]any, string) {
	return runNasTestWithService(ctx, p, nas.NewService())
}

func runNasTestWithService(ctx context.Context, p Payload, service nasTestService) (string, map[string]any, string) {
	spec, ok, err := parseNASSpec(p.Extra["nas"])
	if err != nil {
		return "failed", nil, err.Error()
	}
	if !ok {
		spec, err = nas.ParseSpec(p.Extra)
		if err != nil {
			return "failed", nil, err.Error()
		}
	}
	logNasTask(ctx, "test_start", "", spec)
	requireWrite, _ := payloadBoolValue(p.Extra["require_write"])
	testCtx, testCancel := context.WithTimeout(ctx, nasTestExecutionTimeout)
	testResult := make(chan nasTestOutcome, 1)
	go func() {
		var outcome nasTestOutcome
		if requireWrite {
			outcome.info, outcome.err = service.TestForWrite(testCtx, spec)
		} else {
			outcome.info, outcome.err = service.Test(testCtx, spec)
		}
		testResult <- outcome
	}()
	var info nas.SpaceInfo
	var testErr error
	select {
	case outcome := <-testResult:
		// If the operation completed at the same instant as the deadline,
		// prefer the deadline outcome.  A successful result must never be
		// reported after the probe's execution window has elapsed.
		if deadlineErr := testCtx.Err(); deadlineErr != nil {
			testErr = deadlineErr
		} else {
			info, testErr = outcome.info, outcome.err
		}
	case <-testCtx.Done():
		testErr = testCtx.Err()
	}
	testCancel()
	cleanupAfterTest, _ := payloadBoolValue(p.Extra["cleanup_after_test"])
	var cleanupErr error
	if cleanupAfterTest {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.WithoutCancel(ctx), nasTestCleanupTimeout)
		cleanupErr = service.Unmount(cleanupCtx, spec.MountPoint)
		cleanupCancel()
	}
	if testErr != nil {
		errMessage := testErr.Error()
		if cleanupErr != nil {
			errMessage += "; cleanup failed: " + cleanupErr.Error()
		}
		logNasTask(ctx, "test_failed", "", spec, "err", errMessage)
		result := map[string]any{
			"storage_type": "nas",
			"protocol":     spec.Protocol,
			"server":       spec.Server,
			"mount_point":  spec.MountPoint,
		}
		for key, value := range nasFailureResult(testErr) {
			result[key] = value
		}
		if cleanupAfterTest {
			result["cleanup_status"] = "success"
			result["mount_status"] = "unmounted"
			if cleanupErr != nil {
				result["cleanup_status"] = "failed"
				result["mount_status"] = "cleanup_failed"
			}
		}
		return "failed", result, errMessage
	}
	result := nasResult(spec, info)
	result["storage_type"] = "nas"
	if cleanupAfterTest {
		if cleanupErr != nil {
			result["cleanup_status"] = "failed"
			result["mount_status"] = "cleanup_failed"
			logNasTask(ctx, "test_cleanup_failed", "", spec, "err", cleanupErr.Error())
			return "failed", result, "cleanup failed: " + cleanupErr.Error()
		}
		result["cleanup_status"] = "success"
		result["mount_status"] = "unmounted"
	}
	logNasTask(ctx, "test_ok", "", spec, "total_bytes", info.TotalBytes)
	return "success", result, ""
}

func smbCharsetFailureResult(err error) map[string]any {
	var charsetErr *nas.SMBCharsetUnavailableError
	if !errors.As(err, &charsetErr) {
		return nil
	}
	return map[string]any{
		"error_code": "SMB_CHARSET_UNAVAILABLE",
		"charset":    charsetErr.Charset,
		"kernel":     charsetErr.Kernel,
	}
}

func mountHelperFailureResult(err error) map[string]any {
	var helperErr *nas.MountHelperError
	if !errors.As(err, &helperErr) {
		return nil
	}
	remediation := "install_nas_mount_helper"
	if helperErr.Code == nas.MountHelperUnusable {
		remediation = "repair_nas_mount_helper"
	}
	return map[string]any{
		"error_code":  helperErr.Code,
		"remediation": remediation,
		"dependency":  helperErr.Dependency,
		"helper":      helperErr.Helper,
	}
}

func nasFailureResult(err error) map[string]any {
	if errors.Is(err, context.DeadlineExceeded) {
		return map[string]any{
			"error_code":  "NAS_CONNECTION_TIMEOUT",
			"remediation": "retry_nas_connection",
		}
	}
	if result := mountHelperFailureResult(err); result != nil {
		return result
	}
	var readOnlyErr *nas.MountReadOnlyError
	if errors.As(err, &readOnlyErr) {
		return map[string]any{
			"error_code":  "NAS_MOUNT_READ_ONLY",
			"remediation": "enable_write_access",
		}
	}
	var sourceMismatchErr *nas.MountSourceMismatchError
	if errors.As(err, &sourceMismatchErr) {
		return map[string]any{
			"error_code":  "NAS_MOUNT_SOURCE_MISMATCH",
			"remediation": "remount_nas",
		}
	}
	var writeProbeErr *nas.WriteProbeError
	if errors.As(err, &writeProbeErr) {
		return map[string]any{
			"error_code":  "NAS_WRITE_PERMISSION_DENIED",
			"remediation": "grant_write_access",
		}
	}
	return smbCharsetFailureResult(err)
}
