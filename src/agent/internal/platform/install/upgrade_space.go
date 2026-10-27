package install

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	agentdisk "hyperfilelens/agent/internal/platform/disk"
)

// DefaultUpgradeMinFreeBytes is a baseline reserve, not a prediction of the
// entire upgrade's peak usage. Recheck after preparation and before service stop.
const DefaultUpgradeMinFreeBytes uint64 = 512 * 1024 * 1024

const (
	UpgradeSpaceInsufficient = "AGENT_UPGRADE_DISK_SPACE_INSUFFICIENT"
	UpgradeSpaceUnavailable  = "AGENT_UPGRADE_DISK_SPACE_UNAVAILABLE"
)

type UpgradeSpaceCheck struct {
	Scope         string  `json:"scope"`
	Path          string  `json:"path"`
	FreeBytes     *uint64 `json:"free_bytes,omitempty"`
	RequiredBytes uint64  `json:"required_bytes"`
	Error         string  `json:"error,omitempty"`
}

// UpgradeSpaceError is safe to report before the current Agent is stopped.
type UpgradeSpaceError struct {
	Code        string              `json:"error_code"`
	CollectedAt time.Time           `json:"collected_at"`
	Checks      []UpgradeSpaceCheck `json:"checks"`
}

func (failure *UpgradeSpaceError) Error() string {
	details := make([]string, 0, len(failure.Checks))
	for _, check := range failure.Checks {
		if check.Error != "" {
			details = append(details, fmt.Sprintf("%s %q: free space could not be verified (%s)", check.Scope, check.Path, check.Error))
		} else if check.FreeBytes != nil {
			details = append(details, fmt.Sprintf("%s %q: %.1f MiB available; at least %.1f MiB required", check.Scope, check.Path, float64(*check.FreeBytes)/(1024*1024), float64(check.RequiredBytes)/(1024*1024)))
		}
	}
	return fmt.Sprintf("%s: %s. Upgrade deployment was not started", failure.Code, strings.Join(details, "; "))
}

func (failure *UpgradeSpaceError) Result() map[string]any {
	return map[string]any{"phase": "preflight", "error_code": failure.Code, "checks": failure.Checks, "old_agent_unchanged": true}
}

// UpgradeMinimumFreeBytes preserves the installer's MiB override, with a 512 MiB
// safety floor. The byte override carries control-plane policy to detached runs.
func UpgradeMinimumFreeBytes(requested uint64) (uint64, error) {
	minimum := max(DefaultUpgradeMinFreeBytes, requested)
	for _, option := range []struct {
		name       string
		multiplier uint64
	}{
		{"HFL_UPGRADE_MIN_FREE_MB", 1024 * 1024},
		{"HFL_UPGRADE_MIN_FREE_BYTES", 1},
	} {
		raw := strings.TrimSpace(os.Getenv(option.name))
		if raw == "" {
			continue
		}
		value, err := strconv.ParseUint(raw, 10, 64)
		if err != nil || value > ^uint64(0)/option.multiplier {
			return 0, fmt.Errorf("invalid %s: expected a non-negative integer within range", option.name)
		}
		minimum = max(minimum, value*option.multiplier)
	}
	return minimum, nil
}

// CheckUpgradeSpace checks caller-available bytes on the actual upgrade paths.
// Host-wide inventory and usage percentages are deliberately not consulted.
func CheckUpgradeSpace(dataDir, installDir string, minimum uint64) error {
	paths := []UpgradeSpaceCheck{
		{Scope: "agent_data", Path: dataDir},
		{Scope: "agent_install", Path: installDir},
		{Scope: "upgrade_download", Path: RuntimeDownloadDir(dataDir)},
		{Scope: "upgrade_workspace", Path: RuntimeWorkspaceDir(dataDir)},
		{Scope: "upgrade_staging", Path: LifecycleUpgradeDir(dataDir)},
		{Scope: "upgrade_rollback", Path: filepath.Dir(BackupRollbackBinDir(dataDir))},
	}
	// Windows snapshots the database in TEMP before writing the rollback zip.
	if runtime.GOOS == "windows" {
		paths = append(paths, UpgradeSpaceCheck{Scope: "upgrade_snapshot", Path: os.TempDir()})
	}
	return checkUpgradeSpace(paths, max(minimum, DefaultUpgradeMinFreeBytes), agentdisk.Usage)
}

