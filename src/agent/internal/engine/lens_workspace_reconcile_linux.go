//go:build linux

package engine

import (
	"io"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/unix"
)

// Remove through directory FDs rooted at the verified workspace. A concurrent
// replacement with a symlink cannot redirect traversal outside the workspace.
func removeReconciledWorkspaceEntry(root, relative string, onRemoved func()) error {
	parent := filepath.Dir(relative)
	name := filepath.Base(relative)
	if name == "." || name == ".." || strings.ContainsAny(name, `/\`) {
		return unix.EINVAL
	}
	parentFD, _, err := secureOpenDirectory(
		filepath.Join(root, parent), root, true, uint64(unix.O_RDONLY),
	)
	if err != nil {
		return err
	}
	defer unix.Close(parentFD)
	return removeWorkspaceEntryAt(parentFD, name, onRemoved)
}

func removeWorkspaceEntryAt(parentFD int, name string, onRemoved func()) error {
	var stat unix.Stat_t
	if err := unix.Fstatat(parentFD, name, &stat, unix.AT_SYMLINK_NOFOLLOW); err != nil {
		return err
	}
	if stat.Mode&unix.S_IFMT != unix.S_IFDIR {
		if err := unix.Unlinkat(parentFD, name, 0); err != nil {
			return err
		}
		onRemoved()
		return nil
	}
	childFD, err := unix.Openat(
		parentFD, name, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0,
	)
	if err != nil {
		return err
	}
	children := os.NewFile(uintptr(childFD), name)
	if children == nil {
		_ = unix.Close(childFD)
		return unix.EBADF
	}
	defer children.Close()
	for {
		entries, readErr := children.ReadDir(512)
		for _, entry := range entries {
			if err := removeWorkspaceEntryAt(childFD, entry.Name(), onRemoved); err != nil {
				return err
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return readErr
		}
	}
	if err := unix.Unlinkat(parentFD, name, unix.AT_REMOVEDIR); err != nil {
		return err
	}
	onRemoved()
	return nil
}
