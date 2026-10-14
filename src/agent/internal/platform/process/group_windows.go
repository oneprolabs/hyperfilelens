//go:build windows

package process

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

func configureProcessGroup(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.CreationFlags = syscall.CREATE_NEW_PROCESS_GROUP
}

func bindProcessLifetime(proc *os.Process) (func(), error) {
	if proc == nil {
		return nil, fmt.Errorf("child process is not started")
	}
	// A child of the Agent may already inherit the Agent's optional CPU Job.
	// Windows processes cannot be assigned to an unrelated second Job on all
	// supported versions. The inherited Job already owns the child's lifetime;
	// taskkill still handles context cancellation below.
	processHandle, err := windows.OpenProcess(
		windows.PROCESS_QUERY_LIMITED_INFORMATION,
		false,
		uint32(proc.Pid),
	)
	if err != nil {
		return nil, fmt.Errorf("open child process: %w", err)
	}
	inJob := windows.NewLazySystemDLL("kernel32.dll").NewProc("IsProcessInJob")
	var result uint32
	if ok, _, _ := inJob.Call(uintptr(processHandle), 0, uintptr(unsafe.Pointer(&result))); ok != 0 && result != 0 {
		_ = windows.CloseHandle(processHandle)
		return func() {}, nil
	}
	_ = windows.CloseHandle(processHandle)
	job, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return nil, fmt.Errorf("create job object: %w", err)
	}
	release := func() {
		_ = windows.CloseHandle(job)
	}
	info := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{}
	info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	if _, err := windows.SetInformationJobObject(
		job,
		windows.JobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&info)),
		uint32(unsafe.Sizeof(info)),
	); err != nil {
		release()
		return nil, fmt.Errorf("configure job object: %w", err)
	}
	processHandle, err = windows.OpenProcess(
		windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE,
		false,
		uint32(proc.Pid),
	)
	if err != nil {
		release()
		return nil, fmt.Errorf("open child process: %w", err)
	}
	defer windows.CloseHandle(processHandle)
	if err := windows.AssignProcessToJobObject(job, processHandle); err != nil {
		release()
		return nil, fmt.Errorf("assign child process to job object: %w", err)
	}
	return release, nil
}

func killProcessGroup(ctx context.Context, proc *os.Process) error {
	if proc == nil {
		return nil
	}
	return exec.CommandContext(ctx, "taskkill", "/F", "/T", "/PID", strconv.Itoa(proc.Pid)).Run()
}
