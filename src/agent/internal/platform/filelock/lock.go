// Package filelock provides cancellable, cross-process filesystem locks.
package filelock

import (
	"context"
	"fmt"
	"os"
	"sync"
	"time"
)

// Acquire locks an existing private directory's lock file. Shared users may
// coexist; an exclusive migration waits for every shared user to leave.
// The file must not be unlinked while any process can still use this lock.
func Acquire(ctx context.Context, path string, shared bool) (func(), error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if info, err := os.Lstat(path); err == nil {
		if !info.Mode().IsRegular() {
			return nil, fmt.Errorf("lock is not a regular file")
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	info, err := f.Stat()
	current, statErr := os.Lstat(path)
	if err != nil || statErr != nil || !current.Mode().IsRegular() || !os.SameFile(info, current) {
		_ = f.Close()
		return nil, fmt.Errorf("lock file changed while opening")
	}
	for {
		ok, err := tryLock(f, shared)
		if err != nil {
			_ = f.Close()
			return nil, err
		}
		if ok {
			var once sync.Once
			return func() {
				once.Do(func() {
					unlock(f)
					_ = f.Close()
				})
			}, nil
		}
		timer := time.NewTimer(100 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			_ = f.Close()
			return nil, ctx.Err()
		case <-timer.C:
		}
	}
}
