package process

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

// Result holds captured subprocess output.
type Result struct {
	Stdout           string
	Stderr           string
	ExitCode         int
	StdoutTotalBytes int64
	StderrTotalBytes int64
	StdoutTruncated  bool
	StderrTruncated  bool
}

// Run starts bin with args, optional extra env pairs (KEY=value), and workDir.
// The process is killed when ctx is cancelled.
func Run(
	ctx context.Context,
	bin string,
	args []string,
	extraEnv map[string]string,
	workDir string,
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
	stdout := newBoundedTailBuffer(streamStdoutLimit)
	stderr := newBoundedTailBuffer(streamStderrLimit)
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		return Result{}, err
	}
	stopKill, err := startContextProcessGroupKill(ctx, cmd)
	if err != nil {
		_ = killProcessGroup(context.Background(), cmd.Process)
		_ = cmd.Wait()
		return Result{}, err
	}
	defer stopKill()
	waitErr := cmd.Wait()
	res := Result{
		Stdout:           stringsTrim(stdout.String()),
		Stderr:           stringsTrim(stderr.String()),
		StdoutTotalBytes: stdout.TotalBytes(),
		StderrTotalBytes: stderr.TotalBytes(),
		StdoutTruncated:  stdout.Truncated(),
		StderrTruncated:  stderr.Truncated(),
	}
	if waitErr == nil {
		return res, nil
	}
	if ctx.Err() != nil {
		return res, ctx.Err()
	}
	if exitErr, ok := waitErr.(*exec.ExitError); ok {
		res.ExitCode = exitErr.ExitCode()
		return res, fmt.Errorf("exit %d: %w", res.ExitCode, waitErr)
	}
	return res, waitErr
}

func mapToEnv(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k, v := range m {
		out = append(out, k+"="+v)
	}
	return out
}

func stringsTrim(s string) string {
	return strings.TrimSpace(s)
}
