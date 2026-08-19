package engine

import (
	"context"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"

	"hyperfilelens/agent/internal/service/nas"
)

type nasLeaseEntry struct {
	mu      sync.Mutex
	readers int
	writer  bool
	waiters []*nasLeaseWaiter
	refs    int
}

type nasLeaseWaiter struct {
	exclusive bool
	ready     chan struct{}
	granted   bool
}

type nasLeaseRegistry struct {
	mu      sync.Mutex
	entries map[string]*nasLeaseEntry
}

// NASLeaseCoordinator owns Agent-process mount lifetime leases. WebSocket
// tasks use separate Engine instances but share one coordinator.
type NASLeaseCoordinator struct {
	once     sync.Once
	nasLease *nasLeaseRegistry
}

// NewNASLeaseCoordinator returns a mount lifetime coordinator for one Agent.
func NewNASLeaseCoordinator() *NASLeaseCoordinator {
	return &NASLeaseCoordinator{nasLease: newNASLeaseRegistry()}
}

func (c *NASLeaseCoordinator) registry() *nasLeaseRegistry {
	c.once.Do(func() {
		if c.nasLease == nil {
			c.nasLease = newNASLeaseRegistry()
		}
	})
	return c.nasLease
}

func newNASLeaseRegistry() *nasLeaseRegistry {
	return &nasLeaseRegistry{entries: make(map[string]*nasLeaseEntry)}
}

// acquire coordinates physical mount lifetime inside one Agent process.  It is
// deliberately local: controller task state remains authoritative, while this
// lease closes the race between users of a mount and nas.unmount.
func (r *nasLeaseRegistry) acquire(ctx context.Context, paths []string, exclusive bool) (func(), error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	paths = normalizedLeasePaths(paths)
	if len(paths) == 0 {
		return func() {}, nil
	}

	r.mu.Lock()
	entries := make([]*nasLeaseEntry, 0, len(paths))
	for _, path := range paths {
		entry := r.entries[path]
		if entry == nil {
			entry = &nasLeaseEntry{}
			r.entries[path] = entry
		}
		entry.refs++
		entries = append(entries, entry)
	}
	r.mu.Unlock()

	acquired := make([]*nasLeaseEntry, 0, len(entries))
	for _, entry := range entries {
		if err := entry.acquire(ctx, exclusive); err != nil {
			for index := len(acquired) - 1; index >= 0; index-- {
				acquired[index].release(exclusive)
			}
			r.releaseEntries(paths, entries)
			return nil, err
		}
		acquired = append(acquired, entry)
	}

	return func() {
		for index := len(entries) - 1; index >= 0; index-- {
			entries[index].release(exclusive)
		}
		r.releaseEntries(paths, entries)
	}, nil
}

func (e *nasLeaseEntry) acquire(ctx context.Context, exclusive bool) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	waiter := &nasLeaseWaiter{exclusive: exclusive, ready: make(chan struct{})}
	e.mu.Lock()
	if e.canAcquireImmediately(exclusive) {
		e.grant(waiter)
	} else {
		e.waiters = append(e.waiters, waiter)
	}
	e.mu.Unlock()

	select {
	case <-waiter.ready:
		return nil
	case <-ctx.Done():
		e.mu.Lock()
		if waiter.granted {
			e.releaseGranted(exclusive)
		} else {
			for index, candidate := range e.waiters {
				if candidate == waiter {
					e.waiters = append(e.waiters[:index], e.waiters[index+1:]...)
					break
				}
			}
			e.grantWaiters()
		}
		e.mu.Unlock()
		return ctx.Err()
	}
}

func (e *nasLeaseEntry) canAcquireImmediately(exclusive bool) bool {
	if e.writer || len(e.waiters) > 0 {
		return false
	}
	return !exclusive || e.readers == 0
}

func (e *nasLeaseEntry) grant(waiter *nasLeaseWaiter) {
	waiter.granted = true
	if waiter.exclusive {
		e.writer = true
	} else {
		e.readers++
	}
	close(waiter.ready)
}

func (e *nasLeaseEntry) release(exclusive bool) {
	e.mu.Lock()
	e.releaseGranted(exclusive)
	e.mu.Unlock()
}

func (e *nasLeaseEntry) releaseGranted(exclusive bool) {
	if exclusive {
		e.writer = false
	} else {
		e.readers--
	}
	e.grantWaiters()
}

func (e *nasLeaseEntry) grantWaiters() {
	if e.writer || len(e.waiters) == 0 {
		return
	}
	if e.waiters[0].exclusive {
		if e.readers != 0 {
			return
		}
		waiter := e.waiters[0]
		e.waiters = e.waiters[1:]
		e.grant(waiter)
		return
	}
	for len(e.waiters) > 0 && !e.waiters[0].exclusive {
		waiter := e.waiters[0]
		e.waiters = e.waiters[1:]
		e.grant(waiter)
	}
}

func (r *nasLeaseRegistry) releaseEntries(paths []string, entries []*nasLeaseEntry) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for index, path := range paths {
		entry := entries[index]
		entry.refs--
		if entry.refs == 0 && r.entries[path] == entry {
			delete(r.entries, path)
		}
	}
}

func normalizedLeasePaths(paths []string) []string {
	seen := make(map[string]struct{}, len(paths))
	result := make([]string, 0, len(paths))
	for _, raw := range paths {
		path := filepath.Clean(strings.TrimSpace(raw))
		if path == "" || path == "." {
			continue
		}
		if runtime.GOOS == "windows" {
			path = strings.ToLower(path)
		}
		if _, exists := seen[path]; exists {
			continue
		}
		seen[path] = struct{}{}
		result = append(result, path)
	}
	sort.Strings(result)
	return result
}

func nasLeasePaths(p Payload) []string {
	paths := []string{}
	if mountPoint := stringValue(p.Extra["mount_point"]); mountPoint != "" {
		paths = append(paths, nas.ResolvedMountPoint(mountPoint))
	}
	if spec, ok, err := parseNASSpec(p.Extra["nas"]); err == nil && ok {
		paths = append(paths, spec.MountPoint)
	}
	if raw, ok := p.Extra["repository"].(map[string]any); ok {
		if nested, ok := raw["nas"].(map[string]any); ok {
			if spec, present, err := parseNASSpec(nested); err == nil && present {
				paths = append(paths, spec.MountPoint)
			}
		}
	}
	return normalizedLeasePaths(paths)
}

func needsExclusiveNASLease(kind string, p Payload) bool {
	if kind == "nas.unmount" || kind == "nas.mount" || kind == "repository.operation" {
		return true
	}
	cleanupAfterTest, _ := payloadBoolValue(p.Extra["cleanup_after_test"])
	return kind == "nas.test" && cleanupAfterTest
}
