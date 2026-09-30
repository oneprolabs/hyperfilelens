//go:build linux

package engine

import (
	"context"
	"fmt"
	"io/fs"
	"path/filepath"
	"strings"

	"golang.org/x/sys/unix"
)

const chatUpdateMinimumHeadroomBytes int64 = 8 << 20

// A lower-bound admission check, not a promise that Kopia will not later
// encounter ENOSPC: atomic replacement may temporarily hold both versions of
// a large file and other workloads can consume space concurrently.
func checkChatUpdateDiskHeadroom(
	ctx context.Context, target string, snapshotBytes int64,
) error {
	if snapshotBytes < 0 {
		return fmt.Errorf("snapshot size is not available for Chat data update")
	}
	var existingBytes int64
	err := filepath.WalkDir(target, func(path string, entry fs.DirEntry, walkErr error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if walkErr != nil {
			return walkErr
		}
		name := entry.Name()
		if entry.IsDir() &&
			(strings.HasSuffix(name, ".sourcelens") || strings.HasPrefix(name, ".sourcelens")) {
			return filepath.SkipDir
		}
		if !entry.Type().IsRegular() ||
			strings.HasPrefix(name, ".sourcelens") ||
			strings.HasSuffix(name, ".sourcelens") {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if info.Size() < 0 || existingBytes > int64(^uint64(0)>>1)-info.Size() {
			return fmt.Errorf("workspace size exceeds supported range")
		}
		existingBytes += info.Size()
		return nil
	})
	if err != nil {
		return err
	}
	var stat unix.Statfs_t
	if err := unix.Statfs(target, &stat); err != nil {
		return err
	}
	if stat.Bsize <= 0 {
		return fmt.Errorf("Data Gateway filesystem capacity is unavailable")
	}
	available := ^uint64(0)
	if uint64(stat.Bavail) <= available/uint64(stat.Bsize) {
		available = uint64(stat.Bavail) * uint64(stat.Bsize)
	}
	required := chatUpdateMinimumHeadroomBytes
	if snapshotBytes > existingBytes &&
		snapshotBytes-existingBytes > required {
		required = snapshotBytes - existingBytes
	}
	if available < uint64(required) {
		return fmt.Errorf("Data Gateway workspace has insufficient free space for this snapshot")
	}
	return nil
}
