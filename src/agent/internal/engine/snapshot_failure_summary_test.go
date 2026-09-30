package engine

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
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

func TestSnapshotFailureCollectorClassifiesDeviceOrResourceBusyWithPath(t *testing.T) {
	collector := newSnapshotFailureCollector()
	collector.observe(`Error when processing "pagefile.sys": stat: device or resource busy`)
	summary := collector.summary()
	if summary["cause_counts"].(map[string]int)["source_resource_busy"] != 1 {
		t.Fatalf("unexpected causes: %#v", summary)
	}
	item := summary["items"].([]any)[0].(map[string]any)
	if item["path"] != "pagefile.sys" || item["cause"] != "source_resource_busy" {
		t.Fatalf("unexpected item: %#v", item)
	}
}

func TestSnapshotFailureCollectorSeparatesIgnoredEntries(t *testing.T) {
	collector := newSnapshotFailureCollector()
	collector.observe(`! Ignored error when processing "Documents and Settings": readdirent: permission denied`)
	summary := collector.summary()
	if summary["ignored_count"] != 1 || summary["fatal_count"] != 0 {
		t.Fatalf("unexpected disposition counts: %#v", summary)
	}
	item := summary["items"].([]any)[0].(map[string]any)
	if item["disposition"] != "skipped" {
		t.Fatalf("unexpected disposition: %#v", item)
	}
}

func TestTerminalSnapshotDiagnosticKeepsContextAroundTerminalError(t *testing.T) {
	lines := make([]string, 0, 120)
	for i := 0; i < 120; i++ {
		lines = append(lines, `{"type":"hfl_snapshot_progress","sequence":1}`)
	}
	lines[80] = "source read failed: permission denied"
	lines[81] = "upload error: device or resource busy"
	diagnostic := terminalSnapshotDiagnostic(strings.Join(lines, "\n"))
	if !strings.Contains(diagnostic, "upload error: device or resource busy") {
		t.Fatalf("terminal error missing from diagnostic: %q", diagnostic)
	}
	if !strings.Contains(diagnostic, "source read failed: permission denied") {
		t.Fatalf("failure context missing from diagnostic: %q", diagnostic)
	}
	if strings.Contains(diagnostic, "hfl_snapshot_progress") {
		t.Fatalf("progress JSON should be filtered: %q", diagnostic)
	}
	if got := len(strings.Split(diagnostic, "\n")); got > 10 {
		t.Fatalf("diagnostic lines=%d, want <= 10", got)
	}
}

func TestTerminalSnapshotSourcePathExtractsReaddirentPath(t *testing.T) {
	path, phase := terminalSnapshotSourcePath(
		"upload error: readdirent /opt/source/Documents and Settings: device or resource busy",
	)
	if path != "/opt/source/Documents and Settings" || phase != "directory_enumeration" {
		t.Fatalf("unexpected source context path=%q phase=%q", path, phase)
	}
}

func TestTerminalSnapshotSourcePathExtractsLstatPath(t *testing.T) {
	path, phase := terminalSnapshotSourcePath(
		"upload error: lstat /opt/source/DumpStack.log.tmp: device or resource busy",
	)
	if path != "/opt/source/DumpStack.log.tmp" || phase != "metadata_lookup" {
		t.Fatalf("unexpected source context path=%q phase=%q", path, phase)
	}
}

func TestSnapshotFailureSummaryUsesDirectoryDispositionCounts(t *testing.T) {
	summary := snapshotFailureSummary(map[string]any{
		"rootEntry": map[string]any{"summ": map[string]any{
			"numFailed":        float64(0),
			"numIgnoredErrors": float64(1),
			"errors": []any{map[string]any{
				"path":  "Documents and Settings",
				"error": "readdirent: permission denied",
			}},
		}},
	})
	item := summary["items"].([]any)[0].(map[string]any)
	if item["disposition"] != "skipped" {
		t.Fatalf("unexpected disposition: %#v", item)
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

func TestPreparedSnapshotSeparatesSkippedEntriesFromTerminalFailure(t *testing.T) {
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
		onLine(`! Ignored error when processing "Documents and Settings": readdirent: permission denied`, true)
		return process.Result{
			ExitCode: 1,
			Stdout:   `{"rootEntry":{"summ":{"numFailed":0,"numIgnoredErrors":1,"errors":[{"path":"Documents and Settings","error":"readdirent: permission denied"}]}}}`,
			Stderr:   "upload error: device or resource busy",
		}, errors.New("exit status 1")
	}

	status, result, _ := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "mixed-failure", "kopia", "/tmp/kopia.config",
		nil, "/Users/ghw", map[string]any{},
	)
	if status != "failed" {
		t.Fatalf("status=%q result=%#v", status, result)
	}
	skipped, ok := result["snapshot_skipped_summary"].(map[string]any)
	if !ok || skipped["ignored_count"] != 1 {
		t.Fatalf("missing skipped summary: %#v", result)
	}
	if got := result["snapshot_terminal_error"]; got != "upload error: device or resource busy" {
		t.Fatalf("unexpected terminal error: %#v", got)
	}
	summary, ok := result["snapshot_failure_summary"].(map[string]any)
	if !ok {
		t.Fatalf("missing failure summary: %#v", result)
	}
	item := summary["items"].([]any)[0].(map[string]any)
	if item["disposition"] != "skipped" {
		t.Fatalf("unexpected item disposition: %#v", item)
	}
}
