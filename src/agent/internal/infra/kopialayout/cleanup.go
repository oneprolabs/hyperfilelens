package kopialayout

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"hyperfilelens/agent/internal/platform/filelock"
	"hyperfilelens/agent/internal/platform/vfs"
)

// ClearCache is intentionally conservative: neither an arbitrary directory nor
// a previously completed migration authorizes deleting newly introduced files.
func ClearCache(ctx context.Context, root string) error {
	if err := privateDir(root, RuntimeDir(root)); err != nil {
		return err
	}
	release, err := filelock.Acquire(ctx, filepath.Join(RuntimeDir(root), "migration.lock"), false)
	if err != nil {
		return err
	}
	defer release()
	data, err := readRegular(root, StatePath(root), 32<<20)
	if err != nil {
		return fmt.Errorf("cache cleanup requires a completed migration: %w", err)
	}
	m := migrator{root: root}
	if err := json.Unmarshal(data, &m.state); err != nil || m.state.Version != 1 || m.state.Status != "completed" || m.state.Entries == nil {
		return fmt.Errorf("cache cleanup requires a completed migration")
	}
	if err := CheckUsers(ctx, root, false); err != nil {
		return err
	}
	if err := plainPath(root, vfs.AgentCacheDir(root)); err != nil {
		return err
	}
	m.state.Status = "discovering"
	if err := m.save(); err != nil {
		return err
	}
	if err := m.discover(ctx); err != nil {
		return err
	}
	for _, e := range m.state.Entries {
		if e.Phase != "removed" {
			m.state.Status = "copying"
			if err := m.save(); err != nil {
				return err
			}
			return fmt.Errorf("non-cache files require migration before cache cleanup")
		}
	}
	if len(m.state.Retained) != 0 {
		m.state.Status = "incomplete"
		if err := m.save(); err != nil {
			return err
		}
		return &IncompleteError{Count: len(m.state.Retained)}
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := os.RemoveAll(vfs.AgentCacheDir(root)); err != nil {
		return err
	}
	if err := syncDir(root); err != nil {
		return err
	}
	m.state.Status = "completed"
	return m.save()
}
