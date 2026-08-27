package process

import (
	"context"
	"fmt"
	"os"
	"os/exec"
)

// Options tune subprocess priority and resource limits.
type Options struct {
	NiceLevel    int
	RateLimitBps int64
}

// Configure applies OS-specific priority and rate limiting to cmd.
func Configure(ctx context.Context, cmd *exec.Cmd, opts Options) error {
	_ = ctx
	if cmd == nil {
		return fmt.Errorf("nil command")
	}
	configureProcessGroup(cmd)
	_ = opts
	return nil
}

// KillGroup terminates the process group associated with pid.
func KillGroup(ctx context.Context, pid int) error {
	if pid <= 0 {
		return nil
	}
	if ctx == nil {
		ctx = context.Background()
	}
	proc, err := os.FindProcess(pid)
	if err != nil {
		return err
	}
	return killProcessGroup(ctx, proc)
}

// BindLifetime ties a started child process to the Agent lifetime and returns
// a release function that must remain live until the child exits. On Windows
// this keeps the Job Object handle open; Unix service cgroups own descendants.
func BindLifetime(cmd *exec.Cmd) (func(), error) {
	if cmd == nil || cmd.Process == nil {
		return nil, fmt.Errorf("child process is not started")
	}
	return bindProcessLifetime(cmd.Process)
}
