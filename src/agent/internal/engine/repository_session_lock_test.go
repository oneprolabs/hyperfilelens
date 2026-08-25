package engine

import (
	"context"
	"errors"
	"slices"
	"strings"
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
		map[string]any{}, false,
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
	originalStatsRunner := runManagedSnapshotStorageStatsCommand
	t.Cleanup(func() {
		runManagedSnapshotCommand = originalRunner
		runManagedSnapshotStorageStatsCommand = originalStatsRunner
	})
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
	runManagedSnapshotStorageStatsCommand = func(
		_ context.Context,
		_ string,
		_ []string,
		_ map[string]string,
		_ string,
		onLine process.OutputLineHandler,
	) (process.Result, error) {
		onLine(` {"id":"snapshot-bounded","rootEntry":{"summ":{"size":42,"files":2,"dirs":1,"symlinks":0}},"storageStats":{"newData":{"originalContentBytes":21,"packedContentBytes":7}}}`, false)
		return process.Result{}, nil
	}

	status, result, message := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "prepared-bounded", "kopia", "/tmp/bounded.config",
		nil, "/data", map[string]any{}, false,
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
	if result["storage_stats_available"] != true || result["recoverable_size_bytes"] != int64(42) {
		t.Fatalf("storage statistics missing: %#v", result)
	}
	if result["new_original_content_bytes"] != int64(21) || result["new_packed_content_bytes"] != int64(7) {
		t.Fatalf("storage byte counters missing: %#v", result)
	}
}

func TestPreparedSnapshotKeepsSuccessWhenStorageStatsFail(t *testing.T) {
	originalRunner := runManagedSnapshotCommand
	originalStatsRunner := runManagedSnapshotStorageStatsCommand
	t.Cleanup(func() {
		runManagedSnapshotCommand = originalRunner
		runManagedSnapshotStorageStatsCommand = originalStatsRunner
	})
	runManagedSnapshotCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		return process.Result{Stdout: `{"id":"snapshot-created","rootEntry":{"summ":{"size":42}}}`}, nil
	}
	runManagedSnapshotStorageStatsCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		return process.Result{Stderr: "storage statistics unavailable"}, errors.New("exit status 1")
	}

	status, result, message := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "prepared-reference-failure", "kopia", "/tmp/reference.config",
		nil, "/data", map[string]any{}, false,
	)
	if status != "success" || message != "" {
		t.Fatalf("status=%q message=%q result=%#v", status, message, result)
	}
	if result["storage_stats_available"] != false {
		t.Fatalf("expected unavailable reference metrics: %#v", result)
	}
	if result["storage_stats_error"] != "storage statistics unavailable" {
		t.Fatalf("expected bounded diagnostic: %#v", result)
	}
}

func TestPreparedSnapshotAdoptsExistingOperationBeforeCreating(t *testing.T) {
	originalReconcileRunner := runManagedSnapshotReconcileCommand
	originalSnapshotRunner := runManagedSnapshotCommand
	t.Cleanup(func() {
		runManagedSnapshotReconcileCommand = originalReconcileRunner
		runManagedSnapshotCommand = originalSnapshotRunner
	})
	runManagedSnapshotReconcileCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
	) (process.Result, error) {
		return process.Result{Stdout: `[
			{"id":"snapshot-existing","endTime":"2026-08-20T10:00:00Z","stats":{"totalSize":42,"fileCount":3,"dirCount":1},"tags":{"tag:hfl-operation":"operation-123"}}
		]`}, nil
	}
	snapshotCalls := 0
	runManagedSnapshotCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		snapshotCalls++
		return process.Result{}, nil
	}

	status, result, message := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "prepared-reconcile", "kopia", "/tmp/reconcile.config",
		nil, "/data", map[string]any{"operation_id": "operation-123", "operation_attempt": 2},
		false,
	)

	if status != "success" || message != "" {
		t.Fatalf("status=%q message=%q result=%#v", status, message, result)
	}
	if snapshotCalls != 0 {
		t.Fatalf("snapshot create ran %d times after reconciliation", snapshotCalls)
	}
	if result["kopia_snapshot_id"] != "snapshot-existing" || result["snapshot_reconciled"] != true {
		t.Fatalf("existing snapshot was not adopted: %#v", result)
	}
	if result["size_bytes"] != int64(42) || result["file_count"] != int64(3) {
		t.Fatalf("existing snapshot metrics were not adopted: %#v", result)
	}
}

