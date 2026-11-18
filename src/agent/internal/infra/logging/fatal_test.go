package logging

import (
	"context"
	"errors"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"gopkg.in/natefinch/lumberjack.v2"
)

func TestCriticalErrorIsWrittenBeforeAcknowledgement(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.log")
	// Start with a full queue and no consumer. The critical message must wait
	// for capacity, not follow normal logging's drop-on-full behavior.
	p := &policyLog{
		writer: &lumberjack.Logger{Filename: path},
		queue:  make(chan logWrite, 1),
		stop:   make(chan struct{}),
		done:   make(chan struct{}),
	}
	_, _ = p.Write([]byte("previous startup message\n"))
	old := slog.Default()
	slog.SetDefault(slog.New(newUnifiedHandler(p, nil)))
	t.Cleanup(func() {
		slog.SetDefault(old)
		_ = p.Close()
	})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	result := make(chan error, 1)
	go func() {
		result <- ErrorAndFlush(ctx, "agent service stopped with error", errors.New("HFL-STARTUP-003: migration failed"))
	}()
	go p.run()
	if err := <-result; err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path) // No Close or sleep before reading.
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	if !strings.HasPrefix(text, "previous startup message\n") ||
		!strings.Contains(text, `"err":"HFL-STARTUP-003: migration failed"`) {
		t.Fatalf("missing/incorrectly ordered diagnostics: %s", text)
	}
}

func TestCriticalWriteTimeoutWhenQueueIsFull(t *testing.T) {
	p := &policyLog{queue: make(chan logWrite, 1), stop: make(chan struct{})}
	p.queue <- logWrite{data: []byte("queued")}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := p.writeAndWait(ctx, []byte("fatal")); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("writeAndWait()=%v, want timeout", err)
	}
}

func TestCriticalWriteTimeoutWhileDiskWriterIsBlocked(t *testing.T) {
	p := &policyLog{
		writer: &lumberjack.Logger{Filename: filepath.Join(t.TempDir(), "agent.log")},
		queue:  make(chan logWrite, 1),
		stop:   make(chan struct{}),
		done:   make(chan struct{}),
	}
	t.Cleanup(func() { _ = p.Close() })
	// Simulate stalled I/O without ever accessing a real slow disk.
	policyLogs.Lock()
	go p.run()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	err := p.writeAndWait(ctx, []byte("fatal"))
	cancel()
	policyLogs.Unlock()
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("writeAndWait()=%v, want timeout", err)
	}
}

func TestCriticalWriteReportsFileErrorsAndClosedWriter(t *testing.T) {
	blocker := filepath.Join(t.TempDir(), "not-a-directory")
	if err := os.WriteFile(blocker, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	p := &policyLog{
		writer: &lumberjack.Logger{Filename: filepath.Join(blocker, "agent.log")},
		queue:  make(chan logWrite, 1),
		stop:   make(chan struct{}),
		done:   make(chan struct{}),
	}
	go p.run()
	t.Cleanup(func() { _ = p.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := p.writeAndWait(ctx, []byte("fatal")); err == nil {
		t.Fatal("file write failure was not returned")
	}
	_ = p.Close()
	if err := p.writeAndWait(ctx, []byte("fatal")); err == nil {
		t.Fatal("closed writer was reported as writable")
	}
}
