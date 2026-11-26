package kopialayout

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestDemandDrivenProbeRetriesExecutionFailure(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix fake command")
	}
	root := t.TempDir()
	bin, marker := filepath.Join(root, "kopia"), filepath.Join(root, "first-call")
	script := fmt.Sprintf("#!/bin/sh\nif [ ! -f %q ]; then touch %q; exit 1; fi\necho --cache-demand-driven\n", marker, marker)
	if err := os.WriteFile(bin, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	if SupportsDemandDriven(context.Background(), bin) {
		t.Fatal("failed probe must not advertise support")
	}
	if !SupportsDemandDriven(context.Background(), bin) {
		t.Fatal("transient command failure was permanently cached as unsupported for the unchanged patched binary")
	}
}

func capabilityTestBinary(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "kopia")
	if err := os.WriteFile(path, []byte("binary-identity"), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestDemandDrivenCapabilityObservationsAreShared(t *testing.T) {
	for _, supported := range []bool{false, true} {
		t.Run(fmt.Sprint(supported), func(t *testing.T) {
			var calls atomic.Int32
			cache := demandDrivenCapabilityCache{probe: func(context.Context, string) (bool, error) {
				calls.Add(1)
				return supported, nil
			}}
			bin := capabilityTestBinary(t)
			start := make(chan struct{})
			var workers sync.WaitGroup
			for range 16 {
				workers.Add(1)
				go func() {
					defer workers.Done()
					<-start
					got, err := cache.load(context.Background(), bin)
					if err != nil || got != supported {
						t.Errorf("probe = %v, %v; want %v", got, err, supported)
					}
				}()
			}
			close(start)
			workers.Wait()
			if calls.Load() != 1 {
				t.Fatalf("successful observation made %d probes, want 1", calls.Load())
			}
		})
	}
}

func TestDemandDrivenCapabilityErrorsAreNotCached(t *testing.T) {
	for _, failure := range []error{context.Canceled, context.DeadlineExceeded, errors.New("execution failed")} {
		t.Run(failure.Error(), func(t *testing.T) {
			calls := 0
			cache := demandDrivenCapabilityCache{probe: func(context.Context, string) (bool, error) {
				calls++
				if calls == 1 {
					return false, failure
				}
				return true, nil
			}}
			bin := capabilityTestBinary(t)
			if _, err := cache.load(context.Background(), bin); !errors.Is(err, failure) {
				t.Fatalf("first probe = %v, want original error", err)
			}
			for range 2 {
				if supported, err := cache.load(context.Background(), bin); err != nil || !supported {
					t.Fatalf("retry = %v, %v", supported, err)
				}
			}
			if calls != 2 {
				t.Fatalf("probe calls = %d, want failed probe and successful retry", calls)
			}
		})
	}
}

func TestDemandDrivenCapabilityWaitCanBeCancelled(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	cache := demandDrivenCapabilityCache{probe: func(context.Context, string) (bool, error) {
		close(entered)
		<-release
		return true, nil
	}}
	bin := capabilityTestBinary(t)
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
		t.Fatalf("cancelled waiter = %v", err)
	}
	if supported, err := cache.load(context.Background(), bin); err != nil || !supported {
		t.Fatalf("cancelled waiter poisoned successful result: %v, %v", supported, err)
	}
}

func TestDemandDrivenCapabilityInvalidatesReplacedBinary(t *testing.T) {
	bin := capabilityTestBinary(t)
	calls := 0
	cache := demandDrivenCapabilityCache{probe: func(context.Context, string) (bool, error) {
		calls++
		return calls > 1, nil
	}}
	if supported, err := cache.load(context.Background(), bin); err != nil || supported {
		t.Fatalf("old binary = %v, %v", supported, err)
	}
	if err := os.WriteFile(bin, []byte("new-patched-binary-with-a-different-size"), 0o600); err != nil {
		t.Fatal(err)
	}
	if supported, err := cache.load(context.Background(), bin); err != nil || !supported {
		t.Fatalf("new binary = %v, %v", supported, err)
	}
}

func TestDemandDrivenProbeRejectsFailedHelpAndDoesNotLeakOutput(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix fake command")
	}
	bin := filepath.Join(t.TempDir(), "kopia")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\necho '--cache-demand-driven secret=private-value'\nexit 1\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	supported, err := ProbeDemandDriven(context.Background(), bin)
	if err == nil || supported || strings.Contains(err.Error(), "private-value") {
		t.Fatalf("failed help must not enable caching or disclose output: supported=%v err=%v", supported, err)
	}
}