// CheckUpgradeDownloadSpace applies release preparation estimates only to paths
// that hold packages, not an unrelated binary volume or Windows TEMP volume.
func CheckUpgradeDownloadSpace(dataDir string, minimum uint64) error {
	return checkUpgradeSpace([]UpgradeSpaceCheck{
		{Scope: "upgrade_download", Path: RuntimeDownloadDir(dataDir)},
		{Scope: "upgrade_workspace", Path: RuntimeWorkspaceDir(dataDir)},
		{Scope: "upgrade_staging", Path: LifecycleUpgradeDir(dataDir)},
	}, max(minimum, DefaultUpgradeMinFreeBytes), agentdisk.Usage)
}

func checkUpgradeSpace(paths []UpgradeSpaceCheck, minimum uint64, usage func(string) (uint64, uint64, uint64, error)) error {
	failure := &UpgradeSpaceError{CollectedAt: time.Now().UTC()}
	for _, check := range paths {
		check.RequiredBytes = minimum
		path, err := existingUpgradeDirectory(check.Path)
		if err == nil {
			var free uint64
			var total uint64
			total, _, free, err = usage(path)
			if err == nil && (total == 0 || free > total) {
				err = fmt.Errorf("filesystem capacity is unavailable or invalid")
			}
			if err == nil {
				check.FreeBytes = &free
				if free >= minimum {
					continue
				}
				if failure.Code == "" {
					failure.Code = UpgradeSpaceInsufficient
				}
			}
		}
		if err != nil {
			failure.Code = UpgradeSpaceUnavailable
			check.Error = err.Error()
		}
		failure.Checks = append(failure.Checks, check)
	}
	if len(failure.Checks) == 0 {
		return nil
	}
	return failure
}

func existingUpgradeDirectory(path string) (string, error) {
	if !filepath.IsAbs(path) {
		return "", fmt.Errorf("upgrade path must be absolute: %q", path)
	}
	path = filepath.Clean(path)
	for {
		info, err := os.Stat(path)
		if err == nil {
			if !info.IsDir() {
				return "", fmt.Errorf("upgrade path is not a directory: %s", path)
			}
			return path, nil
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		if _, linkErr := os.Lstat(path); linkErr == nil {
			return "", fmt.Errorf("upgrade path is a dangling symlink: %s", path)
		}
		parent := filepath.Dir(path)
		if parent == path {
			return "", err
		}
		path = parent
	}
}

// RecordUpgradeSpaceFailure retains pre-stop diagnostics for the existing detached
// task repair path. Failure to persist is non-fatal; the installer log remains.
func RecordUpgradeSpaceFailure(dataDir string, failure *UpgradeSpaceError) error {
	if !filepath.IsAbs(dataDir) {
		return fmt.Errorf("upgrade data path must be absolute")
	}
	raw, err := json.Marshal(failure)
	if err != nil {
		return err
	}
	dir := LifecycleUpgradeDir(dataDir)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "disk-preflight.json"), raw, 0600)
}

func ReadUpgradeSpaceFailure(dataDir string, since *time.Time) *UpgradeSpaceError {
	if since == nil {
		return nil
	}
	file, err := os.Open(filepath.Join(LifecycleUpgradeDir(dataDir), "disk-preflight.json"))
	if err != nil {
		return nil
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, 64*1024+1))
	if err != nil || len(raw) > 64*1024 {
		return nil
	}
	var failure UpgradeSpaceError
	if json.Unmarshal(raw, &failure) != nil || len(failure.Checks) == 0 ||
		(failure.Code != UpgradeSpaceInsufficient && failure.Code != UpgradeSpaceUnavailable) ||
		failure.CollectedAt.Before(*since) || failure.CollectedAt.After(time.Now().Add(time.Minute)) {
		return nil
	}
	return &failure
}
