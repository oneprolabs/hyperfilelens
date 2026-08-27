//go:build !windows

package install

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
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
		return startDarwinDetachedScript(scriptPath, userInstall, log)
	}
	return startSetsidScript(scriptPath, log)
}

func shellSingleQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}

type darwinLaunchdJob struct {
	domain    string
	label     string
	plistDir  string
	plistPath string
	dirMode   os.FileMode
	fileMode  os.FileMode
}

func startDarwinDetachedScript(scriptPath string, userInstall bool, log func(string)) error {
	home := ""
	if userInstall {
		var err error
		home, err = os.UserHomeDir()
		if err != nil {
			return fmt.Errorf("resolve user home for detached script: %w", err)
		}
	}
	job := newDarwinLaunchdJob(userInstall, os.Geteuid(), home, time.Now().UnixNano())
	if err := os.MkdirAll(job.plistDir, job.dirMode); err != nil {
		return fmt.Errorf("create detached launchd directory: %w", err)
	}
	plist := darwinLaunchdPlist(job, scriptPath)
	if err := os.WriteFile(job.plistPath, []byte(plist), job.fileMode); err != nil {
		return fmt.Errorf("write detached launchd plist: %w", err)
	}
	cmd := exec.Command("launchctl", darwinLaunchctlBootstrapArgs(job)...)
	if out, err := cmd.CombinedOutput(); err != nil {
		_ = os.Remove(job.plistPath)
		if log != nil {
			log(fmt.Sprintf("launchctl bootstrap failed: %v (%s)", err, strings.TrimSpace(string(out))))
		}
		return fmt.Errorf("launchctl bootstrap: %w", err)
	}
	if log != nil {
		log(fmt.Sprintf("started independent launchd job %s in %s", job.label, job.domain))
	}
	return nil
}

func darwinLaunchctlBootstrapArgs(job darwinLaunchdJob) []string {
	return []string{"bootstrap", job.domain, job.plistPath}
}

func newDarwinLaunchdJob(userInstall bool, uid int, home string, nonce int64) darwinLaunchdJob {
	job := darwinLaunchdJob{
		domain:   "system",
		label:    fmt.Sprintf("com.hyperfilelens.lifecycle-%d", nonce),
		plistDir: "/Library/LaunchDaemons",
		dirMode:  0o755,
		fileMode: 0o644,
	}
	if userInstall {
		job.domain = fmt.Sprintf("gui/%d", uid)
		job.plistDir = filepath.Join(home, "Library", "LaunchAgents")
		job.dirMode = 0o700
		job.fileMode = 0o600
	}
	job.plistPath = filepath.Join(job.plistDir, job.label+".plist")
	return job
}

func darwinLaunchdPlist(job darwinLaunchdJob, scriptPath string) string {
	// Run the lifecycle script in an independent launchd job. nohup only
	// detaches from a terminal; it does not survive bootout of the parent job.
	// Remove the plist before bootout because bootout terminates this wrapper.
	command := fmt.Sprintf("/bin/bash %s; rc=$?; rm -f %s; launchctl bootout %s >/dev/null 2>&1 || true; exit $rc",
		shellSingleQuote(scriptPath), shellSingleQuote(job.plistPath), shellSingleQuote(job.domain+"/"+job.label))
	return fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>%s</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>-c</string><string>%s</string></array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><false/>
</dict></plist>
`, plistXML(job.label), plistXML(command))
}

func plistXML(value string) string {
	value = strings.ReplaceAll(value, "&", "&amp;")
	value = strings.ReplaceAll(value, "<", "&lt;")
	value = strings.ReplaceAll(value, ">", "&gt;")
	value = strings.ReplaceAll(value, "\"", "&quot;")
	return value
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
