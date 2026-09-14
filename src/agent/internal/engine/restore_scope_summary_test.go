package engine

import (
	"math"
	"testing"

	"hyperfilelens/agent/internal/platform/kopia"
)

func TestAddRestoreEntrySummary(t *testing.T) {
	total, ok := addRestoreEntrySummary(restoreScopeTotals{}, kopia.EntrySummary{
		Complete:       true,
		SizeBytes:      100,
		FileCount:      3,
		DirectoryCount: 2,
		SymlinkCount:   1,
	})
	if !ok {
		t.Fatal("expected complete summary to aggregate")
	}
	total, ok = addRestoreEntrySummary(total, kopia.EntrySummary{
		Complete:       true,
		SizeBytes:      20,
		FileCount:      1,
		DirectoryCount: 1,
	})
	if !ok || total.SizeBytes != 120 || total.FileCount != 4 || total.DirectoryCount != 3 || total.SymlinkCount != 1 {
		t.Fatalf("unexpected aggregate: %#v, ok=%v", total, ok)
	}
	if total.totalCount() != 8 {
		t.Fatalf("totalCount() = %d, want 8", total.totalCount())
	}
	payload := total.progressPayload(12, 2)
	if payload["bytes_total"] != int64(120) || payload["total_count"] != int64(8) || payload["totals_source"] != "snapshot_summary" {
		t.Fatalf("unexpected progress payload: %#v", payload)
	}
}

func TestAddRestoreEntrySummaryRejectsIncompleteAndOverflow(t *testing.T) {
	if _, ok := addRestoreEntrySummary(restoreScopeTotals{}, kopia.EntrySummary{Complete: false}); ok {
		t.Fatal("expected incomplete summary to remain unknown")
	}
	if _, ok := addRestoreEntrySummary(
		restoreScopeTotals{SizeBytes: math.MaxInt64},
		kopia.EntrySummary{Complete: true, SizeBytes: 1},
	); ok {
		t.Fatal("expected overflowing summary to remain unknown")
	}
}

func TestRestoreSelectedPathsDeduplicatesCoveredDescendants(t *testing.T) {
	payload := Payload{Extra: map[string]any{
		"selected_paths": []any{"docs/reports", "docs", "images", "images"},
	}}
	paths := restoreSelectedPaths(payload)
	if len(paths) != 2 || paths[0] != "docs" || paths[1] != "images" {
		t.Fatalf("restoreSelectedPaths() = %#v, want [docs images]", paths)
	}

	payload.Extra["selected_paths"] = []any{"docs", ""}
	paths = restoreSelectedPaths(payload)
	if len(paths) != 1 || paths[0] != "" {
		t.Fatalf("root selection must cover descendants, got %#v", paths)
	}
}

func TestApplyRestoreScopeProgressUsesAggregateSummary(t *testing.T) {
	payload := map[string]any{
		"kopia_phase":     "restoring",
		"kopia_percent":   50,
		"bytes_done":      int64(20),
		"bytes_total":     int64(40),
		"processed_count": int64(2),
		"total_count":     int64(4),
	}
	applyRestoreScopeProgress(payload, restoreScopeTotals{
		SizeBytes:      150,
		FileCount:      5,
		DirectoryCount: 2,
		SymlinkCount:   1,
	}, true, 50, 3)

	if payload["bytes_done"] != int64(70) || payload["processed_bytes"] != int64(70) {
		t.Fatalf("unexpected byte progress: %#v", payload)
	}
	if payload["bytes_total"] != int64(150) || payload["processed_count"] != int64(5) || payload["total_count"] != int64(8) {
		t.Fatalf("unexpected scoped totals: %#v", payload)
	}
	if payload["kopia_percent"] != 50 {
		t.Fatalf("Kopia percent must remain live: %#v", payload)
	}
}
