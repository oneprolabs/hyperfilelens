//go:build darwin

package enroll

import (
	"context"
	"errors"
	"os"
	"path/filepath"

	"golang.org/x/sys/unix"

	"hyperfilelens/agent/internal/platform/vfs"
)

const systemInstallLockPath = "/var/run/hyperfilelens-install.lock"

func installLockPath() (string, error) {
	if !vfs.UserInstallation() {
		return systemInstallLockPath, nil
	}
	dataDir, err := vfs.UserDataDir()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dataDir, 0o700); err != nil {
		return "", err
	}
	path := filepath.Join(vfs.AgentLifecycleDir(dataDir), "install.lock")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return "", err
	}
	return path, nil
}

func acquireInstallLock(_ context.Context) (func(), error) {
	lockPath, err := installLockPath()
	if err != nil {
		return nil, err
	}
	file, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := unix.Flock(int(file.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		_ = file.Close()
		if errors.Is(err, unix.EWOULDBLOCK) || errors.Is(err, unix.EAGAIN) {
			return nil, ErrInstallLocked
		}
		return nil, err
	}
	return func() {
		_ = unix.Flock(int(file.Fd()), unix.LOCK_UN)
		_ = file.Close()
	}, nil
}
