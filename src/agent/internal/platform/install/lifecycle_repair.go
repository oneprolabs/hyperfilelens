package install

import (
	"os"
	"path/filepath"
	"strings"
	"time"

	"hyperfilelens/agent/internal/platform/vfs"
)

const (
	lifecycleKindUpgrade   = "agent.upgrade"
	lifecycleKindUninstall = "agent.uninstall"

	upgradeSuccessLogMarkerSH       = "install.sh upgrade succeeded"
	upgradeSuccessLogMarkerPS1      = "install.ps1 upgrade succeeded"
	upgradeSuccessLogMarkerDetached = "Upgrade completed successfully"
	uninstallSuccessLogMarker       = "detached uninstall script finished"
)

var upgradeSuccessLogMarkers = []string{
	upgradeSuccessLogMarkerSH,
	upgradeSuccessLogMarkerPS1,
	upgradeSuccessLogMarkerDetached,
}

// InterruptedLifecycleSucceeded reports whether an orphaned running lifecycle task
// should be treated as success after agent restart (detached upgrade/uninstall).
func InterruptedLifecycleSucceeded(kind string, startedAt *time.Time, dataDir, logDir string) (bool, map[string]any) {
	switch normalizeLifecycleKind(kind) {
	case lifecycleKindUpgrade, lifecycleKindUninstall:
	default:
		return false, nil
	}

	dataDir = strings.TrimSpace(dataDir)
	if dataDir == "" {
		dataDir = vfs.DefaultAgentDataDir()
	}
	logDir = resolveUpgradeLogDir(dataDir, logDir)

	if detachedLifecycleScheduled(kind, dataDir) {
		return false, nil
	}

	switch normalizeLifecycleKind(kind) {
	case lifecycleKindUpgrade:
		if pendingUpgradeFailed(dataDir) {
			return false, nil
		}
		if upgradeLogShowsSuccessAfter(UpgradeLogPath(logDir), startedAt) {
			return true, detachedRepairResult()
		}
	case lifecycleKindUninstall:
		if logShowsSuccessAfter(UninstallLogPath(logDir), uninstallSuccessLogMarker, startedAt) {
			return true, detachedRepairResult()
		}
	}
	return false, nil
}

func detachedRepairResult() map[string]any {
	return map[string]any{
		"mode":                   "local_detached",
		"repaired_after_restart": true,
	}
}

func normalizeLifecycleKind(kind string) string {
	k := strings.TrimSpace(strings.ToLower(kind))
	if i := strings.LastIndex(k, "."); i >= 0 {
		k = k[i+1:]
	}
	switch k {
	case "upgrade":
		return lifecycleKindUpgrade
	case "uninstall":
		return lifecycleKindUninstall
	default:
		return strings.TrimSpace(strings.ToLower(kind))
	}
}

func detachedLifecycleScheduled(kind, dataDir string) bool {
	switch normalizeLifecycleKind(kind) {
	case lifecycleKindUpgrade:
		if pendingUpgradeFailed(dataDir) {
			return false
		}
		pending := LifecycleUpgradeDir(dataDir)
		return fileExists(filepath.Join(pending, pendingUpgradeRunnerName)) ||
			fileExists(filepath.Join(pending, pendingUpgradeRunnerPS1)) ||
			fileExists(filepath.Join(pending, "package.tar.gz")) ||
			fileExists(filepath.Join(pending, "package.zip"))
	case lifecycleKindUninstall:
		return fileExists(filepath.Join(LifecycleUninstallDir(dataDir), pendingUninstallRunnerName))
	default:
		return false
	}
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func pendingUpgradeFailed(dataDir string) bool {
	dataDir = strings.TrimSpace(dataDir)
	if dataDir == "" {
		return false
	}
	return fileExists(LifecycleUpgradeFailedPath(dataDir))
}

// PendingUpgradeFailed reports a failed detached upgrade marker on disk.
func PendingUpgradeFailed(dataDir string) bool {
	return pendingUpgradeFailed(dataDir)
}

// IsLifecycleUpgradeKind identifies an Agent upgrade across legacy aliases.
func IsLifecycleUpgradeKind(kind string) bool {
	return normalizeLifecycleKind(kind) == lifecycleKindUpgrade
}

// PendingUpgradeFailedSince does not attribute a previous attempt's global
// failure marker to a newer accepted execution.
func PendingUpgradeFailedSince(dataDir string, since *time.Time) bool {
	if since == nil || strings.TrimSpace(dataDir) == "" {
		return false
	}
	info, err := os.Lstat(LifecycleUpgradeFailedPath(dataDir))
	return err == nil && info.Mode().IsRegular() && !info.ModTime().Before(*since)
}

// IsLifecycleRepairKind reports whether task repair should defer failure until logs exist.
func IsLifecycleRepairKind(kind string) bool {
	switch normalizeLifecycleKind(kind) {
	case lifecycleKindUpgrade, lifecycleKindUninstall:
		return true
	default:
		return false
	}
}

// InterruptedLifecycleStillRunning reports detached lifecycle work still executing on disk.
func InterruptedLifecycleStillRunning(kind, dataDir string) bool {
	if pendingUpgradeFailed(dataDir) {
		return false
	}
	return detachedLifecycleScheduled(kind, dataDir)
}

func upgradeLogShowsSuccessAfter(logPath string, since *time.Time) bool {
	for _, marker := range upgradeSuccessLogMarkers {
		if logShowsSuccessAfter(logPath, marker, since) {
			return true
		}
	}
	return false
}

func logShowsSuccessAfter(logPath, marker string, since *time.Time) bool {
	if since == nil || strings.TrimSpace(marker) == "" {
		return false
	}
	data, err := os.ReadFile(logPath)
	if err != nil {
		return false
	}
	// Older runners write timestamps at whole-second precision.
	threshold := since.UTC().Truncate(time.Second)
	for line := range strings.SplitSeq(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || !strings.Contains(line, marker) {
			continue
		}
		ts := parseLogLineTimestamp(line)
		if ts != nil && !ts.Before(threshold) {
			return true
		}
	}
	return false
}

func parseLogLineTimestamp(line string) *time.Time {
	line = strings.TrimSpace(line)
	if line == "" {
		return nil
	}
	raw := ""
	if strings.HasPrefix(line, "[") {
		end := strings.IndexByte(line, ']')
		if end > 1 {
			raw = strings.TrimSpace(line[1:end])
		}
	} else {
		space := strings.IndexByte(line, ' ')
		if space <= 0 {
			return nil
		}
		raw = strings.TrimSpace(line[:space])
	}
	if raw == "" {
		return nil
	}
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339} {
		if ts, err := time.Parse(layout, raw); err == nil {
			utc := ts.UTC()
			return &utc
		}
	}
	return nil
}
