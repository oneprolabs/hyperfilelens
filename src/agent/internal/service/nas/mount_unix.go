//go:build !windows

package nas

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"log/slog"

	"hyperfilelens/agent/internal/platform/process"
)

func isMounted(mountPoint string) bool {
	mountPoint = strings.TrimSpace(mountPoint)
	if mountPoint == "" {
		return false
	}
	res, err := process.Run(context.Background(), "mountpoint", []string{"-q", mountPoint}, nil, "")
	if err == nil && res.ExitCode == 0 {
		return true
	}
	data, readErr := os.ReadFile("/proc/mounts")
	if readErr != nil {
		return false
	}
	target := filepath.Clean(mountPoint)
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		if filepath.Clean(fields[1]) == target {
			return true
		}
	}
	return false
}

func validateMountedShare(spec Spec) error {
	data, err := os.ReadFile("/proc/mounts")
	if err != nil {
		// Non-Linux Unix platforms do not expose /proc/mounts. The managed-path
		// write probe remains the authoritative writable check there.
		return nil
	}
	target := filepath.Clean(spec.MountPoint)
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 4 || filepath.Clean(unescapeProcMount(fields[1])) != target {
			continue
		}
		actual := unescapeProcMount(fields[0])
		expected := expectedMountSource(spec)
		fsType := strings.ToLower(fields[2])
		protocolMatches := (spec.Protocol == "nfs" && strings.HasPrefix(fsType, "nfs")) ||
			(spec.Protocol == "smb" && (fsType == "cifs" || fsType == "smb3"))
		if !protocolMatches {
			return &MountSourceMismatchError{Expected: expected, Actual: fmt.Sprintf("%s (%s)", actual, fsType)}
		}
		if !sameMountSource(spec.Protocol, actual, expected) {
			return &MountSourceMismatchError{Expected: expected, Actual: actual}
		}
		for _, option := range strings.Split(fields[3], ",") {
			if option == "ro" {
				return &MountReadOnlyError{Source: actual}
			}
		}
		return nil
	}
	return &MountSourceMismatchError{Expected: expectedMountSource(spec), Actual: "unmounted"}
}

func expectedMountSource(spec Spec) string {
	if spec.Protocol == "smb" {
		return fmt.Sprintf("//%s/%s", spec.Server, strings.Trim(spec.Share, "/"))
	}
	return fmt.Sprintf("%s:%s", spec.Server, spec.ExportPath)
}

func sameMountSource(protocol, actual, expected string) bool {
	normalize := func(value string) string {
		return strings.TrimRight(strings.TrimSpace(value), "/")
	}
	if protocol == "smb" {
		return strings.EqualFold(normalize(actual), normalize(expected))
	}
	return normalize(actual) == normalize(expected)
}

