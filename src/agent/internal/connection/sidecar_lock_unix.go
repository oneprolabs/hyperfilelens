//go:build !windows

package connection

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/sys/unix"
)

// Use the existing sidecar-manager flock, including across Agent restart.
// Its background refresh then sees the new applied fingerprint after release.
func lockSidecar(ctx context.Context) (func(), error) {
	return lockSidecarAt(ctx, "/run/lock/hyperfilelens-gateway-sidecar.lock")
}

func lockSidecarAt(ctx context.Context, path string) (func(), error) {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	for {
		err = unix.Flock(int(file.Fd()), unix.LOCK_EX|unix.LOCK_NB)
		if err == nil {
			return func() {
				_ = unix.Flock(int(file.Fd()), unix.LOCK_UN)
				_ = file.Close()
			}, nil
		}
		if !errors.Is(err, unix.EWOULDBLOCK) && !errors.Is(err, unix.EAGAIN) {
			_ = file.Close()
			return nil, err
		}
		select {
		case <-ctx.Done():
			_ = file.Close()
			return nil, ctx.Err()
		case <-time.After(100 * time.Millisecond):
		}
	}
}
