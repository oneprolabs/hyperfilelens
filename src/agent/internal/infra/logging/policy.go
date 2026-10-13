package logging

import (
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"gopkg.in/natefinch/lumberjack.v2"
)

var runtimeLevel slog.LevelVar
var policyLogs = struct {
	sync.Mutex
	days     int
	budgetMB int
	files    map[string]*policyLog
	dropped  atomic.Int64
}{budgetMB: 500, files: make(map[string]*policyLog)}

type policyLog struct {
	path      string
	mirror    bool
	writer    *lumberjack.Logger
	closed    bool
	lastError error
	queue     chan []byte
	stop      chan struct{}
	closing   atomic.Bool
	tailMu    sync.Mutex
	tail      []byte
}

// All runtime writers share the budget. Errors are observable but never fail a
// subprocess because stdout/stderr logging is not a business operation.
func (p *policyLog) Write(data []byte) (int, error) {
	p.tailMu.Lock()
	if len(data) > 64*1024 {
		dataTail := data[len(data)-64*1024:]
		p.tail = append(p.tail[:0], dataTail...)
	} else {
		p.tail = append(p.tail, data...)
		if len(p.tail) > 64*1024 {
			p.tail = append([]byte(nil), p.tail[len(p.tail)-64*1024:]...)
		}
	}
	p.tailMu.Unlock()
	if p.closing.Load() || len(data) > 256*1024 {
		return len(data), nil
	}
	copyData := append([]byte(nil), data...)
	select {
	case p.queue <- copyData:
	default:
		policyLogs.dropped.Add(1)
	} // bounded best-effort logging.
	return len(data), nil
}

// RuntimeLogTail keeps startup diagnostics independent of optional disk I/O.
func RuntimeLogTail(writer io.Writer) string {
	p, ok := writer.(*policyLog)
	if !ok {
		return ""
	}
	p.tailMu.Lock()
	defer p.tailMu.Unlock()
	lines := strings.Split(strings.TrimSpace(string(p.tail)), "\n")
	if len(lines) > 20 {
		lines = lines[len(lines)-20:]
	}
	return strings.Join(lines, "\n")
}

func (p *policyLog) Close() error {
	if p.closing.CompareAndSwap(false, true) {
		close(p.stop)
	}
	return nil
}

func (p *policyLog) run() {
	defer func() {
		policyLogs.Lock()
		defer policyLogs.Unlock()
		p.closed = true
		if p.writer != nil {
			_ = p.writer.Close()
			p.writer = nil
		}
	}()
	for {
		select {
		case <-p.stop:
			return
		default:
		}
		select {
		case <-p.stop:
			return
		case data := <-p.queue:
			policyLogs.Lock()
			if p.mirror {
				_, _ = (stderrWriter{}).Write(data)
			}
			if p.writer != nil {
				_, p.lastError = p.writer.Write(data)
			}
			policyLogs.Unlock()
		}
	}
}

func runtimeLogWriter(path string, mirror bool) *policyLog {
	if !policyLogs.TryLock() {
		policyLogs.dropped.Add(1)
		discard := &policyLog{closed: true}
		discard.closing.Store(true)
		return discard
	}
	defer policyLogs.Unlock()
	if existing := policyLogs.files[path]; existing != nil && !existing.closing.Load() {
		return existing
	}
	writer := &policyLog{path: path, mirror: mirror, queue: make(chan []byte, 64), stop: make(chan struct{})}
	writer.writer = &lumberjack.Logger{Filename: path,
		MaxSize:    max(1, policyLogs.budgetMB/(maxLogBackups+1)),
		MaxBackups: maxLogBackups, MaxAge: policyLogs.days}
	policyLogs.files[path] = writer
	go writer.run()
	go func() { policyLogs.Lock(); defer policyLogs.Unlock(); reconfigureLogsLocked() }()
	return writer
}

// RuntimeLog uses only a caller-owned fixed runtime-log path, never a remotely
// supplied path. The caller remains responsible for the directory boundary.
func RuntimeLog(path string) io.WriteCloser {
	return runtimeLogWriter(path, false)
}