func unescapeProcMount(value string) string {
	replacer := strings.NewReplacer(
		`\040`, " ",
		`\011`, "\t",
		`\012`, "\n",
		`\134`, `\`,
	)
	return replacer.Replace(value)
}

func hasUnmountWork(mountPoint string) bool {
	return isMounted(mountPoint)
}

func mountShare(ctx context.Context, spec Spec) error {
	LogSpec("mount_begin", spec)

	if err := os.MkdirAll(spec.MountPoint, 0o755); err != nil {
		LogSpec("mount_failed", spec, "stage", "mkdir_mount_point", "err", err.Error())
		return fmt.Errorf("create mount point: %w", err)
	}
	if isMounted(spec.MountPoint) {
		LogSpec("mount_skip_already_mounted", spec)
		return nil
	}
	var err error
	switch spec.Protocol {
	case "smb":
		err = mountSMB(ctx, spec)
	case "nfs":
		err = mountNFS(ctx, spec)
	default:
		err = fmt.Errorf("unsupported nas protocol %q", spec.Protocol)
	}
	if err != nil {
		LogSpec("mount_failed", spec, "err", err.Error())
		return err
	}
	LogSpec("mount_success", spec)
	return nil
}

func mountSMB(ctx context.Context, spec Spec) error {
	if err := ensureSMBMountHelper(ctx); err != nil {
		slog.Info("nas", "event", "mount_helper_missing", "protocol", "smb", "err", err.Error())
		return err
	}
	args, cleanup, err := formatSMBMountArgs(spec)
	if err != nil {
		return err
	}
	defer func() { cleanup() }()

	source := fmt.Sprintf("//%s/%s", spec.Server, strings.Trim(spec.Share, "/"))
	optsStr := ""
	if len(args) >= 2 && args[len(args)-2] == "-o" {
		optsStr = args[len(args)-1]
	}
	res, runErr := process.Run(ctx, "mount", args, nil, "")
	logMountCommand("smb", source, spec.MountPoint, optsStr, res.ExitCode, res.Stderr, runErr)
	if runErr != nil {
		if charset, unavailable := unavailableSMBCharset(optsStr, res, runErr); unavailable {
			return &SMBCharsetUnavailableError{
				Charset: charset,
				Kernel:  runningKernelRelease(),
				Cause:   mountRunErrorMessage(res, runErr),
			}
		}
		if isBusyMountError(res, runErr) {
			if isMounted(spec.MountPoint) {
				slog.Info("nas", "event", "mount_busy_already_mounted", "protocol", "smb", "source", source, "mount_point", spec.MountPoint)
				return nil
			}
			return fmt.Errorf("mount SMB share: mount point %s is busy but is not an active mount; unmount the stale path or choose another mount point (%s)", spec.MountPoint, mountRunErrorMessage(res, runErr))
		}
		return fmt.Errorf("mount SMB share: %s", mountRunErrorMessage(res, runErr))
	}
	return nil
}

func isBusyMountError(res process.Result, err error) bool {
	output := strings.ToLower(strings.Join([]string{
		res.Stdout,
		res.Stderr,
		fmt.Sprint(err),
	}, "\n"))
	return strings.Contains(output, "mount error(16)") ||
		strings.Contains(output, "device or resource busy") ||
		strings.Contains(output, "already mounted") ||
		strings.Contains(output, "is busy")
}

func unavailableSMBCharset(opts string, res process.Result, err error) (string, bool) {
	charset := mountOptionValue(opts, "iocharset")
	if charset == "" {
		return "", false
	}
	output := strings.ToLower(strings.Join([]string{
		res.Stdout,
		res.Stderr,
		fmt.Sprint(err),
	}, "\n"))
	return charset, strings.Contains(output, "mount error(79)") ||
		strings.Contains(output, "mount error(95)") ||
		strings.Contains(output, "needed shared library") ||
		strings.Contains(output, "unable to load nls charset") ||
		(strings.Contains(output, "iocharset") && strings.Contains(output, strings.ToLower(charset)) && strings.Contains(output, "not found"))
}

func runningKernelRelease() string {
	value, err := os.ReadFile("/proc/sys/kernel/osrelease")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(value))
}

func mountRunErrorMessage(res process.Result, err error) string {
	msg := strings.TrimSpace(res.Stderr)
	if msg == "" {
		msg = strings.TrimSpace(res.Stdout)
	}
	if msg == "" && err != nil {
		msg = err.Error()
	}
	if msg == "" {
		msg = fmt.Sprintf("exit code %d", res.ExitCode)
	}
	return msg
}

func ensureSMBMountHelper(ctx context.Context) error {
	helper := mountHelperPath("mount.cifs")
	if helper == "" {
		return &MountHelperError{
			Code:       MountHelperMissing,
			Operation:  "mount SMB share",
			Dependency: "cifs-utils",
			Helper:     "mount.cifs",
		}
	}
	res, err := process.Run(ctx, helper, []string{"--version"}, nil, "")
	if err == nil && res.ExitCode == 0 {
		return nil
	}
	msg := strings.TrimSpace(res.Stderr)
	if msg == "" {
		msg = strings.TrimSpace(res.Stdout)
	}
	if msg == "" && err != nil {
		msg = err.Error()
	}
	if msg == "" {
		msg = fmt.Sprintf("exit code %d", res.ExitCode)
	}
	return &MountHelperError{
		Code:       MountHelperUnusable,
		Operation:  "mount SMB share",
		Dependency: "cifs-utils",
		Helper:     "mount.cifs",
		Cause:      msg,
	}
}

func mountNFS(ctx context.Context, spec Spec) error {
	if err := ensureNFSMountHelper(); err != nil {
		slog.Info("nas", "event", "mount_helper_missing", "protocol", "nfs", "err", err.Error())
		return err
	}
	source := fmt.Sprintf("%s:%s", spec.Server, spec.ExportPath)
	args := []string{
		"-t", "nfs",
		source,
		spec.MountPoint,
	}
	opts := strings.TrimSpace(spec.Options)
	if opts != "" {
		args = append(args, "-o", opts)
	}
	res, runErr := process.Run(ctx, "mount", args, nil, "")
	logMountCommand("nfs", source, spec.MountPoint, opts, res.ExitCode, res.Stderr, runErr)
	if runErr != nil {
		msg := strings.TrimSpace(res.Stderr)
		if msg == "" {
			msg = runErr.Error()
		}
		return fmt.Errorf("mount NFS export: %s", msg)
	}
	return nil
}

func ensureNFSMountHelper() error {
	if mountHelperPath("mount.nfs") != "" {
		return nil
	}
	return &MountHelperError{
		Code:       MountHelperMissing,
		Operation:  "mount NFS export",
		Dependency: "nfs-common",
		Helper:     "mount.nfs",
	}
}

func mountHelperPath(name string) string {
	if path, err := exec.LookPath(name); err == nil {
		return path
	}
	for _, dir := range []string{"/sbin", "/usr/sbin"} {
		path := filepath.Join(dir, name)
		if info, err := os.Stat(path); err == nil && !info.IsDir() {
			return path
		}
	}
	return ""
}

func unmountShare(ctx context.Context, mountPoint string) error {
	res, runErr := process.Run(ctx, "umount", []string{mountPoint}, nil, "")
	if runErr != nil {
		msg := strings.TrimSpace(res.Stderr)
		if msg == "" {
			msg = runErr.Error()
		}
		return fmt.Errorf("unmount NAS share: %s", msg)
	}
	return nil
}

func lazyUnmountShare(ctx context.Context, mountPoint string) error {
	res, runErr := process.Run(ctx, "umount", []string{"-l", mountPoint}, nil, "")
	if runErr != nil {
		msg := strings.TrimSpace(res.Stderr)
		if msg == "" {
			msg = runErr.Error()
		}
		return fmt.Errorf("lazy unmount NAS share: %s", msg)
	}
	return nil
}

func unmountBusyDetails(mountPoint string) string {
	mountPoint = filepath.Clean(mountPoint)
	details := nestedMountDetails(mountPoint)
	procEntries, err := os.ReadDir("/proc")
	if err != nil {
		return strings.Join(details, ", ")
	}
	for _, entry := range procEntries {
		if !entry.IsDir() {
			continue
		}
		pid, parseErr := strconv.Atoi(entry.Name())
		if parseErr != nil || pid <= 0 {
			continue
		}
		procRoot := filepath.Join("/proc", entry.Name())
		for _, reference := range []string{"cwd", "root"} {
			target, readErr := os.Readlink(filepath.Join(procRoot, reference))
			if readErr == nil && pathWithinMount(target, mountPoint) {
				details = append(details, processReferenceDetail(procRoot, pid, reference))
				break
			}
		}
		if len(details) >= 8 {
			break
		}
		fdEntries, readErr := os.ReadDir(filepath.Join(procRoot, "fd"))
		if readErr != nil {
			continue
		}
		for _, fd := range fdEntries {
			target, linkErr := os.Readlink(filepath.Join(procRoot, "fd", fd.Name()))
			if linkErr == nil && pathWithinMount(strings.TrimSuffix(target, " (deleted)"), mountPoint) {
				details = append(details, processReferenceDetail(procRoot, pid, "fd:"+fd.Name()))
				break
			}
		}
		if len(details) >= 8 {
			break
		}
	}
	sort.Strings(details)
	if len(details) > 12 {
		details = details[:12]
	}
	return strings.Join(details, ", ")
}

func nestedMountDetails(mountPoint string) []string {
	raw, err := os.ReadFile("/proc/self/mountinfo")
	if err != nil {
		return nil
	}
	return parseNestedMountDetails(string(raw), mountPoint)
}

func parseNestedMountDetails(raw string, mountPoint string) []string {
	mountPoint = filepath.Clean(mountPoint)
	details := []string{}
	seen := map[string]struct{}{}
	for line := range strings.SplitSeq(raw, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		child := filepath.Clean(unescapeMountInfoPath(fields[4]))
		if child == mountPoint || !pathWithinMount(child, mountPoint) {
			continue
		}
		detail := fmt.Sprintf("nested_mount=%s", child)
		if _, exists := seen[detail]; exists {
			continue
		}
		seen[detail] = struct{}{}
		details = append(details, detail)
	}
	sort.Strings(details)
	return details
}

func unescapeMountInfoPath(path string) string {
	return strings.NewReplacer(
		`\040`, " ",
		`\011`, "\t",
		`\012`, "\n",
		`\134`, `\`,
	).Replace(path)
}

func pathWithinMount(path string, mountPoint string) bool {
	path = filepath.Clean(path)
	if path == mountPoint {
		return true
	}
	rel, err := filepath.Rel(mountPoint, path)
	return err == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}

func processReferenceDetail(procRoot string, pid int, reference string) string {
	name := "unknown"
	if raw, err := os.ReadFile(filepath.Join(procRoot, "comm")); err == nil {
		if value := strings.TrimSpace(string(raw)); value != "" {
			name = value
		}
	}
	return fmt.Sprintf("pid=%d command=%s reference=%s", pid, name, reference)
}

func writeSMBCredentials(spec Spec) (string, error) {
	file, err := os.CreateTemp("", "hfl-nas-cred-*")
	if err != nil {
		return "", fmt.Errorf("create credentials file: %w", err)
	}
	path := file.Name()
	lines := []string{
		"username=" + spec.Username,
		"password=" + spec.Password,
	}
	if spec.Domain != "" {
		lines = append(lines, "domain="+spec.Domain)
	}
	if _, err := file.WriteString(strings.Join(lines, "\n") + "\n"); err != nil {
		file.Close()
		os.Remove(path)
		return "", fmt.Errorf("write credentials file: %w", err)
	}
	if err := file.Chmod(0o600); err != nil {
		file.Close()
		os.Remove(path)
		return "", fmt.Errorf("secure credentials file: %w", err)
	}
	if err := file.Close(); err != nil {
		os.Remove(path)
		return "", fmt.Errorf("close credentials file: %w", err)
	}
	return path, nil
}
