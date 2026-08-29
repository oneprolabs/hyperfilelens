//go:build !windows

package engine

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

func acquireLensWorkspaceFileLock(
	ctx context.Context,
	lockPath string,
) (func(), error) {
	file, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return func() {}, fmt.Errorf("open managed workspace lock: %w", err)
	}
	for {
		err = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return func() {
				_ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
				_ = file.Close()
			}, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			_ = file.Close()
			return func() {}, fmt.Errorf("lock managed workspace lifecycle: %w", err)
		}
		timer := time.NewTimer(100 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			_ = file.Close()
			return func() {}, fmt.Errorf("lock managed workspace lifecycle: %w", ctx.Err())
		case <-timer.C:
		}
	}
}

func replaceLensWorkspaceTombstone(temporary, destination string) error {
	if err := os.Rename(temporary, destination); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(destination))
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}
