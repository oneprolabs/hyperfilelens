package remote

import (
	"context"
	"os"
	"runtime"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/mem"

	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/model"
	agentdisk "hyperfilelens/agent/internal/platform/disk"
	"hyperfilelens/agent/internal/platform/hostinfo"
	"hyperfilelens/agent/internal/platform/kopia"
	"hyperfilelens/agent/internal/platform/networkinventory"
	"hyperfilelens/agent/internal/platform/vfs"
	"hyperfilelens/agent/internal/selfupdate"
	"hyperfilelens/agent/internal/wire"
)

// SendInventory emits a heartbeat frame with host and bundle metadata for the control plane.
func SendInventory(
	ctx context.Context,
	sink wire.Sender,
	provider config.Provider,
	storagePayload map[string]any,
) error {
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
	installationMode := cfg.InstallationMode
	if installationMode == "" {
		installationMode = model.InstallationModeSystem
	}
	rootPath := cfg.AgentRoot
	if rootPath == "" {
		rootPath = vfs.AgentRootForMode(installationMode)
	}
	for key, value := range map[string]any{
		"agent_version":     selfupdate.Version,
		"agent_commit":      selfupdate.Commit,
		"role":              string(cfg.Role),
		"installation_mode": string(installationMode),
		"run_as_user":       cfg.RunAsUser,
		"os":                runtime.GOOS,
		"arch":              runtime.GOARCH,
		"hostname":          hostname(),
		"kopia_path":        cfg.KopiaPath,
		// root_path is the source-host browsing root and must remain the
		// Agent data directory for backend compatibility. The installer-owned
		// unified root is reported separately.
		"root_path":    dataDir,
		"data_path":    dataDir,
		"agent_root":   rootPath,
		"install_path": vfs.InstallDirForMode(installationMode),
		"capabilities": []string{
			"task_command_ack_v1",
			"repository_operation_v1",
			"repository_cleanup_v1",
			"repository_cleanup_v2",
			"repository_cleanup_ownership_v1",
			"repository_cleanup_s3_v1",
			"repository_cleanup_s3_md5_v2",
			"repository_ownership_v1",
			"backup_prepared_snapshot_v1",
			"backup_operation_reconcile_v1",
			"snapshot_browse_v1",
			"snapshot_artifact_upload_v1",
			"snapshot_scope_resolve_v1",
			"insight_safe_restore_v1",
			"nas_mount_lifecycle_v1",
			"network_inventory_v1",
			"agent_upgrade_download_progress_v1",
			"repository_server_port_range_v1",
			"detached_uninstall_v2",
			"storage_inventory_v1",
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
	for key, value := range storagePayload {
		payload[key] = value
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

// EmptyStorageInventoryPayload returns an explicit empty structured inventory.
// The zero-valued summary clears capacity reported by older Agent versions.
func EmptyStorageInventoryPayload() map[string]any {
	return map[string]any{
		"storage_inventory_status":         "pending",
		"network_storage_inventory_status": "pending",
		"local_storage_pools":              []agentdisk.StoragePool{},
		"network_storage_pools":            []agentdisk.StoragePool{},
		"disk_total_bytes":                 uint64(0),
		"disk_used_bytes":                  uint64(0),
		"disk_free_bytes":                  uint64(0),
		"disk_count":                       0,
	}
}

// CollectStorageInventoryPayload returns the current structured storage
// inventory. The list capacity is derived exclusively from host-local pools.
func CollectStorageInventoryPayload() (map[string]any, error) {
	localPools, err := agentdisk.HostLocalStorageInventory()
	if err != nil {
		return nil, err
	}
	return localStorageInventoryPayload(localPools), nil
}

func storageInventoryPayload(storage agentdisk.StorageInventory) map[string]any {
	payload := localStorageInventoryPayload(storage.LocalPools)
	payload["network_storage_pools"] = storage.NetworkPools
	payload["network_storage_inventory_status"] = "ready"
	return payload
}

func localStorageInventoryPayload(localPools []agentdisk.StoragePool) map[string]any {
	payload := EmptyStorageInventoryPayload()
	payload["storage_inventory_status"] = "ready"
	payload["local_storage_pools"] = localPools

	var total, used, free uint64
	for _, pool := range localPools {
		total += pool.TotalBytes
		used += pool.UsedBytes
		free += pool.FreeBytes
	}
	payload["disk_total_bytes"] = total
	payload["disk_used_bytes"] = used
	payload["disk_free_bytes"] = free
	payload["disk_count"] = len(localPools)
	return payload
}

// CollectNetworkStorageInventory returns the current remote mount inventory.
func CollectNetworkStorageInventory() ([]agentdisk.StoragePool, error) {
	return agentdisk.HostNetworkStorageInventory()
}

func hostname() string {
	h, err := os.Hostname()
	if err != nil {
		return ""
	}
	return h
}
