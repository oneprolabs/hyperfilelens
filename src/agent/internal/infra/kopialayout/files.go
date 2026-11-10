package kopialayout

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

func within(root, path string) bool {
	rel, err := filepath.Rel(root, path)
	return err == nil && rel != "." && rel != ".." && !filepath.IsAbs(rel) &&
		!strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

// physicalPath resolves existing ancestors even when the final path has not
// been created yet. This closes aliases such as an external log path pointing
// through a parent symlink back into cache.
func physicalPath(path string) (string, error) {
	path, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	current := path
	var tail []string
	for {
		if _, err := os.Lstat(current); err == nil {
			resolved, err := filepath.EvalSymlinks(current)
			if err != nil {
				return "", err
			}
			for i := len(tail) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, tail[i])
			}
			return resolved, nil
		} else if !os.IsNotExist(err) {
			return "", err
		}
		parent := filepath.Dir(current)
		if parent == current {
			return "", fmt.Errorf("cannot resolve managed path")
		}
		tail = append(tail, filepath.Base(current))
		current = parent
	}
}

func separateLogs(root, logs string) error {
	cache, err := physicalPath(filepath.Dir(LegacyConfigDir(root)))
	if err != nil {
		return err
	}
	scope, err := physicalPath(filepath.Join(logs, "kopia"))
	if err != nil {
		return err
	}
	if samePath(cache, scope) || within(cache, scope) || within(scope, cache) {
		return fmt.Errorf("Kopia log root overlaps the cache root")
	}
	return nil
}

// plainPath checks every existing component below the trusted installation/log
// root. It never traverses a link in a migration source or destination.
func plainPath(root, path string) error {
	if !samePath(root, path) && !within(root, path) {
		return fmt.Errorf("path escapes its managed root")
	}
	current := filepath.Clean(root)
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return err
	}
	parts := []string{"."}
	if rel != "." {
		parts = append(parts, strings.Split(rel, string(filepath.Separator))...)
	}
	for index, part := range parts {
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			if index == 0 {
				// The configured installation/log root is trusted and may be
				// an existing directory alias. Keep its lexical cache identity,
				// but never follow links introduced below that root.
				resolved, err := os.Stat(current)
				if err == nil && resolved.IsDir() {
					continue
				}
			}
			return fmt.Errorf("managed path contains a symbolic link")
		}
	}
	return nil
}

func privateDir(root, dir string) error {
	if err := plainPath(root, dir); err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	return restrictPath(dir, true)
}

func readRegular(root, path string, limit int64) ([]byte, error) {
	if err := plainPath(root, path); err != nil {
		return nil, err
	}
	before, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !before.Mode().IsRegular() || before.Size() > limit {
		return nil, fmt.Errorf("managed file is not regular or exceeds the scan bound")
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !os.SameFile(before, opened) {
		return nil, fmt.Errorf("managed file changed while opening")
	}
	data, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err == nil && int64(len(data)) > limit {
		err = fmt.Errorf("managed file exceeds the scan bound")
	}
	return data, err
}

func digest(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// writeDurable keeps temporary copies outside cache, syncs contents before an
// atomic replacement, and syncs the directory using platform-specific rules.
func writeDurable(root, path string, data []byte) error {
	if err := privateDir(root, filepath.Dir(path)); err != nil {
		return err
	}
	if err := plainPath(root, path); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".cache-layout-*")
	if err != nil {
		return err
	}
	temp := f.Name()
	defer os.Remove(temp)
	if err := restrictPath(temp, false); err != nil {
		_ = f.Close()
		return err
	}
	_, err = io.Copy(f, bytes.NewReader(data))
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return replaceDurable(temp, path)
}
