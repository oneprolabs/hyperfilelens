//go:build unix

package process

import (
	"context"
	"os"
	"os/exec"
	"syscall"
)

func configureProcessGroup(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.Setpgid = true
}

func bindProcessLifetime(_ *os.Process) (func(), error) {
	// The Agent systemd service cgroup already owns descendant lifetime on Unix.
	return func() {}, nil
}

func killProcessGroup(_ context.Context, proc *os.Process) error {
	if proc == nil {
		return nil
	}
	err := syscall.Kill(-proc.Pid, syscall.SIGKILL)
	if err == syscall.ESRCH {
		return nil
	}
	return err
}
