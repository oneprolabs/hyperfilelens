package lifecycle

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestReadinessBindsLiveProcessAndBuild(t *testing.T) {
	root := t.TempDir()
	ctx := context.Background()
	if err := Publish(ctx, root, "test", "commit"); err != nil {
		t.Fatal(err)
	}
	if err := Check(ctx, root, int32(os.Getpid()), "test", "commit"); err != nil {
		t.Fatal(err)
	}
	if err := Check(ctx, root, int32(os.Getpid()), "other", "commit"); err == nil {
		t.Fatal("accepted another build")
	}
	var r Ready
	if err := Read(root, readyFile, &r); err != nil {
		t.Fatal(err)
	}
	r.ProcessStart--
	if err := Write(root, readyFile, r); err != nil {
		t.Fatal(err)
	}
	if err := Check(ctx, root, int32(os.Getpid()), "test", "commit"); err == nil {
		t.Fatal("accepted reused PID")
	}
}

func TestReadinessUsesConfiguredRootAliases(t *testing.T) {
	parent := t.TempDir()
	root := filepath.Join(parent, "agent-root")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	t.Chdir(parent)
	check := func(t *testing.T, configured string) {
		t.Helper()
		if err := Publish(t.Context(), configured, "test", "commit"); err != nil {
			t.Fatal(err)
		}
		for _, input := range []string{root, configured} {
			if err := Check(t.Context(), input, int32(os.Getpid()), "test", "commit"); err != nil {
				t.Fatal(err)
			}
		}
	}
	t.Run("relative", func(t *testing.T) { check(t, "agent-root") })
	t.Run("symlink", func(t *testing.T) {
		alias := filepath.Join(parent, "alias")
		if err := os.Symlink(root, alias); err != nil {
			t.Skip(err)
		}
		check(t, alias)
		outside := t.TempDir()
		if err := os.Rename(filepath.Join(root, "runtime"), filepath.Join(root, "original-runtime")); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(outside, filepath.Join(root, "runtime")); err != nil {
			t.Fatal(err)
		}
		if err := Publish(t.Context(), alias, "test", "commit"); err == nil {
			t.Fatal("accepted a symlink below the configured Agent Root")
		}
	})
}

func TestRecordRejectsSymlinkAndTraversal(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(root, "lifecycle")); err != nil {
		t.Skip(err)
	}
	if err := Write(root, "lifecycle/attempt.json", map[string]any{}); err == nil {
		t.Fatal("followed lifecycle symlink")
	}
	if err := Write(root, "../outside.json", map[string]any{}); err == nil {
		t.Fatal("accepted escaping path")
	}
}

func TestFailureMatchesExactAcceptedExecution(t *testing.T) {
	root := t.TempDir()
	started := time.Now().UTC().Add(-time.Minute)
	a, err := Begin(root, Attempt{TaskID: "task", TaskStartedAt: started.Format(time.RFC3339Nano), TargetVersion: "new", PackageSHA256: "hash"})
	if err != nil {
		t.Fatal(err)
	}
	if got, err := Failure(root, "task", &started); err != nil || got != nil {
		t.Fatalf("unfinished attempt: %v %v", got, err)
	}
	a, err = Fail(root, a.ID, "service_started", "AGENT_UPGRADE_STARTUP_FAILED", "rolled_back", 1)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := Failure(root, "other", &started); err != nil || got != nil {
		t.Fatalf("another task: %v %v", got, err)
	}
	otherStart := started.Add(time.Second)
	if got, err := Failure(root, "task", &otherStart); err != nil || got != nil {
		t.Fatalf("another execution: %v %v", got, err)
	}
	got, err := Failure(root, "task", &started)
	if err != nil || got == nil || got.ID != a.ID {
		t.Fatalf("failure missing: %v %v", got, err)
	}
	again, err := Fail(root, a.ID, "wrong", "wrong", "wrong", 70)
	if err != nil || again.ErrorCode != a.ErrorCode || again.ExitCode != 1 {
		t.Fatalf("failure was overwritten: %v %v", again, err)
	}
}

func TestAttemptBeginIsIdempotentButCannotChangePackage(t *testing.T) {
	root := t.TempDir()
	input := Attempt{TaskID: "task", TaskStartedAt: time.Now().UTC().Format(time.RFC3339Nano), TargetVersion: "new", TargetCommit: "commit", PackageSHA256: "first"}
	a, err := Begin(root, input)
	if err != nil {
		t.Fatal(err)
	}
	b, err := Begin(root, input)
	if err != nil || a.ID != b.ID {
		t.Fatalf("duplicate attempt created: %v %v", b, err)
	}
	input.PackageSHA256 = "second"
	if _, err := Begin(root, input); err == nil {
		t.Fatal("same accepted execution changed packages")
	}
}

func TestAttemptLookupSkipsDamagedHistoryAndRejectsDamagedCurrentRecord(t *testing.T) {
	root := t.TempDir()
	broken := filepath.Join(root, "lifecycle", "attempts", "00000000000000000000000000000000.json")
	if err := os.MkdirAll(filepath.Dir(broken), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(broken, []byte("{corrupt legacy attempt"), 0600); err != nil {
		t.Fatal(err)
	}
	started := time.Now().UTC()
	current, err := Begin(root, Attempt{TaskID: "upgrade-current", TaskStartedAt: started.Format(time.RFC3339Nano), TargetVersion: "new"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Fail(root, current.ID, "installed", "UPGRADE_FAILED", "rolled_back", 2); err != nil {
		t.Fatal(err)
	}
	if got, err := Failure(root, current.TaskID, &started); err != nil || got == nil || got.ID != current.ID {
		t.Fatalf("unrelated history hid valid current evidence: %v %v", got, err)
	}
	currentPath, err := attemptPath(current.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, currentPath), []byte("{corrupt current attempt"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := Failure(root, current.TaskID, &started); err == nil {
		t.Fatal("corrupt current evidence fell through to legacy success heuristics")
	}
	if _, err := Begin(root, current); err == nil {
		t.Fatal("overwrote corrupt current evidence")
	}
}

func TestTaskAttemptLookupReadsLegacyRandomID(t *testing.T) {
	root := t.TempDir()
	started := time.Now().UTC()
	old := Attempt{Schema: 1, ID: "1234567890abcdef1234567890abcdef", TaskID: "legacy", TaskStartedAt: started.Format(time.RFC3339Nano), TargetVersion: "new", ExitCode: 2, ErrorCode: "FAILED", FinishedAt: started.Add(time.Second).Format(time.RFC3339Nano)}
	path, err := attemptPath(old.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := Write(root, path, old); err != nil {
		t.Fatal(err)
	}
	if got, err := Failure(root, old.TaskID, &started); err != nil || got == nil || got.ID != old.ID {
		t.Fatalf("legacy attempt not found: %v %v", got, err)
	}
}
