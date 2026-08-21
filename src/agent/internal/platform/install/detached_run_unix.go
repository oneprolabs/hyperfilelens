//go:build !windows

package install

import (
	"fmt"
	"os/exec"
	"runtime"
	"strings"
	"syscall"
	"time"
)

// startDetachedShellScript runs scriptPath outside the agent service cgroup when possible.
func startDetachedShellScript(
	unitPrefix, scriptPath string,
	userInstall bool,
	log func(string),
) error {
	if runtime.GOOS == "linux" {
		if err := startLinuxTransientScript(
			unitPrefix,
			scriptPath,
			userInstall,
			log,
		); err == nil {
			return nil
		} else if log != nil {
			log(fmt.Sprintf("systemd-run unavailable, falling back to setsid: %v", err))
		}
	}
	if runtime.GOOS == "darwin" {
		return startDarwinDetachedScript(scriptPath, log)
	}
	return startSetsidScript(scriptPath, log)
}

func shellSingleQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}

func startDarwinDetachedScript(scriptPath string, log func(string)) error {
	// launchctl bootout can terminate descendants of the LaunchDaemon job; nohup+background
	// detaches before install.sh stop runs.
	cmd := exec.Command(
		"bash",
		"-c",
		fmt.Sprintf("nohup bash %s </dev/null >/dev/null 2>&1 &", shellSingleQuote(scriptPath)),
	)
	if err := cmd.Start(); err != nil {
		if log != nil {
			log(fmt.Sprintf("failed to start detached script: %v", err))
		}
		return fmt.Errorf("start detached script: %w", err)
	}
	go func() {
		err := cmd.Wait()
		if log != nil {
			if err != nil {
				log(fmt.Sprintf("detached launcher exited with error: %v", err))
			}
		}
	}()
	return nil
}

func startLinuxTransientScript(
	unitPrefix, scriptPath string,
	userInstall bool,
	log func(string),
) error {
	if _, err := exec.LookPath("systemd-run"); err != nil {
		return fmt.Errorf("systemd-run not found: %w", err)
	}
	unit := fmt.Sprintf("%s-%d", unitPrefix, time.Now().Unix())
	cmd := exec.Command(
		"systemd-run",
		systemdRunArgs(unit, scriptPath, userInstall)...,
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		if log != nil {
			log(fmt.Sprintf("systemd-run failed: %v (%s)", err, strings.TrimSpace(string(out))))
		}
		return fmt.Errorf("systemd-run: %w (%s)", err, strings.TrimSpace(string(out)))
	}
	if log != nil {
		log(fmt.Sprintf("started transient unit %s", unit))
	}
	return nil
}

// systemdRunArgs deliberately avoids --collect and Type=oneshot: both are
// rejected by the systemd 219 systemd-run shipped with CentOS 7. A transient
// unit is required here, since a setsid child remains in the agent service
// cgroup and is killed when the upgrade stops that service.
func systemdRunArgs(unit, scriptPath string, userInstall bool) []string {
	args := []string{
		"--unit=" + unit,
		"--property=KillMode=process",
	}
	if userInstall {
		args = append([]string{"--user"}, args...)
	}
	return append(args, "/bin/bash", scriptPath)
}

func startSetsidScript(scriptPath string, log func(string)) error {
	cmd := exec.Command("bash", scriptPath)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		if log != nil {
			log(fmt.Sprintf("failed to start detached script: %v", err))
		}
		return fmt.Errorf("start detached script: %w", err)
	}
	go func() {
		err := cmd.Wait()
		if log != nil {
			if err != nil {
				log(fmt.Sprintf("detached script exited with error: %v", err))
			} else {
				log("detached script process exited")
			}
		}
	}()
	return nil
}
