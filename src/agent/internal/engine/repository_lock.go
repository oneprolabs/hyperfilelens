package engine

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

var repositoryPrepareLocks sync.Map
var insightRepositoryOperationLocks sync.Map
var insightRepositoryReadyCache sync.Map

const insightRepositoryReadyTTL = 30 * time.Second

type insightRepositoryReadyState struct {
	expiresAt time.Time
	modTime   time.Time
	size      int64
}

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
	select {
	case lock <- struct{}{}:
		return func() { <-lock }, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func insightRepositoryReady(configFile string) bool {
	info, err := os.Stat(configFile)
	if err != nil {
		return false
	}
	value, ok := insightRepositoryReadyCache.Load(filepath.Clean(configFile))
	if !ok {
		return false
	}
	state, ok := value.(insightRepositoryReadyState)
	return ok &&
		time.Now().Before(state.expiresAt) &&
		state.modTime.Equal(info.ModTime()) &&
		state.size == info.Size()
}

func markInsightRepositoryReady(configFile string) {
	info, err := os.Stat(configFile)
	if err != nil {
		return
	}
	insightRepositoryReadyCache.Store(
		filepath.Clean(configFile),
		insightRepositoryReadyState{
			expiresAt: time.Now().Add(insightRepositoryReadyTTL),
			modTime:   info.ModTime(),
			size:      info.Size(),
		},
	)
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
