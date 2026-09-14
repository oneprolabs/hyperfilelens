package engine

import (
	"context"
	"fmt"
	"math"

	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/process"
)

type restoreScopeTotals struct {
	SizeBytes      int64
	FileCount      int64
	DirectoryCount int64
	SymlinkCount   int64
}

func inspectManagedRestoreEntrySummary(
	ctx context.Context,
	bin string,
	configFile string,
	env map[string]string,
	snapshotID string,
	selectedPath string,
) (kopia.EntrySummary, process.Result, error) {
	result, err := process.Run(
		ctx,
		bin,
		[]string{
			"--config-file=" + configFile,
			"ls",
			"--hfl-summary",
			snapshotObjectPath(snapshotID, selectedPath),
		},
		env,
		"",
	)
	if err != nil {
		return kopia.EntrySummary{}, result, err
	}
	summary, ok := kopia.ParseEntrySummary(result.Stdout)
	if !ok {
		return kopia.EntrySummary{}, result, fmt.Errorf("invalid Kopia entry summary")
	}
	return summary, result, nil
}

func addRestoreEntrySummary(total restoreScopeTotals, summary kopia.EntrySummary) (restoreScopeTotals, bool) {
	if !summary.Complete {
		return total, false
	}
	var ok bool
	if total.SizeBytes, ok = addRestoreSummaryCounter(total.SizeBytes, summary.SizeBytes); !ok {
		return restoreScopeTotals{}, false
	}
	if total.FileCount, ok = addRestoreSummaryCounter(total.FileCount, summary.FileCount); !ok {
		return restoreScopeTotals{}, false
	}
	if total.DirectoryCount, ok = addRestoreSummaryCounter(total.DirectoryCount, summary.DirectoryCount); !ok {
		return restoreScopeTotals{}, false
	}
	if total.SymlinkCount, ok = addRestoreSummaryCounter(total.SymlinkCount, summary.SymlinkCount); !ok {
		return restoreScopeTotals{}, false
	}
	filesAndDirs, ok := addRestoreSummaryCounter(total.FileCount, total.DirectoryCount)
	if !ok {
		return restoreScopeTotals{}, false
	}
	if _, ok = addRestoreSummaryCounter(filesAndDirs, total.SymlinkCount); !ok {
		return restoreScopeTotals{}, false
	}
	return total, true
}

func addRestoreSummaryCounter(current int64, next int64) (int64, bool) {
	if current < 0 || next < 0 || current > math.MaxInt64-next {
		return 0, false
	}
	return current + next, true
}

func (t restoreScopeTotals) totalCount() int64 {
	filesAndDirs, ok := addRestoreSummaryCounter(t.FileCount, t.DirectoryCount)
	if !ok {
		return 0
	}
	total, ok := addRestoreSummaryCounter(filesAndDirs, t.SymlinkCount)
	if !ok {
		return 0
	}
	return total
}

func (t restoreScopeTotals) progressPayload(bytesDone int64, processedCount int64) map[string]any {
	return map[string]any{
		"progress_schema_version": 1,
		"phase":                   "kopia_transfer",
		"kopia_phase":             "restoring",
		"kopia_percent":           0,
		"percent":                 0,
		"bytes_done":              bytesDone,
		"processed_bytes":         bytesDone,
		"bytes_total":             t.SizeBytes,
		"total_bytes":             t.SizeBytes,
		"bytes_total_known":       true,
		"processed_count":         processedCount,
		"file_done":               processedCount,
		"total_count":             t.totalCount(),
		"file_total":              t.totalCount(),
		"total_file_count":        t.FileCount,
		"total_directory_count":   t.DirectoryCount,
		"total_symlink_count":     t.SymlinkCount,
		"totals_source":           "snapshot_summary",
	}
}

func restoreScopeSummaryResult(t restoreScopeTotals) map[string]any {
	return map[string]any{
		"size_bytes":      t.SizeBytes,
		"file_count":      t.FileCount,
		"directory_count": t.DirectoryCount,
		"symlink_count":   t.SymlinkCount,
		"total_count":     t.totalCount(),
		"complete":        true,
		"source":          "snapshot_summary",
	}
}

func int64ValueOrZero(raw any) int64 {
	value, _ := int64Value(raw)
	return value
}

func applyRestoreScopeProgress(
	payload map[string]any,
	totals restoreScopeTotals,
	totalsKnown bool,
	bytesOffset int64,
	countOffset int64,
) {
	bytesDone, _ := addRestoreSummaryCounter(int64ValueOrZero(payload["bytes_done"]), bytesOffset)
	processedCount, _ := addRestoreSummaryCounter(int64ValueOrZero(payload["processed_count"]), countOffset)
	payload["bytes_done"] = bytesDone
	payload["processed_bytes"] = bytesDone
	payload["processed_count"] = processedCount
	payload["file_done"] = processedCount

	if totalsKnown {
		for key, value := range totals.progressPayload(bytesDone, processedCount) {
			if key != "kopia_percent" && key != "percent" && key != "kopia_phase" {
				payload[key] = value
			}
		}
		return
	}

	if pathTotal := int64ValueOrZero(payload["bytes_total"]); pathTotal > 0 {
		if total, ok := addRestoreSummaryCounter(pathTotal, bytesOffset); ok {
			payload["bytes_total"] = total
			payload["total_bytes"] = total
		}
	}
	if pathCount := int64ValueOrZero(payload["total_count"]); pathCount > 0 {
		if total, ok := addRestoreSummaryCounter(pathCount, countOffset); ok {
			payload["total_count"] = total
			payload["file_total"] = total
		}
	}
}