func TestPreparedSnapshotFirstAttemptTagsWithoutReconcileRead(t *testing.T) {
	originalReconcileRunner := runManagedSnapshotReconcileCommand
	originalSnapshotRunner := runManagedSnapshotCommand
	t.Cleanup(func() {
		runManagedSnapshotReconcileCommand = originalReconcileRunner
		runManagedSnapshotCommand = originalSnapshotRunner
	})
	runManagedSnapshotReconcileCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
	) (process.Result, error) {
		t.Fatal("first backup attempt must not add a repository reconciliation read")
		return process.Result{}, nil
	}
	createCalls := 0
	runManagedSnapshotCommand = func(
		_ context.Context,
		_ string,
		args []string,
		_ map[string]string,
		_ string,
		_ process.OutputLineHandler,
	) (process.Result, error) {
		createCalls++
		if !slices.Contains(args, "--tags=hfl-operation:operation-123") {
			t.Fatalf("first attempt did not tag the snapshot: %#v", args)
		}
		return process.Result{Stdout: `{"id":"snapshot-created"}`}, nil
	}

	status, result, message := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "prepared-first-attempt", "kopia", "/tmp/reconcile.config",
		nil, "/data", map[string]any{"operation_id": "operation-123", "operation_attempt": 1},
		false,
	)

	if status != "success" || message != "" {
		t.Fatalf("status=%q message=%q result=%#v", status, message, result)
	}
	if createCalls != 1 || result["kopia_snapshot_id"] != "snapshot-created" {
		t.Fatalf("first attempt did not create exactly one tagged snapshot: %#v", result)
	}
}

func TestPreparedSnapshotAdoptsNewestExistingOperationMatch(t *testing.T) {
	originalReconcileRunner := runManagedSnapshotReconcileCommand
	originalSnapshotRunner := runManagedSnapshotCommand
	t.Cleanup(func() {
		runManagedSnapshotReconcileCommand = originalReconcileRunner
		runManagedSnapshotCommand = originalSnapshotRunner
	})
	runManagedSnapshotReconcileCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
	) (process.Result, error) {
		return process.Result{Stdout: `[
			{"id":"snapshot-older","endTime":"2026-08-20T09:00:00Z","tags":{"tag:hfl-operation":"operation-123"}},
			{"id":"snapshot-newest","endTime":"2026-08-20T11:00:00Z","tags":{"tag:hfl-operation":"operation-123"}},
			{"id":"snapshot-other","endTime":"2026-08-20T12:00:00Z","tags":{"tag:hfl-operation":"operation-other"}}
		]`}, nil
	}
	runManagedSnapshotCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		t.Fatal("snapshot create must not run when an operation match exists")
		return process.Result{}, nil
	}

	status, result, message := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "prepared-reconcile-newest", "kopia", "/tmp/reconcile.config",
		nil, "/data", map[string]any{"operation_id": "operation-123"},
		false,
	)

	if status != "success" || message != "" {
		t.Fatalf("status=%q message=%q result=%#v", status, message, result)
	}
	if result["kopia_snapshot_id"] != "snapshot-newest" {
		t.Fatalf("newest matching snapshot was not adopted: %#v", result)
	}
	if result["snapshot_reconcile_match_count"] != 2 {
		t.Fatalf("unexpected operation match count: %#v", result)
	}
}

func TestPreparedSnapshotFailsClosedWhenOperationCannotBeReconciled(t *testing.T) {
	originalReconcileRunner := runManagedSnapshotReconcileCommand
	originalSnapshotRunner := runManagedSnapshotCommand
	t.Cleanup(func() {
		runManagedSnapshotReconcileCommand = originalReconcileRunner
		runManagedSnapshotCommand = originalSnapshotRunner
	})
	runManagedSnapshotReconcileCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
	) (process.Result, error) {
		return process.Result{ExitCode: 1}, errors.New("repository unavailable")
	}
	snapshotCalls := 0
	runManagedSnapshotCommand = func(
		context.Context,
		string,
		[]string,
		map[string]string,
		string,
		process.OutputLineHandler,
	) (process.Result, error) {
		snapshotCalls++
		return process.Result{}, nil
	}

	status, result, message := runPreparedManagedSnapshot(
		t.Context(), ReporterSink{}, "prepared-reconcile-failed", "kopia", "/tmp/reconcile.config",
		nil, "/data", map[string]any{"operation_id": "operation-123"},
		false,
	)

	if status != "failed" || !strings.Contains(message, "snapshot reconciliation failed") {
		t.Fatalf("status=%q message=%q result=%#v", status, message, result)
	}
	if snapshotCalls != 0 {
		t.Fatalf("snapshot create ran %d times after failed reconciliation", snapshotCalls)
	}
	if result["error_code"] != "KOPIA_SNAPSHOT_RECONCILE_FAILED" {
		t.Fatalf("structured reconciliation error missing: %#v", result)
	}
}

func TestManagedBackupOperationIDRejectsUnsafeValues(t *testing.T) {
	for _, operationID := range []string{
		"operation:with-colon",
		"operation with spaces",
		strings.Repeat("a", backupOperationIDMaxLength+1),
	} {
		_, err := managedBackupOperationID(Payload{Extra: map[string]any{"operation_id": operationID}})
		if err == nil {
			t.Fatalf("unsafe operation id was accepted: %q", operationID)
		}
	}
}
