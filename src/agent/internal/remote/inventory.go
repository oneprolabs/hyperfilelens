package remote

import (
	"context"
	"os"
	"runtime"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/mem"

	"hyperfilelens/agent/internal/infra/config"
	agentdisk "hyperfilelens/agent/internal/platform/disk"
	"hyperfilelens/agent/internal/platform/hostinfo"
	"hyperfilelens/agent/internal/platform/install"
	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/networkinventory"
	"hyperfilelens/agent/internal/platform/vfs"
	"hyperfilelens/agent/internal/selfupdate"
	"hyperfilelens/agent/internal/wire"
)

// SendInventory emits a heartbeat frame with host and bundle metadata for the control plane.
func SendInventory(ctx context.Context, sink wire.Sender, provider config.Provider) error {
	if sink == nil || provider == nil {
		return nil
	}
	cfg := provider.Current()
	platform := hostinfo.Collect(ctx)
	dataDir := cfg.DataDir
	if dataDir == "" {
		dataDir = vfs.DefaultAgentDataDir()
	}
	payload := platform.Inventory()
	for key, value := range map[string]any{
		"agent_version": selfupdate.Version,
		"agent_commit":  selfupdate.Commit,
		"role":          string(cfg.Role),
		"os":            runtime.GOOS,
		"arch":          runtime.GOARCH,
		"hostname":      hostname(),
		"kopia_path":    cfg.KopiaPath,
		"root_path":     dataDir,
		"install_path":  install.DefaultInstallDir(),
		"capabilities": []string{
			"task_command_ack_v1",
			"repository_operation_v1",
			"repository_cleanup_v1",
			"repository_cleanup_v2",
			"backup_prepared_snapshot_v1",
			"network_inventory_v1",
			"repository_server_port_range_v1",
			"detached_uninstall_v2",
		},
	} {
		payload[key] = value
	}
	preferredAddress := ""
	if reporter, ok := sink.(interface{ LocalIPAddress() string }); ok {
		preferredAddress = reporter.LocalIPAddress()
	}
	networkSnapshot := networkinventory.CollectWithPreferredAddress(
		ctx,
		cfg.APIBaseURL,
		preferredAddress,
	)
	if mac := networkSnapshot.PrimaryMACAddress(); mac != "" {
		payload["mac_address"] = mac
		payload["primary_mac_address"] = mac
	}
	if addresses := networkSnapshot.IPAddresses(); len(addresses) > 0 {
		payload["ip_addresses"] = addresses
		payload["primary_ip_address"] = networkSnapshot.PrimaryAddress()
		payload["primary_ip_source"] = networkSnapshot.Selection.Source
		payload["network_inventory"] = networkSnapshot
	}
	if total, used, free, count, err := agentdisk.HostStorageUsage(); err == nil && count > 0 {
		payload["disk_total_bytes"] = total
		payload["disk_used_bytes"] = used
		payload["disk_free_bytes"] = free
		payload["disk_count"] = count
	} else if total, used, free, err := agentdisk.Usage(dataDir); err == nil {
		payload["disk_total_bytes"] = total
		payload["disk_used_bytes"] = used
		payload["disk_free_bytes"] = free
		if parts, err := disk.Partitions(false); err == nil && len(parts) > 0 {
			payload["disk_count"] = len(parts)
		}
	}
	if logical, err := cpu.Counts(true); err == nil && logical > 0 {
		payload["cpu_cores"] = logical
	}
	if vm, err := mem.VirtualMemory(); err == nil && vm.Total > 0 {
		payload["memory_total_bytes"] = vm.Total
	}
	if bin := cfg.KopiaPath; bin != "" {
		if ver, err := kopia.Version(ctx, bin); err == nil {
			payload["kopia_version"] = ver
		} else {
			payload["kopia_error"] = err.Error()
		}
	}
	return sink.SendJSON(ctx, wire.NewHeartbeatWithPayload(payload))
}

func hostname() string {
	h, err := os.Hostname()
	if err != nil {
		return ""
	}
	return h
}
