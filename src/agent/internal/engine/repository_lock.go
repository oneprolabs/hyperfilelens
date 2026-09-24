package engine

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"
)

var repositoryPrepareLocks sync.Map
var insightRepositoryOperationLocks sync.Map

func withRepositoryPrepareLock(ctx context.Context, configFile string, fn func() (string, map[string]string, map[string]any, repositorySpec, string)) (string, map[string]string, map[string]any, repositorySpec, string) {
	if err := ctx.Err(); err != nil {
		return "", nil, nil, repositorySpec{}, err.Error()
	}
	key := filepath.Clean(configFile)
	lockValue, _ := repositoryPrepareLocks.LoadOrStore(key, &sync.Mutex{})
	mu := lockValue.(*sync.Mutex)
	mu.Lock()
	defer mu.Unlock()
	if err := ctx.Err(); err != nil {
		return "", nil, nil, repositorySpec{}, err.Error()
	}
	unlock, err := acquireRepositoryFileLock(configFile)
	if err != nil {
		return "", nil, nil, repositorySpec{}, err.Error()
	}
	defer unlock()
	return fn()
}

func acquireInsightRepositoryOperationLock(
	ctx context.Context,
	p Payload,
) (func(), error) {
	spec, ok, err := parseRepositorySpec(p.Extra["repository"])
	if err != nil {
		return nil, err
	}
	if !ok || spec.ID <= 0 {
		return func() {}, nil
	}
	key := fmt.Sprintf("insight-repository-%d", spec.ID)
	lockValue, _ := insightRepositoryOperationLocks.LoadOrStore(
		key,
		make(chan struct{}, 1),
	)
	lock := lockValue.(chan struct{})
	waitStarted := time.Now()
	select {
	case lock <- struct{}{}:
		slog.Info(
			"insight_repository",
			"event", "operation_lock_acquired",
			"repository_id", spec.ID,
			"wait_ms", time.Since(waitStarted).Milliseconds(),
		)
		return func() { <-lock }, nil
	case <-ctx.Done():
		slog.Warn(
			"insight_repository",
			"event", "operation_lock_canceled",
			"repository_id", spec.ID,
			"wait_ms", time.Since(waitStarted).Milliseconds(),
		)
		return nil, ctx.Err()
	}
}

func repositoryLockFile(configFile string) string {
	return configFile + ".lock"
}

func openRepositoryLockFile(configFile string) (*os.File, error) {
	lockPath := repositoryLockFile(configFile)
	if err := os.MkdirAll(filepath.Dir(lockPath), 0o700); err != nil {
		return nil, err
	}
	return os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0o600)
}
