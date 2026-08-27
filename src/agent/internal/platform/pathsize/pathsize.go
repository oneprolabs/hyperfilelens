package pathsize

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

var errDuUnsupported = errors.New("du does not support the requested options")

// Estimate returns logical byte size for a file or directory path.
func Estimate(path string, pathType string) (uint64, error) {
	return EstimateWithExclusions(path, pathType, nil)
}

// EstimateWithExclusions returns a size estimate without traversing the
// supplied absolute paths. Unix uses du when its exclusion support is
// available; other platforms and minimal du implementations use WalkDir.
func EstimateWithExclusions(path string, pathType string, exclusions []string) (uint64, error) {
	return EstimateWithExclusionsContext(context.Background(), path, pathType, exclusions)
}

// EstimateWithExclusionsContext estimates a path while honoring cancellation.
func EstimateWithExclusionsContext(ctx context.Context, path string, pathType string, exclusions []string) (uint64, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
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
		if size, err := duBytes(ctx, clean, exclusions); err == nil {
			return size, nil
		} else if !errors.Is(err, errDuUnsupported) {
			return 0, err
		}
	}
	return walkBytes(ctx, clean, exclusions)
}

func duBytes(ctx context.Context, path string, exclusions []string) (uint64, error) {
	args := []string{"-sb"}
	for _, excluded := range exclusions {
		if value := strings.TrimSpace(excluded); value != "" {
			args = append(args, "--exclude="+filepath.Clean(value))
		}
	}
	args = append(args, "--", path)
	cmd := exec.CommandContext(ctx, "du", args...)
	output, err := cmd.Output()
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return 0, ctxErr
		}
		if errors.Is(err, exec.ErrNotFound) {
			return 0, errDuUnsupported
		}
		var exitErr *exec.ExitError
		if !errors.As(err, &exitErr) {
			return 0, fmt.Errorf("du: %w", err)
		}
		stderrText := strings.ToLower(string(exitErr.Stderr))
		if strings.Contains(stderrText, "unrecognized option") ||
			strings.Contains(stderrText, "illegal option") ||
			strings.Contains(stderrText, "unknown option") {
			return 0, errDuUnsupported
		}
		if strings.Contains(stderrText, "permission denied") {
			return 0, fs.ErrPermission
		}
		return 0, fmt.Errorf("du: %w", err)
	}
	fields := strings.Fields(strings.TrimSpace(string(output)))
	if len(fields) == 0 {
		return 0, fmt.Errorf("du returned empty output")
	}
	parsed, err := strconv.ParseUint(fields[0], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("parse du output: %w", err)
	}
	return parsed, nil
}

func walkBytes(ctx context.Context, root string, exclusions []string) (uint64, error) {
	var total uint64
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
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
