package engine

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const maintenanceSummarySchemaVersion = 1

var (
	maintenanceGCLine        = regexp.MustCompile(`(?i)^GC found (\d+) (unused contents(?: that are too recent to delete)?|in-use contents|in-use system-contents) \(([^)]+)\)$`)
	maintenanceUndeletedLine = regexp.MustCompile(`(?i)^GC undeleted (\d+) contents \(([^)]+)\)$`)
	maintenanceSize          = regexp.MustCompile(`(?i)^(\d+(?:\.\d+)?)\s*([KMGTPE]?I?B)$`)
)

type maintenanceInfoPayload struct {
	Schedule struct {
		Runs map[string][]maintenanceRun `json:"runs"`
	} `json:"schedule"`
}

type maintenanceRun struct {
	Start   time.Time             `json:"start"`
	Success bool                  `json:"success"`
	Extra   []maintenanceRunExtra `json:"extra"`
}

type maintenanceRunExtra struct {
	Kind string          `json:"kind"`
	Data json.RawMessage `json:"data"`
}

type snapshotGCStats struct {
	UnreferencedContentCount       uint64 `json:"unreferencedContentCount"`
	UnreferencedContentSize        uint64 `json:"unreferencedContentSize"`
	DeletedContentCount            uint64 `json:"deletedContentCount"`
	DeletedContentSize             uint64 `json:"deletedContentSize"`
	UnreferencedRecentContentCount uint64 `json:"unreferencedRecentContentCount"`
	UnreferencedRecentContentSize  uint64 `json:"unreferencedRecentContentSize"`
	InUseContentCount              uint64 `json:"inUseContentCount"`
	InUseContentSize               uint64 `json:"inUseContentSize"`
	InUseSystemContentCount        uint64 `json:"inUseSystemContentCount"`
	InUseSystemContentSize         uint64 `json:"inUseSystemContentSize"`
	RecoveredContentCount          uint64 `json:"recoveredContentCount"`
	RecoveredContentSize           uint64 `json:"recoveredContentSize"`
}

type deleteUnreferencedPacksStats struct {
	UnreferencedPackCount uint64 `json:"unreferencedPackCount"`
	UnreferencedTotalSize uint64 `json:"unreferencedTotalSize"`
	DeletedPackCount      uint64 `json:"deletedPackCount"`
	DeletedTotalSize      uint64 `json:"deletedTotalSize"`
	RetainedPackCount     uint64 `json:"retainedPackCount"`
	RetainedTotalSize     uint64 `json:"retainedTotalSize"`
}

func currentMaintenanceRun(runs []maintenanceRun, startedAt time.Time) *maintenanceRun {
	lowerBound := startedAt.Add(-2 * time.Second)
	var selected *maintenanceRun
	for i := range runs {
		run := &runs[i]
		if !run.Success || run.Start.Before(lowerBound) {
			continue
		}
		if selected == nil || run.Start.After(selected.Start) {
			selected = run
		}
	}
	return selected
}

func maintenanceRunData(run *maintenanceRun, kind string, out any) bool {
	if run == nil {
		return false
	}
	for _, extra := range run.Extra {
		if extra.Kind != kind || len(extra.Data) == 0 || string(extra.Data) == "null" {
			continue
		}
		return json.Unmarshal(extra.Data, out) == nil
	}
	return false
}

