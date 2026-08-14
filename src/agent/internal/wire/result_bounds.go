package wire

import (
	"encoding/json"
	"log/slog"
	"sort"
	"unicode/utf8"
)

const (
	maxTaskResultFrameBytes = 256 * 1024
	// Reserve space for the task.result envelope, task ID, status, and bounded error.
	maxTaskResultBytes   = maxTaskResultFrameBytes - 16*1024
	maxResultStringBytes = 8 * 1024
)

type resultBoundStats struct {
	OriginalBytes int
	FinalBytes    int
	Truncated     bool
}

var essentialResultKeys = map[string]struct{}{
	"error_code": {}, "phase": {}, "policy_phase": {}, "status": {},
	"kopia_snapshot_id": {}, "snapshot_id": {}, "source_path": {},
	"size_bytes": {}, "file_count": {}, "dir_count": {}, "directory_count": {},
	"bytes_done": {}, "bytes_total": {}, "bytes_total_known": {},
	"hashed_bytes": {}, "uploaded_bytes": {}, "kopia_percent": {}, "percent": {},
	"last_progress": {}, "execution_state": {}, "result_reported": {},
	"path": {}, "target_path": {}, "filename": {}, "content_type": {},
	"repository_path": {}, "repository_type": {}, "storage_type": {},
	"session_id": {}, "url": {}, "count": {}, "has_more": {},
	"failed_count": {}, "created": {}, "deleted_count": {},
	"selected_paths": {}, "stats": {}, "restore": {}, "restore_results": {},
	"results": {}, "entries": {}, "snapshot_browse": {}, "snapshot_download": {},
}

func boundTaskResult(result map[string]any) (map[string]any, resultBoundStats) {
	if result == nil {
		result = map[string]any{}
	}
	originalBytes := jsonSize(result)
	if originalBytes <= maxTaskResultBytes {
		return result, resultBoundStats{OriginalBytes: originalBytes, FinalBytes: originalBytes}
	}

	trimmed, _ := stripCommandOutput(result).(map[string]any)
	if trimmed == nil {
		trimmed = map[string]any{}
	}
	trimmed["result_truncated"] = true
	trimmed["result_original_bytes"] = originalBytes
	if finalBytes := jsonSize(trimmed); finalBytes <= maxTaskResultBytes {
		return trimmed, resultBoundStats{
			OriginalBytes: originalBytes,
			FinalBytes:    finalBytes,
			Truncated:     true,
		}
	}

	compact := map[string]any{
		"result_truncated":      true,
		"result_original_bytes": originalBytes,
	}
	for key := range essentialResultKeys {
		if value, ok := result[key]; ok {
			compact[key] = compactResultValue(value, 0)
		}
	}
	finalBytes := jsonSize(compact)
	if finalBytes > maxTaskResultBytes {
		compact = map[string]any{
			"result_truncated":      true,
			"result_original_bytes": originalBytes,
		}
		for key := range essentialResultKeys {
			value, ok := result[key]
			if !ok {
				continue
			}
			switch value.(type) {
			case string, bool, float64, float32, int, int8, int16, int32, int64,
				uint, uint8, uint16, uint32, uint64, nil:
				compact[key] = compactResultValue(value, 0)
			}
		}
		finalBytes = jsonSize(compact)
	}
	return compact, resultBoundStats{
		OriginalBytes: originalBytes,
		FinalBytes:    finalBytes,
		Truncated:     true,
	}
}

func resultBoundLog(taskID string, stats resultBoundStats) {
	slog.Warn(
		"task.result compacted",
		"task_id", taskID,
		"original_bytes", stats.OriginalBytes,
		"final_bytes", stats.FinalBytes,
		"max_bytes", maxTaskResultBytes,
	)
}

func stripCommandOutput(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		out := make(map[string]any, len(typed))
		for key, child := range typed {
			if key == "stdout" || key == "stderr" {
				continue
			}
			out[key] = stripCommandOutput(child)
		}
		return out
	case []any:
		out := make([]any, len(typed))
		for i, child := range typed {
			out[i] = stripCommandOutput(child)
		}
		return out
	default:
		return value
	}
}

func compactResultValue(value any, depth int) any {
	if depth >= 3 {
		return nil
	}
	switch typed := value.(type) {
	case string:
		return truncateUTF8(typed, maxResultStringBytes)
	case bool, float64, float32, int, int8, int16, int32, int64,
		uint, uint8, uint16, uint32, uint64, nil:
		return value
	case map[string]any:
		out := make(map[string]any, min(len(typed), 32))
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys[:min(len(keys), 32)] {
			child := typed[key]
			out[key] = compactResultValue(child, depth+1)
		}
		return out
	case []any:
		limit := min(len(typed), 32)
		out := make([]any, 0, limit)
		for _, child := range typed[:limit] {
			out = append(out, compactResultValue(child, depth+1))
		}
		return out
	default:
		return truncateUTF8(stringJSON(value), maxResultStringBytes)
	}
}

func truncateUTF8(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	value = value[:limit]
	for !utf8.ValidString(value) && len(value) > 0 {
		value = value[:len(value)-1]
	}
	return value
}

func jsonSize(value any) int {
	encoded, err := json.Marshal(value)
	if err != nil {
		return maxTaskResultBytes + 1
	}
	return len(encoded)
}

func stringJSON(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	return string(encoded)
}
