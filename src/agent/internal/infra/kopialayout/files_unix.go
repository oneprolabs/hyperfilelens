//go:build !windows

package kopialayout

import (
	"os"
	"path/filepath"
)

func restrictPath(path string, dir bool) error {
	mode := os.FileMode(0o600)
	if dir {
		mode = 0o700
	}
	return os.Chmod(path, mode)
}

func syncDir(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}

func replaceDurable(source, target string) error {
	if err := os.Rename(source, target); err != nil {
		return err
	}
	return syncDir(filepath.Dir(target))
}
