//go:build unix

package process

import (
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

func killProcessGroup(proc *os.Process) {
	if proc == nil {
		return
	}
	_ = syscall.Kill(-proc.Pid, syscall.SIGKILL)
}
