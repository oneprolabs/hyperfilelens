package engine

import (
	"context"
	"errors"
	"testing"
	"time"

	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/process"
)

type backupMetricsSink struct{ samples []map[string]any }

func (s *backupMetricsSink) OnProgress(_ context.Context, p map[string]any) error {
	s.samples = append(s.samples, p)
	return nil
}

func TestBackupMetricsCoalesceCountersAndSequenceForFiveSeconds(t *testing.T) {
	sink := &backupMetricsSink{}
	rep := ReporterSink{Sink: sink}
	r := newKopiaProgressReporter()
	r.maybeSend(t.Context(), rep, "task", kopia.ProgressSnapshot{SchemaVersion: 2, Sequence: 1, Phase: "processing", ProcessedBytes: 1})
	r.sendLatest(t.Context(), rep, "task", false, time.Now())
	for i := int64(2); i <= 100; i++ {
		r.maybeSend(t.Context(), rep, "task", kopia.ProgressSnapshot{SchemaVersion: 2, Sequence: i, Phase: "processing", ProcessedBytes: i})
	}
	if len(sink.samples) != 1 {
		t.Fatalf("ordinary updates bypassed rate limit: %d", len(sink.samples))
	}
	r.sendLatest(t.Context(), rep, "task", false, r.lastSentAt.Add(5*time.Second))
	if len(sink.samples) != 2 || sink.samples[1]["processed_bytes"] != int64(100) {
		t.Fatalf("latest sample not drained: %#v", sink.samples)
	}
	r.maybeSend(t.Context(), rep, "task", kopia.ProgressSnapshot{SchemaVersion: 2, Sequence: 101, Phase: "finalizing", ProcessedBytes: 100})
	r.sendLatest(t.Context(), rep, "task", false, time.Now())
	if len(sink.samples) != 3 {
		t.Fatal("real phase transition was delayed")
	}
	final := r.completionPayload("snapshot-one")
	if final["progress_sequence"] != int64(102) || final["kopia_phase"] != "snapshot_created" {
		t.Fatalf("completion would be discarded as a duplicate sample: %#v", final)
	}
}

func TestBackupMetricsEmptyEntriesCountAsWorkNotSequence(t *testing.T) {
	r := newKopiaProgressReporter()
	zero := int64(0)
	p := kopia.ProgressSnapshot{Phase: "processing", ProcessedEntryCount: &zero}
	r.noteSubstantive(p)
	old := time.Now().Add(-time.Hour)
	r.lastSubstantiveAt = old
	p.Sequence = 100
	r.noteSubstantive(p)
	if !r.lastSubstantiveAt.Equal(old) {
		t.Fatal("sequence alone renewed substantive progress")
	}
	one := int64(1)
	p.ProcessedEntryCount = &one
	r.noteSubstantive(p)
	if r.lastSubstantiveAt.Equal(old) {
		t.Fatal("empty entry processing did not renew substantive progress")
	}
}

func TestBackupCreateMetricsUseManifestSummaryAndPersistedBasis(t *testing.T) {
	result := parseSnapshotOutput(`{"id":"one","stats":{"totalSize":999,"fileCount":4},"rootEntry":{"summ":{"size":42,"files":3,"dirs":2,"symlinks":1}},"hflCreateStats":{"version":1,"basis":"creation_session_data_v1","originalBytes":0,"packedBytes":0}}`)
	if result["size_bytes"] != int64(42) || result["file_count"] != int64(3) || result["storage_stats_available"] != true || result["new_packed_content_bytes"] != int64(0) {
		t.Fatalf("wrong manifest projection: %#v", result)
	}
	for _, raw := range []string{`{"hflCreateStats":{"version":2,"basis":"creation_session_data_v1","originalBytes":3,"packedBytes":2}}`, `{"hflCreateStats":{"version":1,"basis":"creation_session_data_v1","originalBytes":-1,"packedBytes":0}}`, `{"id":"legacy"}`} {
		if parseSnapshotOutput(raw)["storage_stats_available"] != false {
			t.Fatal("invalid/legacy metrics treated as available")
		}
	}
}

func TestPreparedSnapshotDoesNotConfirmMetricsAfterFlushFailure(t *testing.T) {
	original := runManagedSnapshotCommand
	t.Cleanup(func() { runManagedSnapshotCommand = original })
	runManagedSnapshotCommand = func(context.Context, string, []string, map[string]string, string, process.OutputLineHandler) (process.Result, error) {
		return process.Result{ExitCode: 1, Stdout: `{"id":"provisional","rootEntry":{"summ":{"size":5,"files":1,"dirs":1}},"hflCreateStats":{"version":1,"basis":"creation_session_data_v1","originalBytes":5,"packedBytes":3}}`}, errors.New("flush error")
	}
	status, result, _ := runPreparedManagedSnapshot(t.Context(), ReporterSink{}, "failed", "kopia", "/tmp/test.config", nil, "/data", map[string]any{})
	if status != "failed" || result["storage_stats_available"] != false {
		t.Fatalf("provisional metrics confirmed: %#v", result)
	}
	if _, known := result["new_packed_content_bytes"]; known {
		t.Fatal("failed write exposed confirmed packed size")
	}
}
