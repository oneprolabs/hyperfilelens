package disk

import (
	"fmt"
	"path/filepath"
	"runtime"
	"sort"
	"strings"

	"github.com/shirou/gopsutil/v4/disk"
)

// StoragePool describes one de-duplicated local filesystem or network share.
// Multiple mount points may refer to the same underlying storage pool.
type StoragePool struct {
	Key         string   `json:"key"`
	Kind        string   `json:"kind"`
	Device      string   `json:"device"`
	FSType      string   `json:"fs_type"`
	MountPoints []string `json:"mount_points"`
	TotalBytes  uint64   `json:"total_bytes"`
	UsedBytes   uint64   `json:"used_bytes"`
	FreeBytes   uint64   `json:"free_bytes"`
}

// StorageInventory separates host-local storage from network-mounted storage.
type StorageInventory struct {
	LocalPools   []StoragePool `json:"local_pools"`
	NetworkPools []StoragePool `json:"network_pools"`
}

type usageReader func(string) (*disk.UsageStat, error)

// HostStorageInventory returns de-duplicated local and network storage pools.
func HostStorageInventory() (StorageInventory, error) {
	// Read all mounts so remote sources such as "server:/export" are not
	// filtered out by the platform library before they can be classified.
	parts, err := disk.Partitions(true)
	if err != nil && len(parts) == 0 {
		return StorageInventory{}, err
	}
	return summarizeStoragePartitions(parts, disk.Usage), nil
}

// HostLocalStorageInventory returns host-local storage without probing remote
// mount capacity. This path is safe to publish independently of network mounts.
func HostLocalStorageInventory() ([]StoragePool, error) {
	parts, err := localStoragePartitions()
	if err != nil && len(parts) == 0 {
		return nil, err
	}
	parts = storagePartitionsByKind(parts, false)
	return summarizeStoragePartitions(parts, disk.Usage).LocalPools, nil
}

// HostNetworkStorageInventory returns de-duplicated network-mounted storage.
func HostNetworkStorageInventory() ([]StoragePool, error) {
	parts, err := disk.Partitions(true)
	if err != nil && len(parts) == 0 {
		return nil, err
	}
	parts = storagePartitionsByKind(parts, true)
	return summarizeStoragePartitions(parts, disk.Usage).NetworkPools, nil
}

func storagePartitionsByKind(parts []disk.PartitionStat, remote bool) []disk.PartitionStat {
	result := make([]disk.PartitionStat, 0, len(parts))
	for _, part := range parts {
		mountPoint := normalizeMountpoint(part.Mountpoint)
		if mountPoint == "" || isSystemOnlyMount(mountPoint) {
			continue
		}
		isRemote := isNetworkFilesystem(part.Fstype, part.Device) || isRemoteMount(mountPoint)
		if isRemote == remote {
			result = append(result, part)
		}
	}
	return result
}

// HostStorageUsage returns local host storage only. Network mounts are exposed
// separately through HostStorageInventory and must not inflate host capacity.
func HostStorageUsage() (total, used, free uint64, count int, err error) {
	localPools, err := HostLocalStorageInventory()
	if err != nil {
		return 0, 0, 0, 0, err
	}
	for _, pool := range localPools {
		total += pool.TotalBytes
		used += pool.UsedBytes
		free += pool.FreeBytes
	}
	return total, used, free, len(localPools), nil
}

func summarizeStoragePartitions(parts []disk.PartitionStat, readUsage usageReader) StorageInventory {
	local := make(map[string]*StoragePool)
	network := make(map[string]*StoragePool)
	for _, part := range parts {
		mountPoint := normalizeMountpoint(part.Mountpoint)
		if mountPoint == "" || isSystemOnlyMount(mountPoint) {
			continue
		}
		remote := isNetworkFilesystem(part.Fstype, part.Device) || isRemoteMount(mountPoint)
		if !remote && !isStorageFilesystem(part.Fstype, part.Device) {
			continue
		}
		usage, err := readUsage(mountPoint)
		if err != nil || usage == nil || usage.Total == 0 {
			continue
		}

		device := strings.TrimSpace(part.Device)
		kind := "local"
		key := ""
		target := local
		if remote {
			kind = "network"
			device = remoteStorageDevice(mountPoint, device)
			key = networkStorageKey(part.Fstype, device, mountPoint)
			target = network
		} else {
			key = localStorageKey(localStorageIdentity(mountPoint, device), mountPoint)
		}
		if key == "" {
			continue
		}
		part.Device = device
		mergeStoragePool(target, key, kind, part, mountPoint, usage)
	}
	return StorageInventory{
		LocalPools:   sortedStoragePools(local),
		NetworkPools: sortedStoragePools(network),
	}
}

func isStorageFilesystem(fsType string, device string) bool {
	if isNetworkFilesystem(fsType, device) {
		return true
	}
	fs := strings.ToLower(strings.TrimSpace(fsType))
	switch fs {
	case "apfs", "bcachefs", "btrfs", "exfat", "ext2", "ext3", "ext4", "f2fs", "fat", "fat32", "fuseblk",
		"hfs", "hfs+", "iso9660", "jfs", "nilfs2", "ntfs", "ntfs3", "refs", "reiserfs",
		"udf", "ufs", "vfat", "windows-fixed", "xfs", "zfs":
		return true
	}
	return false
}

