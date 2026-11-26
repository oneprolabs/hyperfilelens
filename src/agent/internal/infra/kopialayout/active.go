package kopialayout

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"hyperfilelens/agent/internal/platform/filelock"
	"hyperfilelens/agent/internal/platform/vfs"
)

type ActiveRoot struct {
	Path           string `json:"path"`
	Generation     int64  `json:"generation"`
	ParentIdentity string `json:"parent_identity,omitempty"`
}

func ReadActiveRoot(root string) (ActiveRoot, error) {
	a := ActiveRoot{Path: vfs.AgentCacheDir(root)}
	err := loadJSON(root, managementPath(root, "active"), &a)
	if os.IsNotExist(err) {
		if err := plainPath(root, a.Path); err != nil {
			return a, err
		}
		return a, nil
	}
	if err != nil {
		return a, err
	}
	if !filepath.IsAbs(a.Path) || a.Generation < 1 {
		return a, fmt.Errorf("invalid active cache root")
	}
	if a.ParentIdentity == "" {
		return a, fmt.Errorf("active cache parent identity missing")
	}
	id, err := directoryIdentity(filepath.Dir(a.Path))
	if err != nil || id != a.ParentIdentity {
		return a, fmt.Errorf("active cache mount changed or is unavailable")
	}
	if err := validateOwnedOrAbsent(root, a.Path); err != nil {
		return a, err
	}
	return a, nil
}

// ActiveCacheRoot fails closed on corrupt state. Callers must not silently use
// the default root and create a second active cache after a damaged manifest.
func ActiveCacheRoot(root string) string {
	a, err := ReadActiveRoot(root)
	if err != nil {
		return filepath.Join(root, "invalid-cache-state")
	}
	return a.Path
}

func acquireAdmission(ctx context.Context, root string) (func(), error) {
	if err := privateDir(root, RuntimeDir(root)); err != nil {
		return nil, err
	}
	for {
		if _, err := ReadActiveRoot(root); err != nil {
			return nil, err
		}
		if _, err := os.Lstat(managementPath(root, "relocation")); err == nil {
			var j Relocation
			if err := loadJSON(root, managementPath(root, "relocation"), &j); err != nil {
				return nil, err
			}
			if j.Stage != "completed" && j.Stage != "failed" && j.Stage != "switched_cleanup_pending" {
				select {
				case <-ctx.Done():
					return nil, ctx.Err()
				case <-time.After(100 * time.Millisecond):
					continue
				}
			}
		} else if !os.IsNotExist(err) {
			return nil, err
		}
		release, err := filelock.Acquire(ctx, filepath.Join(RuntimeDir(root), "migration.lock"), true)
		if err != nil {
			return nil, err
		}
		// Recheck after acquiring: the drain intent may have been persisted while
		// this reader waited for the lock.
		var j Relocation
		err = loadJSON(root, managementPath(root, "relocation"), &j)
		if os.IsNotExist(err) || err == nil && (j.Stage == "completed" || j.Stage == "failed" || j.Stage == "switched_cleanup_pending") {
			return release, nil
		}
		release()
		if err != nil {
			return nil, err
		}
	}
}

// A reference-counted usage context lets an already admitted operation open
// nested clients or transfer a lease to a detached server while draining. It
// does not admit unrelated operations, and the OS lock survives the parent.
type usageContextKey struct{}
type usageLease struct {
	mu     sync.Mutex
	root   string
	count  int
	unlock func()
}

func (l *usageLease) retain() func() {
	l.mu.Lock()
	if l.count == 0 {
		l.mu.Unlock()
		return nil
	}
	l.count++
	l.mu.Unlock()
	var once sync.Once
	return func() { once.Do(l.drop) }
}
func (l *usageLease) drop() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.count--
	if l.count == 0 {
		l.unlock()
	}
}
func AcquireUsageContext(ctx context.Context, root string) (context.Context, func(), error) {
	if existing, ok := ctx.Value(usageContextKey{}).(*usageLease); ok && samePath(existing.root, root) {
		if release := existing.retain(); release != nil {
			return ctx, release, nil
		}
	}
	release, err := acquireAdmission(ctx, root)
	if err != nil {
		return ctx, nil, err
	}
	lease := &usageLease{root: root, count: 1, unlock: release}
	var once sync.Once
	return context.WithValue(ctx, usageContextKey{}, lease), func() { once.Do(lease.drop) }, nil
}

// EnsureActiveRoot recreates only an installation-owned cache on its verified
// parent filesystem. It never creates a parallel legacy root after relocation.
func EnsureActiveRoot(root string) (ActiveRoot, error) {
	a, err := ReadActiveRoot(root)
	if err != nil {
		return a, err
	}
	if a.Generation == 0 {
		return a, privateDir(root, a.Path)
	}
	if _, err := os.Lstat(a.Path); os.IsNotExist(err) {
		if err := os.Mkdir(a.Path, 0o700); err != nil {
			return a, err
		}
		if err := saveJSON(a.Path, filepath.Join(a.Path, ownerFile), ownerID(root)); err != nil {
			return a, err
		}
	} else if err != nil {
		return a, err
	}
	if _, err := os.Lstat(filepath.Join(a.Path, ownerFile)); os.IsNotExist(err) {
		if err := validateOwnedOrAbsent(root, a.Path); err != nil {
			return a, err
		}
		if err := saveJSON(a.Path, filepath.Join(a.Path, ownerFile), ownerID(root)); err != nil {
			return a, err
		}
	} else if err != nil {
		return a, err
	}
	return a, nil
}
