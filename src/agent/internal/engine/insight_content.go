package engine

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
)

const (
	insightRegularFilesOnlyPolicy      = "regular_files_only_v1"
	insightUnsupportedContentErrorCode = "INSIGHT_UNSUPPORTED_CONTENT_TYPE"
)

// enforceInsightRestoreContent removes filesystem objects that SourceLens
// must never ingest. WalkDir does not follow symbolic links, so content
// outside the managed workspace cannot be traversed by this validation pass.
func enforceInsightRestoreContent(root string) (int64, error) {
	var skipped int64
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			if errors.Is(walkErr, os.ErrNotExist) {
				return nil
			}
			return walkErr
		}
		info, err := entry.Info()
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				return nil
			}
			return err
		}
		if info.IsDir() || info.Mode().IsRegular() {
			return nil
		}
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		skipped++
		return nil
	})
	if errors.Is(err, os.ErrNotExist) {
		return skipped, nil
	}
	return skipped, err
}
