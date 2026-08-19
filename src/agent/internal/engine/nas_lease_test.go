package engine

import (
	"context"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"hyperfilelens/agent/internal/platform/vfs"
)

func TestNASLeaseExclusiveWaitsForActiveReader(t *testing.T) {
	registry := newNASLeaseRegistry()
	releaseReader, err := registry.acquire(context.Background(), []string{"/managed/source"}, false)
	if err != nil {
		t.Fatal(err)
	}

	acquired := make(chan func(), 1)
	go func() {
		release, acquireErr := registry.acquire(context.Background(), []string{"/managed/source"}, true)
		if acquireErr == nil {
			acquired <- release
		}
	}()

	select {
	case release := <-acquired:
		release()
		t.Fatal("exclusive lease acquired while a reader was active")
	case <-time.After(30 * time.Millisecond):
	}

	releaseReader()
	select {
	case release := <-acquired:
		release()
	case <-time.After(time.Second):
		t.Fatal("exclusive lease did not acquire after reader released")
	}
}

func TestTaskScopedEnginesShareNASLeaseCoordinator(t *testing.T) {
	coordinator := NewNASLeaseCoordinator()
	first := NewWithNASLeaseCoordinator(nil, coordinator)
	second := NewWithNASLeaseCoordinator(nil, coordinator)
	if first.nasLeases() != second.nasLeases() {
		t.Fatal("task-scoped engines do not share the Agent NAS lease registry")
	}
}

func TestZeroValueEngineInitializesNASLeasesSafely(t *testing.T) {
	engine := &Engine{}
	registries := make(chan *nasLeaseRegistry, 16)
	var workers sync.WaitGroup
	for range 16 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			registries <- engine.nasLeases()
		}()
	}
	workers.Wait()
	close(registries)

	var first *nasLeaseRegistry
	for registry := range registries {
		if first == nil {
			first = registry
			continue
		}
		if registry != first {
			t.Fatal("zero-value Engine initialized more than one lease registry")
		}
	}
}

func TestNASLeaseWaitHonorsContextCancellation(t *testing.T) {
	registry := newNASLeaseRegistry()
	releaseReader, err := registry.acquire(context.Background(), []string{"/managed/source"}, false)
	if err != nil {
		t.Fatal(err)
	}
	defer releaseReader()

	ctx, cancel := context.WithCancel(context.Background())
	canceled := make(chan error, 1)
	go func() {
		_, acquireErr := registry.acquire(ctx, []string{"/managed/source"}, true)
		canceled <- acquireErr
	}()
	cancel()

	select {
	case acquireErr := <-canceled:
		if acquireErr != context.Canceled {
			t.Fatalf("acquire error=%v", acquireErr)
		}
	case <-time.After(time.Second):
		t.Fatal("lease wait did not stop after context cancellation")
	}
}

func TestNASLeaseWriterIsNotStarvedByNewReaders(t *testing.T) {
	registry := newNASLeaseRegistry()
	releaseFirstReader, err := registry.acquire(context.Background(), []string{"/managed/source"}, false)
	if err != nil {
		t.Fatal(err)
	}

	order := make(chan string, 2)
	releases := make(chan func(), 2)
	go func() {
		release, acquireErr := registry.acquire(context.Background(), []string{"/managed/source"}, true)
		if acquireErr == nil {
			order <- "writer"
			releases <- release
		}
	}()
	waitForNASLeaseWaiters(t, registry, "/managed/source", 1)
	go func() {
		release, acquireErr := registry.acquire(context.Background(), []string{"/managed/source"}, false)
		if acquireErr == nil {
			order <- "reader"
			releases <- release
		}
	}()

	releaseFirstReader()
	select {
	case first := <-order:
		if first != "writer" {
			t.Fatalf("first lease=%s want=writer", first)
		}
	case <-time.After(time.Second):
		t.Fatal("writer did not acquire")
	}
	(<-releases)()
	select {
	case second := <-order:
		if second != "reader" {
			t.Fatalf("second lease=%s want=reader", second)
		}
	case <-time.After(time.Second):
		t.Fatal("reader did not acquire after writer")
	}
	(<-releases)()
}

func waitForNASLeaseWaiters(
	t *testing.T,
	registry *nasLeaseRegistry,
	path string,
	want int,
) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		registry.mu.Lock()
		entry := registry.entries[path]
		registry.mu.Unlock()
		if entry != nil {
			entry.mu.Lock()
			count := len(entry.waiters)
			entry.mu.Unlock()
			if count >= want {
				return
			}
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("lease waiters did not reach %d", want)
}

func TestNASLeasePathsIncludeSourceAndNASRepository(t *testing.T) {
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	sourceMount := filepath.Join(vfs.MountCustomDir(dataDir), "source")
	targetMount := filepath.Join(vfs.MountCustomDir(dataDir), "target")
	payload := Payload{Extra: map[string]any{
		"nas": map[string]any{
			"protocol": "nfs", "server": "192.0.2.10", "export_path": "/source", "mount_point": sourceMount,
		},
		"repository": map[string]any{
			"type": "nas",
			"nas": map[string]any{
				"protocol": "nfs", "server": "192.0.2.20", "export_path": "/target", "mount_point": targetMount,
			},
		},
	}}

	paths := nasLeasePaths(payload)
	want := normalizedLeasePaths([]string{sourceMount, targetMount})
	if len(paths) != 2 || paths[0] != want[0] || paths[1] != want[1] {
		t.Fatalf("lease paths=%v", paths)
	}
}

func TestNASLeasePathsIncludeFlatNASPayload(t *testing.T) {
	dataDir := t.TempDir()
	t.Setenv("HFL_DATA_DIR", dataDir)
	mountPoint := filepath.Join(vfs.MountCustomDir(dataDir), "source")
	payload := Payload{Extra: map[string]any{
		"resource_id": 7,
		"protocol":    "nfs",
		"mount_point": mountPoint,
	}}

	paths := nasLeasePaths(payload)
	if len(paths) != 1 || paths[0] != mountPoint {
		t.Fatalf("lease paths=%v want=[%s]", paths, mountPoint)
	}
}
