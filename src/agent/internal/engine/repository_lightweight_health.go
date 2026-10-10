package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	nassvc "hyperfilelens/agent/internal/service/nas"
)

const (
	repositoryMetadataMaxBytes = 1024 * 1024
	// Kopia's filesystem blob provider adds .f to physical blob filenames.
	// Object storage uses the logical blob ID without this suffix.
	filesystemRepositoryFormatFile = "kopia.repository.f"
)

var errLightweightOwnershipInvalid = errors.New("repository ownership is invalid")

// readHealthMetadata bounds reads and rejects non-regular/symlink metadata.
func readHealthMetadata(path string) ([]byte, error) {
	root, err := os.OpenRoot(filepath.Dir(path))
	if err != nil {
		return nil, err
	}
	defer root.Close()
	return readHealthMetadataAt(root, filepath.Base(path))
}

func readHealthMetadataAt(root *os.Root, path string) ([]byte, error) {
	info, err := root.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > repositoryMetadataMaxBytes {
		return nil, fmt.Errorf("repository metadata must be a bounded regular file")
	}
	f, err := root.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err = f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > repositoryMetadataMaxBytes {
		return nil, fmt.Errorf("repository metadata must be a bounded regular file")
	}
	data, err := io.ReadAll(io.LimitReader(f, repositoryMetadataMaxBytes+1))
	if len(data) > repositoryMetadataMaxBytes {
		return nil, fmt.Errorf("repository metadata exceeds the size limit")
	}
	return data, err
}

func (e *Engine) runLightweightRepositoryHealth(ctx context.Context, p Payload) (string, map[string]any, string) {
	if err := ctx.Err(); err != nil {
		return "failed", nil, err.Error()
	}
	spec, ok, err := parseRepositorySpec(p.Extra["repository"])
	if err != nil || !ok {
		return "failed", nil, "valid repository payload is required"
	}
	result := map[string]any{"repository_type": spec.Type, "health_check_mode": "lightweight"}
	if spec.Type == "nas" {
		if err := nassvc.NewService().EnsureMounted(ctx, *spec.TargetNAS); err != nil {
			return "failed", nasRepositoryMountErrorResult(err), err.Error()
		}
	}
	repoPath, root, err := filesystemRepositoryOwnershipPath(spec)
	if err != nil {
		return "failed", result, err.Error()
	}
	if spec.Ownership == nil {
		result["error_code"] = "REPOSITORY_OWNERSHIP_INVALID"
		return "failed", result, "repository ownership payload is required"
	}
	remaining := time.Until(deadlineOrNow(ctx))
	if remaining <= 0 {
		return "failed", result, context.DeadlineExceeded.Error()
	}
	err = runWithTimeout(ctx, remaining, func() error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if _, err := validateRepositoryCleanupPath(repoPath, root); err != nil {
			return err
		}
		handle, err := os.OpenRoot(repoPath)
		if err != nil {
			return err
		}
		defer handle.Close()
		raw, err := readHealthMetadataAt(handle, filesystemRepositoryFormatFile)
		if err != nil {
			return fmt.Errorf("repository format could not be read: %w", err)
		}
		var format struct {
			UniqueID string `json:"uniqueID"`
			KeyAlgo  string `json:"keyAlgo"`
		}
		if json.Unmarshal(raw, &format) != nil || format.UniqueID == "" || format.KeyAlgo == "" {
			return fmt.Errorf("repository format file is invalid")
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		if info, err := handle.Lstat(".hyperfilelens"); err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			if err != nil && !errors.Is(err, os.ErrNotExist) {
				return fmt.Errorf("repository ownership metadata could not be read: %w", err)
			}
			return fmt.Errorf("%w: metadata directory is missing or invalid", errLightweightOwnershipInvalid)
		}
		raw, err = readHealthMetadataAt(handle, repositoryOwnershipMarkerPath)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				return fmt.Errorf("%w: marker is missing", errLightweightOwnershipInvalid)
			}
			return fmt.Errorf("repository ownership marker could not be read: %w", err)
		}
		var marker repositoryOwnershipMarker
		if json.Unmarshal(raw, &marker) != nil {
			return fmt.Errorf("%w: marker format", errLightweightOwnershipInvalid)
		}
		if err := requireMatchingRepositoryOwner(marker, *spec.Ownership); err != nil {
			return fmt.Errorf("%w: marker mismatch", errLightweightOwnershipInvalid)
		}
		return nil
	})
	if err != nil {
		if errors.Is(err, errLightweightOwnershipInvalid) && ctx.Err() == nil {
			result["error_code"] = "REPOSITORY_OWNERSHIP_INVALID"
		}
		return "failed", result, err.Error()
	}
	result["repository_path"] = repoPath
	result["ownership_verified"] = true
	return "success", result, ""
}

func deadlineOrNow(ctx context.Context) time.Time {
	if deadline, ok := ctx.Deadline(); ok {
		return deadline
	}
	return time.Now().Add(time.Minute)
}
