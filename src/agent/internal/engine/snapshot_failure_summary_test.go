package engine

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"hyperfilelens/agent/internal/platform/process"
)

func TestSnapshotFailureSummaryKeepsExactCountsAndBoundedSamples(t *testing.T) {
	errorsList := make([]any, 0, 35)
	for i := 0; i < 18; i++ {
		errorsList = append(errorsList, map[string]any{
			"path":  "Library/private",
			"error": "cannot create iterator: operation not permitted",
		})
	}
	for i := 0; i < 17; i++ {
		errorsList = append(errorsList, map[string]any{
			"path":  ".docker/run/docker.sock",
			"error": "unknown or unsupported entry type",
		})
	}
	summary := snapshotFailureSummary(map[string]any{
		"rootEntry": map[string]any{"summ": map[string]any{"errors": errorsList}},
	})
	if summary["total_count"] != 35 || summary["reported_count"] != snapshotFailureSampleLimit {
		t.Fatalf("unexpected totals: %#v", summary)
	}
	counts := summary["cause_counts"].(map[string]int)
	if counts["macos_privacy_denied"] != 18 || counts["unsupported_entry_type"] != 17 {
		t.Fatalf("unexpected cause counts: %#v", counts)
	}
	encoded, err := json.Marshal(summary)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) > 80*1024 {
		t.Fatalf("summary bytes=%d, want bounded diagnostic", len(encoded))
	}
}

func TestSnapshotFailureCollectorSurvivesTruncatedSnapshotJSON(t *testing.T) {
	collector := newSnapshotFailureCollector()
	collector.observe(`Error when processing "Library/Caches/private": cannot create iterator: operation not permitted`)
	collector.observe(`Error when processing ".docker/run/docker.sock": unknown or unsupported entry type`)
	collector.observe(`Found 955 fatal error(s) while snapshotting ghw@mini:/Users/ghw.`)
	summary := collector.summary()
	if summary["total_count"] != 955 {
		t.Fatalf("unexpected total: %#v", summary)
	}
	counts := summary["cause_counts"].(map[string]int)
	if counts["macos_privacy_denied"] != 1 || counts["unsupported_entry_type"] != 1 || counts["snapshot_errors"] != 953 {
		t.Fatalf("unexpected fallback counts: %#v", counts)
	}
}

func TestPreparedSnapshotSendsCompactSummaryWhenStdoutCannotBeParsed(t *testing.T) {
	originalRunner := runManagedSnapshotCommand
	t.Cleanup(func() { runManagedSnapshotCommand = originalRunner })
	runManagedSnapshotCommand = func(
		_ context.Context,
		_ string,
		_ []string,
		_ map[string]string,
		_ string,
		onLine process.OutputLineHandler,
	) (process.Result, error) {
		onLine(`Error when processing "Library/private": cannot create iterator: operation not permitted`, true)
		onLine(`Found 1200 fatal error(s) while snapshotting ghw@mini:/Users/ghw.`, true)
		return process.Result{
			ExitCode:         1,
			Stdout:           `{"rootEntry":{"summ":{"errors":[`,
			Stderr:           "Found 1200 fatal error(s).",
			StdoutTruncated:  true,
			StdoutTotalBytes: 2 * 1024 * 1024,
		}, errors.New("exit status 1")
	}

	status, result, _ := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "large-failure", "kopia", "/tmp/kopia.config",
		nil, "/Users/ghw", map[string]any{},
	)
	if status != "failed" {
		t.Fatalf("status=%q result=%#v", status, result)
	}
	summary := result["snapshot_failure_summary"].(map[string]any)
	if summary["total_count"] != 1200 {
		t.Fatalf("summary=%#v", summary)
	}
	command := result["snapshot_create"].(map[string]any)
	if _, ok := command["stdout"]; ok {
		t.Fatalf("full stdout should not be transported: %#v", command)
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) >= 64*1024 {
		t.Fatalf("result was not compacted: bytes=%d", len(encoded))
	}
}
