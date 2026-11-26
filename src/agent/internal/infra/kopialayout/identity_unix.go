//go:build !windows

package kopialayout

import (
	"fmt"
	"os"
	"syscall"
)

func fileIdentity(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return "", fmt.Errorf("directory identity unavailable")
	}
	return fmt.Sprintf("%d:%d", stat.Dev, stat.Ino), nil
}

func directoryIdentity(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	if !info.IsDir() {
		return "", fmt.Errorf("not a directory")
	}
	return fileIdentity(path)
}

// removeEmptyCacheDirectory must not unlink a file swapped into the cache path.
func removeEmptyCacheDirectory(path string) error {
	if err := syscall.Rmdir(path); err != nil {
		return &os.PathError{Op: "remove", Path: path, Err: err}
	}
	return nil
}