func maintenanceSummaryFromInfo(stdout, mode string, startedAt time.Time) map[string]any {
	var info maintenanceInfoPayload
	if json.Unmarshal([]byte(stdout), &info) != nil {
		return nil
	}

	var content map[string]any
	var contentStats snapshotGCStats
	if maintenanceRunData(
		currentMaintenanceRun(info.Schedule.Runs["snapshot-gc"], startedAt),
		"snapshotGCStats",
		&contentStats,
	) {
		content = map[string]any{
			"unused_count":        contentStats.UnreferencedContentCount,
			"unused_bytes":        contentStats.UnreferencedContentSize,
			"deleted_count":       contentStats.DeletedContentCount,
			"deleted_bytes":       contentStats.DeletedContentSize,
			"deferred_count":      contentStats.UnreferencedRecentContentCount,
			"deferred_bytes":      contentStats.UnreferencedRecentContentSize,
			"in_use_count":        contentStats.InUseContentCount,
			"in_use_bytes":        contentStats.InUseContentSize,
			"in_use_system_count": contentStats.InUseSystemContentCount,
			"in_use_system_bytes": contentStats.InUseSystemContentSize,
			"recovered_count":     contentStats.RecoveredContentCount,
			"recovered_bytes":     contentStats.RecoveredContentSize,
		}
	}

	packTaskNames := []string{"full-delete-blobs", "quick-delete-blobs"}
	if mode == "quick" {
		packTaskNames[0], packTaskNames[1] = packTaskNames[1], packTaskNames[0]
	}
	var packs map[string]any
	for _, taskName := range packTaskNames {
		var packStats deleteUnreferencedPacksStats
		if !maintenanceRunData(
			currentMaintenanceRun(info.Schedule.Runs[taskName], startedAt),
			"deleteUnreferencedPacksStats",
			&packStats,
		) {
			continue
		}
		packs = map[string]any{
			"unreferenced_count": packStats.UnreferencedPackCount,
			"unreferenced_bytes": packStats.UnreferencedTotalSize,
			"deleted_count":      packStats.DeletedPackCount,
			"deleted_bytes":      packStats.DeletedTotalSize,
			"retained_count":     packStats.RetainedPackCount,
			"retained_bytes":     packStats.RetainedTotalSize,
		}
		break
	}

	if content == nil && packs == nil {
		return nil
	}
	return map[string]any{
		"schema_version": maintenanceSummarySchemaVersion,
		"mode":           mode,
		"source":         "maintenance_info",
		"approximate":    false,
		"content_gc":     content,
		"pack_gc":        packs,
	}
}

func parseMaintenanceSize(raw string) (uint64, bool) {
	match := maintenanceSize.FindStringSubmatch(strings.TrimSpace(raw))
	if len(match) != 3 {
		return 0, false
	}
	value, err := strconv.ParseFloat(match[1], 64)
	if err != nil || value < 0 {
		return 0, false
	}
	factors := map[string]uint64{
		"B": 1, "KB": 1 << 10, "KIB": 1 << 10,
		"MB": 1 << 20, "MIB": 1 << 20,
		"GB": 1 << 30, "GIB": 1 << 30,
		"TB": 1 << 40, "TIB": 1 << 40,
		"PB": 1 << 50, "PIB": 1 << 50,
		"EB": 1 << 60, "EIB": 1 << 60,
	}
	factor, ok := factors[strings.ToUpper(match[2])]
	if !ok {
		return 0, false
	}
	return uint64(value * float64(factor)), true
}

func maintenanceSummaryFromStderr(stderr, mode string) map[string]any {
	content := map[string]any{}
	for _, rawLine := range strings.Split(stderr, "\n") {
		line := strings.TrimSpace(rawLine)
		if match := maintenanceGCLine.FindStringSubmatch(line); len(match) == 4 {
			count, countErr := strconv.ParseUint(match[1], 10, 64)
			size, sizeOK := parseMaintenanceSize(match[3])
			if countErr != nil || !sizeOK {
				continue
			}
			switch strings.ToLower(match[2]) {
			case "unused contents":
				content["unused_count"] = count
				content["unused_bytes"] = size
				content["deleted_count"] = count
				content["deleted_bytes"] = size
			case "unused contents that are too recent to delete":
				content["deferred_count"] = count
				content["deferred_bytes"] = size
			case "in-use contents":
				content["in_use_count"] = count
				content["in_use_bytes"] = size
			case "in-use system-contents":
				content["in_use_system_count"] = count
				content["in_use_system_bytes"] = size
			}
			continue
		}
		if match := maintenanceUndeletedLine.FindStringSubmatch(line); len(match) == 3 {
			count, countErr := strconv.ParseUint(match[1], 10, 64)
			size, sizeOK := parseMaintenanceSize(match[2])
			if countErr == nil && sizeOK {
				content["recovered_count"] = count
				content["recovered_bytes"] = size
			}
		}
	}
	if len(content) == 0 {
		return nil
	}
	return map[string]any{
		"schema_version": maintenanceSummarySchemaVersion,
		"mode":           mode,
		"source":         "stderr",
		"approximate":    true,
		"content_gc":     content,
		"pack_gc":        nil,
	}
}

func buildMaintenanceSummary(infoStdout, stderr, mode string, startedAt time.Time) map[string]any {
	if summary := maintenanceSummaryFromInfo(infoStdout, mode, startedAt); summary != nil {
		return summary
	}
	return maintenanceSummaryFromStderr(stderr, mode)
}
