package logging

import (
	"log/slog"
	"os"
	"path/filepath"
	"strconv"
)

const (
	maxLogSizeMB  = 100
	maxLogBackups = 4 // 4 rotated + active agent.log → 5 files max, 100MB each (~500MB cap)
	logFileName   = "agent.log"
)

// stderrWriter mirrors logs to stderr when available. Windows services often have
// no stderr sink; failures there must not block agent.log writes.
type stderrWriter struct{}

func (stderrWriter) Write(p []byte) (int, error) {
	n, err := os.Stderr.Write(p)
	if err != nil {
		return len(p), nil
	}
	return n, err
}

// Setup configures the default slog logger with the unified text format.
func Setup(logDir string, orgKey string) error {
	defaultOrgKey = orgKey
	hostPID = hostnamePID()

	if err := os.MkdirAll(logDir, 0o755); err != nil {
		slog.SetDefault(slog.New(newUnifiedHandler(stderrWriter{}, &slog.HandlerOptions{Level: &runtimeLevel})))
		return nil
	}
	logPath := filepath.Join(logDir, logFileName)
	if err := touchLogFile(logPath); err != nil {
		slog.SetDefault(slog.New(newUnifiedHandler(stderrWriter{}, &slog.HandlerOptions{Level: &runtimeLevel})))
		return nil
	}
	mw := runtimeLogWriter(logPath, true)
	h := newUnifiedHandler(mw, &slog.HandlerOptions{
		Level: &runtimeLevel,
	})
	slog.SetDefault(slog.New(h))
	return nil
}

func touchLogFile(path string) error {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	return f.Close()
}

func hostnamePID() string {
	host, err := os.Hostname()
	if err != nil || host == "" {
		host = "unknown"
	}
	return host + ":" + strconv.Itoa(os.Getpid())
}
