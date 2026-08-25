package pathsize

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

// Estimate returns logical byte size for a file or directory path.
func Estimate(path string, pathType string) (uint64, error) {
	return EstimateWithExclusions(path, pathType, nil)
}

// EstimateWithExclusions returns a size estimate without traversing the
// supplied absolute paths. Unix uses du when its exclusion support is
// available; other platforms and minimal du implementations use WalkDir.
func EstimateWithExclusions(path string, pathType string, exclusions []string) (uint64, error) {
	clean := strings.TrimSpace(path)
	if clean == "" {
		return 0, os.ErrInvalid
	}
	kind := strings.ToLower(strings.TrimSpace(pathType))
	info, err := os.Stat(clean)
	if err != nil {
		return 0, err
	}
	if kind == "file" || (!info.IsDir() && kind != "directory") {
		if info.Size() < 0 {
			return 0, nil
		}
		return uint64(info.Size()), nil
	}
	if runtime.GOOS != "windows" {
		if size, ok := duBytes(clean, exclusions); ok {
			return size, nil
		}
	}
	return walkBytes(clean, exclusions)
}

func duBytes(path string, exclusions []string) (uint64, bool) {
	args := []string{"-sb"}
	for _, excluded := range exclusions {
		if value := strings.TrimSpace(excluded); value != "" {
			args = append(args, "--exclude="+filepath.Clean(value))
		}
	}
	args = append(args, "--", path)
	cmd := exec.Command("du", args...)
	output, err := cmd.Output()
	if err != nil {
		return 0, false
	}
	fields := strings.Fields(strings.TrimSpace(string(output)))
	if len(fields) == 0 {
		return 0, false
	}
	parsed, err := strconv.ParseUint(fields[0], 10, 64)
	if err != nil {
		return 0, false
	}
	return parsed, true
}

func walkBytes(root string, exclusions []string) (uint64, error) {
	var total uint64
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		for _, excluded := range exclusions {
			if sameOrWithin(path, excluded) {
				if entry == nil || entry.IsDir() {
					return filepath.SkipDir
				}
				return nil
			}
		}
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		info, infoErr := entry.Info()
		if infoErr != nil {
			return infoErr
		}
		if info.Size() > 0 {
			total += uint64(info.Size())
		}
		return nil
	})
	return total, err
}

func sameOrWithin(path, root string) bool {
	path = filepath.Clean(path)
	root = filepath.Clean(root)
	if runtime.GOOS == "windows" {
		path = strings.ToLower(path)
		root = strings.ToLower(root)
	}
	if path == root {
		return true
	}
	rel, err := filepath.Rel(root, path)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}