func reconfigureLogsLocked() {
	count := 0
	for _, file := range policyLogs.files {
		if !file.closed {
			count++
		}
	}
	if count == 0 {
		return
	}
	perFile := policyLogs.budgetMB / count / (maxLogBackups + 1)
	if perFile < 1 {
		perFile = 1
	}
	if perFile > maxLogSizeMB {
		perFile = maxLogSizeMB
	}
	for _, file := range policyLogs.files {
		if file.closed {
			continue
		}
		if file.writer != nil {
			_ = file.writer.Close()
		}
		file.writer = &lumberjack.Logger{
			Filename: file.path, MaxSize: perFile, MaxBackups: maxLogBackups,
			MaxAge: policyLogs.days, LocalTime: false,
		}
	}
}

// ApplyPolicy is independent of Agent startup and task success.
func ApplyPolicy(level string, days, budgetMB int) error {
	levels := map[string]slog.Level{"debug": slog.LevelDebug, "info": slog.LevelInfo,
		"warning": slog.LevelWarn, "error": slog.LevelError}
	selected, ok := levels[level]
	if !ok || days < 1 || days > 365 || budgetMB < 32 || budgetMB > 8192 {
		return errors.New("invalid runtime logging policy")
	}
	policyLogs.Lock()
	defer policyLogs.Unlock()
	if len(policyLogs.files) == 0 {
		return errors.New("runtime file logging is unavailable")
	}
	policyLogs.days, policyLogs.budgetMB = days, budgetMB
	reconfigureLogsLocked()
	runtimeLevel.Set(selected)
	return nil
}

type logCandidate struct {
	path     string
	size     int64
	modified time.Time
	info     os.FileInfo
}

var rotatedSuffix = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.\d{3}\.log$`)

// Sweep removes only exact lumberjack backups of registered files. Active logs,
// install logs, symlinks and unrelated data are never deleted.
func Sweep(now time.Time) error {
	policyLogs.Lock()
	defer policyLogs.Unlock()
	var candidates []logCandidate
	var total int64
	var firstErr error
	if policyLogs.dropped.Swap(0) > 0 {
		firstErr = errors.New("runtime log capacity unavailable; business execution was preserved")
	}
	for _, file := range policyLogs.files {
		if file.lastError != nil {
			firstErr = file.lastError
		}
		dir, base := filepath.Dir(file.path), filepath.Base(file.path)
		entries, err := os.ReadDir(dir)
		if err != nil {
			if file.closed && os.IsNotExist(err) {
				delete(policyLogs.files, file.path)
				continue
			}
			firstErr = err
			continue
		}
		prefix := strings.TrimSuffix(base, ".log") + "-"
		for _, entry := range entries {
			name := entry.Name()
			active := name == base
			backup := strings.HasPrefix(name, prefix) && rotatedSuffix.MatchString(strings.TrimPrefix(name, prefix))
			if !active && !backup {
				continue
			}
			if entry.Type()&os.ModeSymlink != 0 {
				continue
			}
			info, err := entry.Info()
			if err != nil || !info.Mode().IsRegular() {
				continue
			}
			total += info.Size()
			if backup || (active && file.closed) {
				candidates = append(candidates, logCandidate{filepath.Join(dir, name), info.Size(), info.ModTime(), info})
			}
		}
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].modified.Before(candidates[j].modified) })
	for _, file := range candidates {
		expired := policyLogs.days > 0 && now.Sub(file.modified) > time.Duration(policyLogs.days)*24*time.Hour
		if !expired && total <= int64(policyLogs.budgetMB)*1024*1024 {
			continue
		}
		// Recheck regular-file identity immediately before removing a backup.
		info, err := os.Lstat(file.path)
		if err != nil || !info.Mode().IsRegular() || !os.SameFile(info, file.info) {
			continue
		}
		if err := os.Remove(file.path); err != nil {
			firstErr = err
		} else {
			total -= file.size
			if registered := policyLogs.files[file.path]; registered != nil && registered.closed {
				delete(policyLogs.files, file.path)
			}
		}
	}
	if total > int64(policyLogs.budgetMB)*1024*1024 && firstErr == nil {
		firstErr = errors.New("active runtime logs exceed the soft disk budget")
	}
	return firstErr
}
