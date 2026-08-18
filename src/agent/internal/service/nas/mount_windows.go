//go:build windows

package nas

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/platform/process"
)

type windowsMountMeta struct {
	Drive    string `json:"drive"`
	Remote   string `json:"remote"`
	Junction string `json:"junction"`
}

func mountMetaPath(mountPoint string) string {
	return filepath.Join(mountPoint, ".hfl-nas-mount.json")
}

func readMountMeta(mountPoint string) (windowsMountMeta, bool) {
	data, err := os.ReadFile(mountMetaPath(mountPoint))
	if err != nil {
		return windowsMountMeta{}, false
	}
	var meta windowsMountMeta
	if err := json.Unmarshal(data, &meta); err != nil {
		return windowsMountMeta{}, false
	}
	if strings.TrimSpace(meta.Drive) == "" || strings.TrimSpace(meta.Junction) == "" {
		return windowsMountMeta{}, false
	}
	return meta, true
}

func writeMountMeta(mountPoint string, meta windowsMountMeta) error {
	if err := os.MkdirAll(mountPoint, 0o755); err != nil {
		return err
	}
	data, err := json.Marshal(meta)
	if err != nil {
		return err
	}
	return os.WriteFile(mountMetaPath(mountPoint), data, 0o600)
}

func isMounted(mountPoint string) bool {
	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return false
	}
	meta, ok := readMountMeta(mountPoint)
	if !ok {
		return false
	}
	if _, err := os.Stat(meta.Junction); err != nil {
		return false
	}
	return netUseShowsDrive(meta.Drive)
}

func validateMountedShare(spec Spec) error {
	meta, ok := readMountMeta(spec.MountPoint)
	if !ok {
		return &MountSourceMismatchError{Expected: expectedWindowsRemote(spec), Actual: "unmounted"}
	}
	expected := expectedWindowsRemote(spec)
	actual, mounted := netUseRemote(meta.Drive)
	if !mounted {
		return &MountSourceMismatchError{Expected: expected, Actual: "unmounted"}
	}
	if !strings.EqualFold(strings.TrimRight(actual, `\/`), strings.TrimRight(expected, `\/`)) {
		return &MountSourceMismatchError{Expected: expected, Actual: actual}
	}
	return nil
}

func expectedWindowsRemote(spec Spec) string {
	return fmt.Sprintf(`\\%s\%s`, spec.Server, strings.Trim(spec.Share, "/\\"))
}

func mountShare(ctx context.Context, spec Spec) error {
	spec.MountPoint = ResolvedMountPoint(spec.MountPoint)
	if spec.MountPoint == "" {
		return fmt.Errorf("mount_point is required")
	}
	if isMounted(spec.MountPoint) {
		return nil
	}
	switch spec.Protocol {
	case "smb":
		return mountSMB(ctx, spec)
	case "nfs":
		return fmt.Errorf("NFS mount is not supported on Windows agent hosts yet")
	default:
		return fmt.Errorf("unsupported nas protocol %q", spec.Protocol)
	}
}

func mountSMB(ctx context.Context, spec Spec) error {
	remote := expectedWindowsRemote(spec)
	user := spec.Username
	if spec.Domain != "" {
		user = spec.Domain + `\` + spec.Username
	}

	drive, err := pickAvailableDriveLetter()
	if err != nil {
		return err
	}
	args := []string{
		"use",
		drive,
		remote,
		spec.Password,
		"/user:" + user,
		"/persistent:no",
	}
	res, runErr := process.Run(ctx, "net", args, nil, "")
	if runErr != nil {
		msg := strings.TrimSpace(res.Stderr + "\n" + res.Stdout)
		if msg == "" {
			msg = runErr.Error()
		}
		return fmt.Errorf("mount SMB share: %s", msg)
	}

	junction := spec.MountPoint
	if err := os.MkdirAll(filepath.Dir(junction), 0o755); err != nil {
		_ = netUseDelete(ctx, drive)
		return fmt.Errorf("create mount parent: %w", err)
	}
	if _, err := os.Stat(junction); err == nil {
		_ = os.Remove(junction)
	}
	linkArgs := []string{"/c", "mklink", "/J", junction, drive + `\`}
	linkRes, linkErr := process.Run(ctx, "cmd", linkArgs, nil, "")
	if linkErr != nil {
		_ = netUseDelete(ctx, drive)
		msg := strings.TrimSpace(linkRes.Stderr + "\n" + linkRes.Stdout)
		if msg == "" {
			msg = linkErr.Error()
		}
		return fmt.Errorf("create SMB junction: %s", msg)
	}
	meta := windowsMountMeta{
		Drive:    drive,
		Remote:   remote,
		Junction: junction,
	}
	if err := writeMountMeta(junction, meta); err != nil {
		_ = netUseDelete(ctx, drive)
		return fmt.Errorf("record SMB mount metadata: %w", err)
	}
	return nil
}

func unmountShare(ctx context.Context, mountPoint string) error {
	mountPoint = ResolvedMountPoint(mountPoint)
	if mountPoint == "" {
		return fmt.Errorf("mount_point is required")
	}
	meta, ok := readMountMeta(mountPoint)
	if !ok {
		return nil
	}
	if _, err := os.Stat(meta.Junction); err == nil {
		_ = os.Remove(meta.Junction)
	}
	_ = netUseDelete(ctx, meta.Drive)
	_ = os.Remove(mountMetaPath(mountPoint))
	return nil
}

func pickAvailableDriveLetter() (string, error) {
	for letter := 'Z'; letter >= 'D'; letter-- {
		drive := fmt.Sprintf("%c:", letter)
		if _, err := os.Stat(drive + `\`); err != nil {
			return drive, nil
		}
	}
	return "", fmt.Errorf("no available drive letter for SMB mount")
}

func netUseShowsDrive(drive string) bool {
	_, ok := netUseRemote(drive)
	return ok
}

func netUseRemote(drive string) (string, bool) {
	drive = strings.TrimSpace(drive)
	if drive == "" {
		return "", false
	}
	res, err := process.Run(context.Background(), "net", []string{"use", drive}, nil, "")
	if err != nil {
		return "", false
	}
	text := res.Stdout + "\n" + res.Stderr
	if strings.Contains(strings.ToLower(text), "disconnected") {
		return "", false
	}
	return parseNetUseRemote(text)
}

func netUseDelete(ctx context.Context, drive string) error {
	args := []string{"use", drive, "/delete", "/y"}
	res, runErr := process.Run(ctx, "net", args, nil, "")
	if runErr != nil {
		msg := strings.TrimSpace(res.Stderr + "\n" + res.Stdout)
		if msg == "" {
			msg = runErr.Error()
		}
		return fmt.Errorf("unmount NAS share: %s", msg)
	}
	return nil
}
