package engine

import (
	"context"
	"errors"
	"testing"
	"time"

	"hyperfilelens/agent/internal/platform/process"
)

func TestRepositorySessionLockAllowsParallelSnapshotsAndBlocksPolicyWriter(t *testing.T) {
	lock := newRepositorySessionLock()
	releaseFirst, err := lock.acquireRead(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	releaseSecond, err := lock.acquireRead(t.Context())
	if err != nil {
		t.Fatal(err)
	}

	writerAcquired := make(chan func(), 1)
	go func() {
		release, acquireErr := lock.acquireWrite(context.Background())
		if acquireErr == nil {
			writerAcquired <- release
		}
	}()

	select {
	case release := <-writerAcquired:
		release()
		t.Fatal("policy writer acquired while prepared snapshots were active")
	case <-time.After(50 * time.Millisecond):
	}
	releaseFirst()
	select {
	case release := <-writerAcquired:
		release()
		t.Fatal("policy writer acquired before all prepared snapshots completed")
	case <-time.After(50 * time.Millisecond):
	}
	releaseSecond()
	select {
	case release := <-writerAcquired:
		release()
	case <-time.After(time.Second):
		t.Fatal("policy writer did not acquire after prepared snapshots completed")
	}
}

func TestRepositorySessionWriterPreventsNewSnapshotReaders(t *testing.T) {
	lock := newRepositorySessionLock()
	releaseWriter, err := lock.acquireWrite(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	readerAcquired := make(chan func(), 1)
	go func() {
		release, acquireErr := lock.acquireRead(context.Background())
		if acquireErr == nil {
			readerAcquired <- release
		}
	}()
	select {
	case release := <-readerAcquired:
		release()
		t.Fatal("prepared snapshot reader acquired while policy writer was active")
	case <-time.After(50 * time.Millisecond):
	}
	releaseWriter()
	select {
	case release := <-readerAcquired:
		release()
	case <-time.After(time.Second):
		t.Fatal("prepared snapshot reader did not acquire after policy writer completed")
	}
}

func TestPreparedSnapshotReturnsPolicyNotFoundToBackendWithoutRetry(t *testing.T) {
	originalRunner := runManagedSnapshotCommand
	t.Cleanup(func() {
		runManagedSnapshotCommand = originalRunner
	})
	calls := 0
	runManagedSnapshotCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		calls++
		return process.Result{
			ExitCode: 1,
			Stderr:   "unable to get policy tree: unable to get policies: policy not found",
		}, errors.New("exit status 1")
	}

	status, result, message := runPreparedManagedSnapshot(
		t.Context(),
		ReporterSink{},
		"prepared-policy-retry",
		"kopia",
		"/tmp/prepared-policy-retry.config",
		nil,
		"/data",
		map[string]any{},
	)

	if status != "failed" || message != "kopia policy not found" {
		t.Fatalf("unexpected result status=%q message=%q payload=%#v", status, message, result)
	}
	if calls != 1 {
		t.Fatalf("snapshot calls=%d, want 1", calls)
	}
	if result["error_code"] != "KOPIA_POLICY_NOT_FOUND" || result["policy_phase"] != "snapshot_create" {
		t.Fatalf("unexpected structured policy error: %#v", result)
	}
}

func TestPreparedSnapshotKeepsSingleBoundedCommandSummary(t *testing.T) {
	originalRunner := runManagedSnapshotCommand
	t.Cleanup(func() { runManagedSnapshotCommand = originalRunner })
	runManagedSnapshotCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		return process.Result{
			Stdout:           `{"id":"snapshot-bounded","stats":{"totalSize":42}}`,
			Stderr:           "latest progress",
			StdoutTotalBytes: 512 * 1024,
			StderrTotalBytes: 2 * 1024 * 1024,
			StdoutTruncated:  true,
			StderrTruncated:  true,
		}, nil
	}

	status, result, message := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "prepared-bounded", "kopia", "/tmp/bounded.config",
		nil, "/data", map[string]any{},
	)
	if status != "success" || message != "" {
		t.Fatalf("status=%q message=%q result=%#v", status, message, result)
	}
	if result["kopia_snapshot_id"] != "snapshot-bounded" {
		t.Fatalf("snapshot identity missing: %#v", result)
	}
	if _, duplicated := result["stdout"]; duplicated {
		t.Fatalf("command stdout duplicated at top level: %#v", result)
	}
	command, ok := result["snapshot_create"].(map[string]any)
	if !ok || command["stderr"] != "latest progress" {
		t.Fatalf("bounded command summary missing: %#v", result)
	}
	if command["stdout_total_bytes"] != int64(512*1024) || command["stderr_truncated"] != true {
		t.Fatalf("output bounds metadata missing: %#v", command)
	}
}