func mergeStoragePool(
	pools map[string]*StoragePool,
	key string,
	kind string,
	part disk.PartitionStat,
	mountPoint string,
	usage *disk.UsageStat,
) {
	if current, ok := pools[key]; ok {
		if !containsString(current.MountPoints, mountPoint) {
			current.MountPoints = append(current.MountPoints, mountPoint)
			sort.Strings(current.MountPoints)
		}
		// Keep total, used, and free from the same observation. Multiple
		// mount points can be sampled a few milliseconds apart, so merging
		// extrema independently could produce a contradictory capacity row.
		if usage.Total > current.TotalBytes ||
			(usage.Total == current.TotalBytes && usage.Used > current.UsedBytes) {
			current.TotalBytes = usage.Total
			current.UsedBytes = usage.Used
			current.FreeBytes = usage.Free
		}
		return
	}
	pools[key] = &StoragePool{
		Key:         key,
		Kind:        kind,
		Device:      canonicalStorageDevice(kind, part.Fstype, part.Device),
		FSType:      strings.ToLower(strings.TrimSpace(part.Fstype)),
		MountPoints: []string{mountPoint},
		TotalBytes:  usage.Total,
		UsedBytes:   usage.Used,
		FreeBytes:   usage.Free,
	}
}

func canonicalStorageDevice(kind string, fsType string, device string) string {
	device = strings.TrimSpace(device)
	if kind != "network" {
		return device
	}
	normalized := strings.ReplaceAll(device, `\`, "/")
	fs := strings.ToLower(strings.TrimSpace(fsType))
	if fs == "cifs" || fs == "smbfs" || strings.HasPrefix(normalized, "//") {
		parts := strings.Split(strings.TrimPrefix(normalized, "//"), "/")
		if len(parts) >= 2 && parts[0] != "" && parts[1] != "" {
			return fmt.Sprintf("//%s/%s", parts[0], parts[1])
		}
	}
	return device
}

func sortedStoragePools(values map[string]*StoragePool) []StoragePool {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	result := make([]StoragePool, 0, len(keys))
	for _, key := range keys {
		result = append(result, *values[key])
	}
	return result
}

func isNetworkFilesystem(fsType string, device string) bool {
	fs := strings.ToLower(strings.TrimSpace(fsType))
	switch fs {
	case "cifs", "smbfs", "nfs", "nfs4", "sshfs", "fuse.sshfs", "davfs", "ceph", "glusterfs":
		return true
	}
	device = strings.TrimSpace(device)
	return strings.HasPrefix(device, "//") || strings.HasPrefix(device, `\\`)
}

func localStorageKey(device string, mountPoint string) string {
	device = strings.TrimSpace(device)
	if runtime.GOOS == "windows" {
		device = strings.ToLower(strings.ReplaceAll(device, "/", `\`))
	}
	if device != "" {
		return "local:device:" + device
	}
	return "local:mount:" + comparableStoragePath(mountPoint)
}

func networkStorageKey(fsType string, device string, mountPoint string) string {
	fs := strings.ToLower(strings.TrimSpace(fsType))
	device = strings.TrimSpace(strings.ReplaceAll(device, `\`, "/"))
	if fs == "cifs" || fs == "smbfs" || strings.HasPrefix(device, "//") {
		parts := strings.Split(strings.TrimPrefix(device, "//"), "/")
		if len(parts) >= 2 && parts[0] != "" && parts[1] != "" {
			return fmt.Sprintf("network:smb:%s/%s", strings.ToLower(parts[0]), strings.ToLower(parts[1]))
		}
	}
	if device != "" {
		return fmt.Sprintf("network:%s:%s", fs, strings.ToLower(strings.TrimRight(device, "/")))
	}
	return fmt.Sprintf("network:%s:mount:%s", fs, comparableStoragePath(mountPoint))
}

func comparableStoragePath(value string) string {
	clean := filepath.Clean(strings.TrimSpace(value))
	if runtime.GOOS == "windows" {
		clean = strings.ToLower(strings.ReplaceAll(clean, "/", `\`))
	}
	return clean
}

func isSystemOnlyMount(mountPoint string) bool {
	if runtime.GOOS == "windows" {
		return false
	}
	clean := filepath.Clean(mountPoint)
	return clean == "/boot" || strings.HasPrefix(clean, "/boot/")
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func normalizeMountpoint(mountpoint string) string {
	clean := strings.TrimSpace(mountpoint)
	if runtime.GOOS == "windows" {
		clean = strings.ReplaceAll(clean, "/", `\`)
		if len(clean) == 2 && clean[1] == ':' {
			return strings.ToUpper(string(clean[0])) + `:\`
		}
		if len(clean) >= 3 && clean[1] == ':' && clean[2] == '\\' {
			return strings.ToUpper(string(clean[0])) + clean[1:]
		}
	}
	return clean
}
