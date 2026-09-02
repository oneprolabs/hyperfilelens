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

// Estimate returns a byte-size estimate for a file or directory path.
func Estimate(path string, pathType string) (uint64, error) {
	return EstimateWithExclusions(path, pathType, nil)
}

// EstimateWithExclusions returns a size estimate without traversing the
// supplied absolute paths. Unix uses allocated size from du when its required
// options are available; other platforms and minimal du implementations use
// logical file sizes from WalkDir.
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
	// BSD du (macOS) has no -b flag. -k reports allocated disk usage in KiB,
	// which is the capacity reference requested by HFL. GNU du keeps the
	// existing byte mode for Linux and other Unix platforms.
	args := []string{"-sb"}
	unitBytes := uint64(1)
	if runtime.GOOS == "darwin" {
		args = []string{"-sk"}
		unitBytes = 1024
	}
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
			strings.Contains(stderrText, "invalid option") ||
			strings.Contains(stderrText, "unknown option") {
			return 0, errDuUnsupported
		}
		if strings.Contains(stderrText, "permission denied") {
			if size, parseErr := parseDuBytes(output, unitBytes); parseErr == nil {
				// du emits a valid summary but exits non-zero when macOS privacy
				// controls hide part of a tree. Keep the usable partial total.
				return size, nil
			}
			return 0, fs.ErrPermission
		}
		if strings.Contains(stderrText, "operation not permitted") {
			if size, parseErr := parseDuBytes(output, unitBytes); parseErr == nil {
				return size, nil
			}
			return 0, fs.ErrPermission
		}
		return 0, fmt.Errorf("du: %w", err)
	}
	return parseDuBytes(output, unitBytes)
}

func parseDuBytes(output []byte, unitBytes uint64) (uint64, error) {
	fields := strings.Fields(strings.TrimSpace(string(output)))
	if len(fields) == 0 {
		return 0, fmt.Errorf("du returned empty output")
	}
	parsed, err := strconv.ParseUint(fields[0], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("parse du output: %w", err)
	}
	if unitBytes == 0 || parsed > ^uint64(0)/unitBytes {
		return 0, fmt.Errorf("du output overflows byte count")
	}
	return parsed * unitBytes, nil
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
			if path != root && os.IsPermission(walkErr) {
				// macOS privacy controls can hide isolated subtrees. Skip those
				// paths while retaining the readable portion of the estimate.
				return nil
			}
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		info, infoErr := entry.Info()
		if infoErr != nil {
			if path != root && os.IsPermission(infoErr) {
				return nil
			}
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
