package process

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
)

const (
	streamStdoutLimit = 128 * 1024
	streamStderrLimit = 64 * 1024
	streamLineLimit   = 8 * 1024
)

type boundedTailBuffer struct {
	limit     int
	data      []byte
	total     int64
	truncated bool
}

func newBoundedTailBuffer(limit int) *boundedTailBuffer {
	return &boundedTailBuffer{limit: max(0, limit)}
}

func (b *boundedTailBuffer) Write(p []byte) (int, error) {
	b.total += int64(len(p))
	b.appendTail(p)
	return len(p), nil
}

func (b *boundedTailBuffer) appendTail(p []byte) {
	if b.limit == 0 {
		b.truncated = b.truncated || len(p) > 0
		return
	}
	if len(p) >= b.limit {
		b.data = append(b.data[:0], p[len(p)-b.limit:]...)
		b.truncated = b.truncated || b.total > int64(b.limit)
		return
	}
	overflow := len(b.data) + len(p) - b.limit
	if overflow > 0 {
		copy(b.data, b.data[overflow:])
		b.data = b.data[:len(b.data)-overflow]
		b.truncated = true
	}
	b.data = append(b.data, p...)
}

func (b *boundedTailBuffer) WriteCaptured(p []byte, rawBytes int64, contentTruncated bool) {
	b.total += rawBytes
	b.appendTail(p)
	b.truncated = b.truncated || contentTruncated
}

func (b *boundedTailBuffer) String() string    { return string(b.data) }
func (b *boundedTailBuffer) TotalBytes() int64 { return b.total }
func (b *boundedTailBuffer) Truncated() bool   { return b.truncated }

// OutputLineHandler receives one stdout/stderr line while a subprocess runs.
type OutputLineHandler func(line string, stderr bool)

// RunStreaming starts bin with args and invokes onLine for each output line.
// Lines are split on newline or carriage return so cliprint-style progress works.
func RunStreaming(
	ctx context.Context,
	bin string,
	args []string,
	extraEnv map[string]string,
	workDir string,
	onLine OutputLineHandler,
) (Result, error) {
	return runStreaming(ctx, bin, args, extraEnv, workDir, onLine, true)
}

// RunStreamingDiscardStdout streams stdout without retaining it in memory.
// Stderr remains captured so callers can return a useful command failure.
func RunStreamingDiscardStdout(
	ctx context.Context,
	bin string,
	args []string,
	extraEnv map[string]string,
	workDir string,
	onLine OutputLineHandler,
) (Result, error) {
	return runStreaming(ctx, bin, args, extraEnv, workDir, onLine, false)
}

func runStreaming(
	ctx context.Context,
	bin string,
	args []string,
	extraEnv map[string]string,
	workDir string,
	onLine OutputLineHandler,
	captureStdout bool,
) (Result, error) {
	if bin == "" {
		return Result{}, fmt.Errorf("empty binary path")
	}
	cmd := exec.Command(bin, args...)
	configureProcessGroup(cmd)
	if workDir != "" {
		cmd.Dir = workDir
	}
	if len(extraEnv) > 0 {
		cmd.Env = append(os.Environ(), mapToEnv(extraEnv)...)
	}

	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return Result{}, err
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		return Result{}, err
	}

	if err := cmd.Start(); err != nil {
		return Result{}, err
	}

	stdoutLimit := streamStdoutLimit
	if !captureStdout {
		stdoutLimit = 0
	}
	stdoutBuf := newBoundedTailBuffer(stdoutLimit)
	stderrBuf := newBoundedTailBuffer(streamStderrLimit)
	var wg sync.WaitGroup
	capture := func(reader io.Reader, stderr bool, buf *boundedTailBuffer) {
		defer wg.Done()
		captureProgressLines(reader, stderr, buf, onLine)
	}

	wg.Add(2)
	go capture(stdoutPipe, false, stdoutBuf)
	go capture(stderrPipe, true, stderrBuf)

	stopKill, err := startContextProcessGroupKill(ctx, cmd)
	if err != nil {
		killProcessGroup(cmd.Process)
		_ = cmd.Wait()
		wg.Wait()
		return Result{}, err
	}
	runErr := cmd.Wait()
	stopKill()
	wg.Wait()

	res := Result{
		Stdout:           stringsTrim(stdoutBuf.String()),
		Stderr:           stringsTrim(stderrBuf.String()),
		StdoutTotalBytes: stdoutBuf.TotalBytes(),
		StderrTotalBytes: stderrBuf.TotalBytes(),
		StdoutTruncated:  stdoutBuf.Truncated(),
		StderrTruncated:  stderrBuf.Truncated(),
	}
	if runErr == nil {
		return res, nil
	}
	if ctx.Err() != nil {
		return res, ctx.Err()
	}
	if exitErr, ok := runErr.(*exec.ExitError); ok {
		res.ExitCode = exitErr.ExitCode()
		return res, fmt.Errorf("exit %d: %w", res.ExitCode, runErr)
	}
	return res, runErr
}

func captureProgressLines(
	reader io.Reader,
	stderr bool,
	buf *boundedTailBuffer,
	onLine OutputLineHandler,
) {
	br := bufio.NewReader(reader)
	for {
		line, rawBytes, truncated, terminated, err := readProgressLine(br)
		captured := []byte(line)
		if terminated {
			captured = append(captured, '\n')
		}
		if rawBytes > 0 {
			buf.WriteCaptured(captured, rawBytes, truncated)
		}
		if line != "" {
			if onLine != nil {
				onLine(line, stderr)
			}
		}
		if err != nil {
			return
		}
	}
}

func readProgressLine(r *bufio.Reader) (string, int64, bool, bool, error) {
	out := make([]byte, 0, streamLineLimit)
	var rawBytes int64
	truncated := false
	for {
		b, err := r.ReadByte()
		if err != nil {
			if err == io.EOF && len(out) > 0 {
				return string(out), rawBytes, truncated, false, io.EOF
			}
			return string(out), rawBytes, truncated, false, err
		}
		rawBytes++
		if b == '\r' {
			if peek, _ := r.Peek(1); len(peek) > 0 && peek[0] == '\n' {
				_, _ = r.ReadByte()
				rawBytes++
			}
			return string(out), rawBytes, truncated, true, nil
		}
		if b == '\n' {
			return string(out), rawBytes, truncated, true, nil
		}
		if len(out) < streamLineLimit {
			out = append(out, b)
		} else {
			truncated = true
		}
	}
}
