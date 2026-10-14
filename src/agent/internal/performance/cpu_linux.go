//go:build linux

package performance

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"hyperfilelens/agent/internal/model"
)

var cpuBaseline struct {
	path     string
	value    string
	captured bool
}

func systemdCPUQuota(ctx context.Context, cfg *model.AgentConfig, quota string) error {
	args := []string{"--runtime", "--no-ask-password"}
	if cfg != nil && (cfg.InstallationMode == model.InstallationModeUser || cfg.InstallationMode == model.InstallationModeUserContinuous) {
		args = append(args, "--user")
	}
	args = append(args, "set-property", "hyperfilelens-agent.service", "CPUQuota="+quota)
	callCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	return exec.CommandContext(callCtx, "systemctl", args...).Run()
}

func cpuQuotaForSystemd(raw string) string {
	fields := strings.Fields(raw)
	if len(fields) != 2 || fields[0] == "max" {
		return "infinity"
	}
	quota, quotaErr := strconv.ParseFloat(fields[0], 64)
	period, periodErr := strconv.ParseFloat(fields[1], 64)
	if quotaErr != nil || periodErr != nil || quota <= 0 || period <= 0 {
		return "infinity"
	}
	return strconv.FormatFloat(quota/period*100, 'f', 3, 64) + "%"
}

// Use only the current HFL service's cgroup, never a parent slice or another
// user's service. No sudo, systemctl restarts or persistent host edits.
func applyCPU(ctx context.Context, cfg *model.AgentConfig, cores int) Item {
	raw, err := os.ReadFile("/proc/self/cgroup")
	if err != nil {
		return Item{"unsupported", "Cannot identify the Agent resource group."}
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if !strings.HasPrefix(line, "0::") {
			continue
		}
		path := strings.TrimPrefix(line, "0::")
		if filepath.Base(path) != "hyperfilelens-agent.service" ||
			!strings.HasPrefix(path, "/") || strings.Contains(path, "..") {
			continue
		}
		file := filepath.Join("/sys/fs/cgroup", path, "cpu.max")
		old, err := os.ReadFile(file)
		if err != nil {
			break
		}
		fields := strings.Fields(string(old))
		if len(fields) != 2 {
			break
		}
		period, err := strconv.ParseInt(fields[1], 10, 64)
		if err != nil || period < 1000 || period > 1000000 {
			break
		}
		if !cpuBaseline.captured {
			cpuBaseline.path = file
			cpuBaseline.value = string(old)
			cpuBaseline.captured = true
		}
		value := strconv.FormatInt(int64(cores)*period, 10) + " " + fields[1]
		if err := os.WriteFile(file, []byte(value), 0600); err != nil {
			break // The user manager may own this controller instead.
		}
		return Item{"applied", "Shared service cgroup CPU-time quota; memory remains a soft budget."}
	}
	owned := false
	for _, line := range strings.Split(string(raw), "\n") {
		parts := strings.SplitN(line, ":", 3)
		if len(parts) == 3 && filepath.Base(parts[2]) == "hyperfilelens-agent.service" {
			owned = true
		}
	}
	if !owned {
		return Item{"unsupported", "Agent is not in its dedicated service cgroup; no host-wide policy is changed."}
	}
	if err := systemdCPUQuota(ctx, cfg, strconv.Itoa(cores*100)+"%"); err == nil {
		return Item{"applied", "Shared systemd service CPU-time quota; no service restart or privilege escalation."}
	}
	return Item{"unsupported", "A writable CPU controller or authorized service manager is required; business continues."}
}

func ProcessStarted(_ int) {} // Children inherit the service cgroup.

func clearCPU(ctx context.Context, cfg *model.AgentConfig) Item {
	if !cpuBaseline.captured {
		return Item{"disabled", "CPU governance disabled; no CPU constraint was changed by this policy."}
	}
	baseline := cpuBaseline
	if err := os.WriteFile(baseline.path, []byte(baseline.value), 0600); err == nil {
		cpuBaseline = struct {
			path     string
			value    string
			captured bool
		}{}
		return Item{"disabled", "CPU governance disabled; the Agent service's previous CPU policy was restored."}
	}
	if err := systemdCPUQuota(ctx, cfg, cpuQuotaForSystemd(baseline.value)); err == nil {
		cpuBaseline = struct {
			path     string
			value    string
			captured bool
		}{}
		return Item{"disabled", "CPU governance disabled; the Agent service's previous CPU policy was restored."}
	}
	return Item{"degraded", "CPU policy could not be restored; the previous service policy remains unchanged."}
}
