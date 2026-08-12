package process

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
)

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

	var stdoutBuf, stderrBuf bytes.Buffer
	var wg sync.WaitGroup
	capture := func(reader io.Reader, stderr bool, buf *bytes.Buffer) {
		defer wg.Done()
		captureProgressLines(reader, stderr, buf, onLine)
	}

	wg.Add(2)
	if captureStdout {
		go capture(stdoutPipe, false, &stdoutBuf)
	} else {
		go capture(stdoutPipe, false, nil)
	}
	go capture(stderrPipe, true, &stderrBuf)

	stopKill := startContextProcessGroupKill(ctx, cmd)
	runErr := cmd.Wait()
	stopKill()
	wg.Wait()

	res := Result{
		Stdout: stringsTrim(stdoutBuf.String()),
		Stderr: stringsTrim(stderrBuf.String()),
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
	buf *bytes.Buffer,
	onLine OutputLineHandler,
) {
	br := bufio.NewReader(reader)
	for {
		line, err := readProgressLine(br)
		if line != "" {
			if buf != nil {
				buf.WriteString(line)
				buf.WriteByte('\n')
			}
			if onLine != nil {
				onLine(line, stderr)
			}
		}
		if err != nil {
			return
		}
	}
}

func readProgressLine(r *bufio.Reader) (string, error) {
	var out []byte
	for {
		b, err := r.ReadByte()
		if err != nil {
			if err == io.EOF && len(out) > 0 {
				return string(out), io.EOF
			}
			return string(out), err
		}
		if b == '\r' {
			if peek, _ := r.Peek(1); len(peek) > 0 && peek[0] == '\n' {
				_, _ = r.ReadByte()
			}
			return string(out), nil
		}
		if b == '\n' {
			return string(out), nil
		}
		out = append(out, b)
	}
}
