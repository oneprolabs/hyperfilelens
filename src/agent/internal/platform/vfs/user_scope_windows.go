//go:build windows

package vfs

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows"
)

// resolveUserScopedPath keeps user-mode Windows workloads on local fixed
// drives and verifies access with the Agent process token. The console must
// never grant broader access than this local check.
func resolveUserScopedPath(path string, allowMissing bool) (string, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return UserHome()
	}
	if !filepath.IsAbs(path) {
		home, err := UserHome()
		if err != nil {
			return "", err
		}
		path = filepath.Join(home, path)
	}
	absPath, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve path: %w", err)
	}
	absPath = filepath.Clean(absPath)
	if err := requireLocalFixedDrive(absPath); err != nil {
		return "", err
	}

	resolved := absPath
	if allowMissing {
		resolved, err = resolvePathWithMissingTail(absPath)
	} else {
		resolved, err = filepath.EvalSymlinks(absPath)
	}
	if err != nil {
		return "", err
	}
	resolved = filepath.Clean(resolved)
	if err := requireLocalFixedDrive(resolved); err != nil {
		return "", err
	}
	if err := requireReadableExistingPath(resolved); err != nil {
		return "", err
	}
	return resolved, nil
}

func requireLocalFixedDrive(path string) error {
	volume := filepath.VolumeName(path)
	if len(volume) != 2 || volume[1] != ':' {
		return fmt.Errorf("%w: user-level Agent paths must use a local fixed drive", os.ErrPermission)
	}
	root := volume + `\`
	rootPtr, err := windows.UTF16PtrFromString(root)
	if err != nil || windows.GetDriveType(rootPtr) != windows.DRIVE_FIXED {
		return fmt.Errorf("%w: user-level Agent paths must use a local fixed drive", os.ErrPermission)
	}
	return nil
}

func requireReadableExistingPath(path string) error {
	probe := path
	for {
		handle, err := os.Open(probe)
		if err == nil {
			return handle.Close()
		}
		if !os.IsNotExist(err) {
			return err
		}
		parent := filepath.Dir(probe)
		if parent == probe {
			return err
		}
		probe = parent
	}
}
