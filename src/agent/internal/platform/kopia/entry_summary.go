package kopia

import (
	"encoding/json"
	"strings"
)

// EntrySummary is the bounded recursive metadata emitted by the HFL Kopia
// list --hfl-summary operation. DirectoryCount includes the selected directory.
type EntrySummary struct {
	Version           int    `json:"version"`
	PathType          string `json:"path_type"`
	SizeBytes         int64  `json:"size_bytes"`
	FileCount         int64  `json:"file_count"`
	DirectoryCount    int64  `json:"directory_count"`
	SymlinkCount      int64  `json:"symlink_count"`
	SummaryAvailable  bool   `json:"summary_available"`
	Complete          bool   `json:"complete"`
	IncompleteReason  string `json:"incomplete_reason,omitempty"`
	FatalErrorCount   int    `json:"fatal_error_count,omitempty"`
	IgnoredErrorCount int    `json:"ignored_error_count,omitempty"`
}

// ParseEntrySummary validates one HFL Kopia entry-summary response.
func ParseEntrySummary(raw string) (EntrySummary, bool) {
	var summary EntrySummary
	if err := json.Unmarshal([]byte(strings.TrimSpace(raw)), &summary); err != nil {
		return EntrySummary{}, false
	}
	if summary.Version != 1 || summary.SizeBytes < 0 || summary.FileCount < 0 ||
		summary.DirectoryCount < 0 || summary.SymlinkCount < 0 ||
		summary.FatalErrorCount < 0 || summary.IgnoredErrorCount < 0 {
		return EntrySummary{}, false
	}
	switch summary.PathType {
	case "directory":
		if summary.Complete && !summary.SummaryAvailable {
			return EntrySummary{}, false
		}
	case "file":
		if summary.FileCount != 1 || summary.DirectoryCount != 0 || summary.SymlinkCount != 0 {
			return EntrySummary{}, false
		}
	case "symlink":
		if summary.SizeBytes != 0 || summary.FileCount != 0 || summary.DirectoryCount != 0 || summary.SymlinkCount != 1 {
			return EntrySummary{}, false
		}
	case "unsupported":
		if summary.Complete {
			return EntrySummary{}, false
		}
	default:
		return EntrySummary{}, false
	}
	return summary, true
}

// TotalCount matches Kopia restore's file, directory, and symlink item count.
func (s EntrySummary) TotalCount() int64 {
	return s.FileCount + s.DirectoryCount + s.SymlinkCount
}
