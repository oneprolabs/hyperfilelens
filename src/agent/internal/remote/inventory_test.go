package remote

import (
	"testing"

	agentdisk "hyperfilelens/agent/internal/platform/disk"
)

func TestStorageInventoryPayloadSummarizesOnlyLocalPools(t *testing.T) {
	payload := storageInventoryPayload(agentdisk.StorageInventory{
		LocalPools: []agentdisk.StoragePool{
			{Key: "local:first", TotalBytes: 40, UsedBytes: 8, FreeBytes: 32},
			{Key: "local:second", TotalBytes: 60, UsedBytes: 12, FreeBytes: 48},
		},
		NetworkPools: []agentdisk.StoragePool{
			{Key: "network:smb:share", TotalBytes: 500, UsedBytes: 100, FreeBytes: 400},
		},
	})

	if got := payload["disk_total_bytes"]; got != uint64(100) {
		t.Fatalf("disk_total_bytes = %v, want 100", got)
	}
	if got := payload["storage_inventory_status"]; got != "ready" {
		t.Fatalf("storage_inventory_status = %v, want ready", got)
	}
	if got := payload["network_storage_inventory_status"]; got != "ready" {
		t.Fatalf("network_storage_inventory_status = %v, want ready", got)
	}
	if got := payload["disk_used_bytes"]; got != uint64(20) {
		t.Fatalf("disk_used_bytes = %v, want 20", got)
	}
	if got := payload["disk_count"]; got != 2 {
		t.Fatalf("disk_count = %v, want 2", got)
	}
}

func TestStorageInventoryPayloadClearsLegacySummaryWhenLocalPoolsEmpty(t *testing.T) {
	payload := storageInventoryPayload(agentdisk.StorageInventory{
		NetworkPools: []agentdisk.StoragePool{
			{Key: "network:smb:share", TotalBytes: 500, UsedBytes: 100, FreeBytes: 400},
		},
	})

	for _, key := range []string{
		"disk_total_bytes",
		"disk_used_bytes",
		"disk_free_bytes",
		"disk_count",
	} {
		if got := payload[key]; got != uint64(0) && got != 0 {
			t.Fatalf("%s = %v, want 0", key, got)
		}
	}
}
