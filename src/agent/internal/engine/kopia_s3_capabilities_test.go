package engine

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestKopiaS3CapabilityProbeFailureRemainsRetryable(t *testing.T) {
	var calls int
	cache := kopiaS3CapabilityCache{probe: func(context.Context, string) (kopiaS3Capabilities, error) {
		calls++
		if calls == 1 {
			return kopiaS3Capabilities{}, context.DeadlineExceeded
		}
		return kopiaS3Capabilities{urlStyle: true, credentialsFile: true}, nil
	}}
	bin := filepath.Join(t.TempDir(), "kopia")
	if _, err := cache.load(context.Background(), bin); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("first probe = %v, want original transient failure", err)
	}
	for range 2 {
		result, err := cache.load(context.Background(), bin)
		if err != nil || !result.urlStyle || !result.credentialsFile {
			t.Fatalf("retry = %#v, %v", result, err)
		}
	}
	if calls != 2 {
		t.Fatalf("probe calls = %d, want failed probe plus one successful probe", calls)
	}
}

func TestKopiaS3CapabilityConcurrentTasksShareProbe(t *testing.T) {
	var calls atomic.Int32
	cache := kopiaS3CapabilityCache{probe: func(context.Context, string) (kopiaS3Capabilities, error) {
		calls.Add(1)
		return kopiaS3Capabilities{urlStyle: true, credentialsFile: true}, nil
	}}
	bin := filepath.Join(t.TempDir(), "kopia")
	var workers sync.WaitGroup
	start := make(chan struct{})
	for range 16 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			<-start
			result, err := cache.load(context.Background(), bin)
			if err != nil || !result.urlStyle || !result.credentialsFile {
				t.Errorf("probe = %#v, %v", result, err)
			}
		}()
	}
	close(start)
	workers.Wait()
	if calls.Load() != 1 {
		t.Fatalf("probe calls = %d, want 1", calls.Load())
	}
}

func TestKopiaS3CapabilityWaitCanBeCancelled(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	cache := kopiaS3CapabilityCache{probe: func(context.Context, string) (kopiaS3Capabilities, error) {
		close(entered)
		<-release
		return kopiaS3Capabilities{credentialsFile: true}, nil
	}}
	bin := filepath.Join(t.TempDir(), "kopia")
	finished := make(chan error, 1)
	go func() {
		_, err := cache.load(context.Background(), bin)
		finished <- err
	}()
	<-entered
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := cache.load(ctx, bin)
	close(release)
	if firstErr := <-finished; firstErr != nil {
		t.Fatal(firstErr)
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("waiting task = %v, want cancellation", err)
	}
}

func TestKopiaS3CapabilityCacheInvalidatesWhenBinaryChanges(t *testing.T) {
	bin := filepath.Join(t.TempDir(), "kopia")
	if err := os.WriteFile(bin, []byte("old"), 0o600); err != nil {
		t.Fatal(err)
	}
	var calls int
	cache := kopiaS3CapabilityCache{probe: func(context.Context, string) (kopiaS3Capabilities, error) {
		calls++
		return kopiaS3Capabilities{credentialsFile: calls > 1}, nil
	}}
	first, err := cache.load(context.Background(), bin)
	if err != nil || first.credentialsFile {
		t.Fatalf("old binary = %#v, %v", first, err)
	}
	if err := os.WriteFile(bin, []byte("new patched binary"), 0o600); err != nil {
		t.Fatal(err)
	}
	second, err := cache.load(context.Background(), bin)
	if err != nil || !second.credentialsFile || calls != 2 {
		t.Fatalf("replaced binary = %#v, %v; calls = %d", second, err, calls)
	}
}

func TestKopiaS3CapabilityExecutionFailureIsNotMissingSupport(t *testing.T) {
	_, err := probeKopiaS3Capabilities(context.Background(), filepath.Join(t.TempDir(), "missing-kopia"))
	if err == nil || !strings.Contains(err.Error(), "capability check failed") {
		t.Fatalf("missing executable = %v, want probe execution error", err)
	}
	if strings.Contains(err.Error(), "does not support") || strings.Contains(err.Error(), "update") {
		t.Fatalf("probe failure misclassified as unsupported binary: %v", err)
	}
}
