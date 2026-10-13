package logging

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestUnifiedHandlerRendersStandardPrefix(t *testing.T) {
	defaultOrgKey = "888"
	hostPID = "host-agent-16:5621"

	var buf bytes.Buffer
	h := newUnifiedHandler(&buf, &slog.HandlerOptions{Level: slog.LevelInfo})
	logger := slog.New(h)

	ctx := WithTraceID(context.Background(), "8a9d1c7f")
	logger.WarnContext(ctx, "nfs mount command failed", "err", "mount: connection timed out", "server", "10.147.18.31")

	out := buf.String()
	if !strings.Contains(out, "[WARN]") {
		t.Fatalf("expected WARN level, got %q", out)
	}
	if !strings.Contains(out, "[host-agent-16:5621]") {
		t.Fatalf("expected host:pid, got %q", out)
	}
	if !strings.Contains(out, "[t-8a9d1c7f]") {
		t.Fatalf("expected trace id, got %q", out)
	}
	if !strings.Contains(out, "[org-888/-]") {
		t.Fatalf("expected org/user, got %q", out)
	}
	if !strings.Contains(out, "[agent-go(") {
		t.Fatalf("expected service(file:line), got %q", out)
	}
	if !strings.Contains(out, "nfs mount command failed:") {
		t.Fatalf("expected message prefix, got %q", out)
	}
}

func TestFormatTraceID(t *testing.T) {
	if got := formatTraceID(""); got != "-" {
		t.Fatalf("empty = %q", got)
	}
	if got := formatTraceID("abc"); got != "t-abc" {
		t.Fatalf("prefix = %q", got)
	}
	if got := formatTraceID("t-abc"); got != "t-abc" {
		t.Fatalf("already prefixed = %q", got)
	}
}

func TestSetupCreatesAgentLog(t *testing.T) {
	dir := t.TempDir()
	if err := Setup(dir, "org-test"); err != nil {
		t.Fatalf("Setup: %v", err)
	}
	path := filepath.Join(dir, logFileName)
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("agent.log missing: %v", err)
	}
	slog.Info("setup test line")
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if info, err := os.Stat(path); err == nil && info.Size() > 0 {
			break
		}
		time.Sleep(time.Millisecond)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat after log: %v", err)
	}
	if info.Size() == 0 {
		t.Fatal("expected agent.log to contain bootstrap log line")
	}
}

func TestStderrWriterDoesNotFailMultiWriter(t *testing.T) {
	var buf bytes.Buffer
	mw := io.MultiWriter(&buf, stderrWriter{})
	if _, err := mw.Write([]byte("ok")); err != nil {
		t.Fatalf("write: %v", err)
	}
	if buf.String() != "ok" {
		t.Fatalf("buffer = %q", buf.String())
	}
}
