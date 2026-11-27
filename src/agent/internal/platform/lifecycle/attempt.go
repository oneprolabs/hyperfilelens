package lifecycle

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"time"

	"hyperfilelens/agent/internal/platform/filelock"
)

type Attempt struct {
	Schema        int    `json:"schema_version"`
	ID            string `json:"attempt_id"`
	TaskID        string `json:"task_id"`
	TaskStartedAt string `json:"task_started_at"`
	TargetVersion string `json:"target_version"`
	TargetCommit  string `json:"target_commit"`
	PackageSHA256 string `json:"package_sha256"`
	Phase         string `json:"phase"`
	ErrorCode     string `json:"error_code,omitempty"`
	ExitCode      int    `json:"exit_code,omitempty"`
	RollbackPhase string `json:"rollback_phase,omitempty"`
	FinishedAt    string `json:"finished_at,omitempty"`
}

func attemptPath(id string) (string, error) {
	b, err := hex.DecodeString(id)
	if err != nil || len(b) != 16 {
		return "", fmt.Errorf("invalid upgrade attempt identity")
	}
	return filepath.Join("lifecycle", "attempts", id+".json"), nil
}

// taskAttemptID locates an attempt without parsing any other task's files.
// The ID is a lookup key, not authentication evidence: every read still checks
// the task tuple, and the database independently checks the accepted execution.
func taskAttemptID(taskID, startedAt string) string {
	sum := sha256.Sum256([]byte(taskID + "\x00" + startedAt))
	return hex.EncodeToString(sum[:16])
}

func normalizeAttemptStart(startedAt string) (string, error) {
	started, err := time.Parse(time.RFC3339Nano, startedAt)
	if err != nil {
		return "", err
	}
	return started.UTC().Format(time.RFC3339Nano), nil
}

func Begin(root string, a Attempt) (Attempt, error) {
	started, err := normalizeAttemptStart(a.TaskStartedAt)
	if err != nil || a.TaskID == "" {
		return a, fmt.Errorf("invalid upgrade task identity")
	}
	a.TaskStartedAt = started
	release, err := lockAttempts(root)
	if err != nil {
		return a, err
	}
	defer release()
	existing, err := findAttempt(root, a.TaskID, started, false)
	if err != nil {
		return a, err
	}
	if existing != nil {
		if existing.TargetVersion != a.TargetVersion || existing.TargetCommit != a.TargetCommit || existing.PackageSHA256 != a.PackageSHA256 || existing.FinishedAt != "" {
			return a, fmt.Errorf("upgrade execution already has incompatible or terminal evidence")
		}
		return *existing, nil
	}
	a.Schema = 1
	a.ID = taskAttemptID(a.TaskID, started)
	a.Phase = "installer_started"
	relative, _ := attemptPath(a.ID)
	return a, Write(root, relative, a)
}

func sameAttemptTask(a Attempt, taskID, started string) bool {
	actual, err := normalizeAttemptStart(a.TaskStartedAt)
	return a.TaskID == taskID && err == nil && actual == started
}

func findAttempt(root, taskID, started string, requireCertainLegacyEvidence bool) (*Attempt, error) {
	id := taskAttemptID(taskID, started)
	path, err := attemptPath(id)
	if err != nil {
		return nil, err
	}
	full, err := Path(root, path)
	if err != nil {
		return nil, err
	}
	// Distinguish a missing binding from a record that failed to open or parse.
	if _, err := os.Lstat(full); err == nil {
		a, err := Load(root, id)
		if err != nil {
			return nil, fmt.Errorf("read current upgrade attempt: %w", err)
		}
		if !sameAttemptTask(a, taskID, started) {
			return nil, fmt.Errorf("current upgrade attempt identity mismatch")
		}
		return &a, nil
	} else if !errors.Is(err, fs.ErrNotExist) {
		return nil, err
	}

	// Older releases used random filenames. Valid exact-task evidence still
	// works, but a damaged unrelated file must not prevent new attempts.
	dir, err := Path(root, "lifecycle/attempts")
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var match *Attempt
	uncertain := false
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		a, err := Load(root, entry.Name()[:len(entry.Name())-5])
		if err != nil {
			uncertain = true
			continue
		}
		if !sameAttemptTask(a, taskID, started) {
			continue
		}
		if match != nil {
			return nil, fmt.Errorf("ambiguous upgrade attempt evidence")
		}
		copy := a
		match = &copy
	}
	if match == nil && uncertain && requireCertainLegacyEvidence {
		// Do not infer a successful completion from a global log or marker
		// if an unreadable legacy attempt could correspond to this task.
		return nil, fmt.Errorf("cannot attribute unreadable legacy upgrade evidence")
	}
	return match, nil
}

func Load(root, id string) (Attempt, error) {
	var a Attempt
	relative, err := attemptPath(id)
	if err != nil {
		return a, err
	}
	if err = Read(root, relative, &a); err != nil {
		return a, err
	}
	if a.Schema != 1 || a.ID != id {
		return a, fmt.Errorf("invalid upgrade attempt record")
	}
	return a, nil
}

func Fail(root, id, phase, code, rollback string, exit int) (Attempt, error) {
	release, err := lockAttempts(root)
	if err != nil {
		return Attempt{}, err
	}
	defer release()
	a, err := Load(root, id)
	if err != nil {
		return a, err
	}
	if a.FinishedAt != "" {
		return a, nil
	}
	a.Phase = phase
	a.ErrorCode = code
	a.RollbackPhase = rollback
	a.ExitCode = exit
	a.FinishedAt = time.Now().UTC().Format(time.RFC3339Nano)
	relative, _ := attemptPath(id)
	return a, Write(root, relative, a)
}

// Failure returns only evidence for this exact accepted task execution. Legacy
// global FAILED markers remain fallback evidence, never proof of another attempt.
func Failure(root, taskID string, started *time.Time) (*Attempt, error) {
	if started == nil {
		return nil, nil
	}
	root, err := ResolveAgentRoot(root)
	if err != nil {
		return nil, err
	}
	a, err := findAttempt(root, taskID, started.UTC().Format(time.RFC3339Nano), true)
	if err != nil || a == nil {
		return nil, err
	}
	if a.FinishedAt == "" {
		return nil, nil
	}
	return a, nil
}

func (a Attempt) Result() map[string]any {
	return map[string]any{"mode": "local_detached", "attempt_id": a.ID, "phase": a.Phase,
		"error_code": a.ErrorCode, "exit_code": a.ExitCode, "rollback_phase": a.RollbackPhase,
		"target_version": a.TargetVersion, "target_commit": a.TargetCommit, "finished_at": a.FinishedAt}
}

func lockAttempts(root string) (func(), error) {
	path, err := Path(root, "lifecycle/attempts/.lock")
	if err != nil {
		return nil, err
	}
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	return filelock.Acquire(ctx, path, false)
}
