package enroll

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

func useTestInstallLogPath(t *testing.T, path string) {
	t.Helper()
	previous := resolveInstallLogPath
	resolveInstallLogPath = func() string { return path }
	t.Cleanup(func() { resolveInstallLogPath = previous })
}

func TestCommandLoggingDoesNotWriteBeforeCommit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "logs", "install.log")
	useTestInstallLogPath(t, path)

	finish := StartCommandLogging()
	fmt.Fprintln(os.Stdout, "preflight output")
	finish()

	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("log file exists before commit: %v", err)
	}
}

func TestCommandLoggingFlushesBufferedAndLiveOutput(t *testing.T) {
	path := filepath.Join(t.TempDir(), "logs", "install.log")
	useTestInstallLogPath(t, path)

	finish := StartCommandLogging()
	fmt.Fprintln(os.Stdout, "buffered preflight output")
	commitInstallLog()
	fmt.Fprintln(os.Stdout, "live stdout output")
	fmt.Fprintln(os.Stderr, "live stderr output")
	finish()

	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read install log: %v", err)
	}
	for _, expected := range []string{
		"buffered preflight output",
		"Install log enabled",
		"live stdout output",
		"live stderr output",
	} {
		if !strings.Contains(string(content), expected) {
			t.Errorf("install log does not contain %q: %s", expected, content)
		}
	}
	timestampedLine := regexp.MustCompile(`(?m)^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\] `)
	if !timestampedLine.Match(content) {
		t.Fatalf("install log does not contain UTC RFC3339 timestamps: %s", content)
	}
}

func TestCommandLoggingSanitizesTerminalControlSequences(t *testing.T) {
	path := filepath.Join(t.TempDir(), "logs", "install.log")
	useTestInstallLogPath(t, path)

	finish := StartCommandLogging()
	commitInstallLog()
	fmt.Fprint(os.Stdout, "\r\033[35m[....] Download 10%\033[0m\033[K")
	fmt.Fprint(os.Stdout, "\r\033[32m[....] Download 100%\033[0m\033[K\n")
	finish()

	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read install log: %v", err)
	}
	message := string(content)
	if strings.Contains(message, "\033[") || strings.Contains(message, "10%") {
		t.Fatalf("install log contains terminal control output: %q", message)
	}
	if !strings.Contains(message, "[....] Download 100%") {
		t.Fatalf("install log does not contain final progress: %q", message)
	}
}

func TestCommandLoggingCommitIsIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "logs", "install.log")
	useTestInstallLogPath(t, path)

	finish := StartCommandLogging()
	fmt.Fprintln(os.Stdout, "written once")
	commitInstallLog()
	commitInstallLog()
	finish()

	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read install log: %v", err)
	}
	if count := strings.Count(string(content), "written once"); count != 1 {
		t.Fatalf("buffered output count = %d, want 1: %s", count, content)
	}
}

func TestCommandLoggingFailureDoesNotExposeLogPath(t *testing.T) {
	tempDir := t.TempDir()
	blockingFile := filepath.Join(tempDir, "not-a-directory")
	if err := os.WriteFile(blockingFile, []byte("block"), 0o600); err != nil {
		t.Fatalf("create blocking file: %v", err)
	}
	useTestInstallLogPath(t, filepath.Join(blockingFile, "install.log"))

	finish := StartCommandLogging()
	commitInstallLog()
	if path := activeInstallLogPath(); path != "" {
		t.Errorf("active log path = %q after failed commit, want empty", path)
	}
	finish()
}
