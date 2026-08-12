//go:build !windows

package disk

import "github.com/shirou/gopsutil/v4/disk"

func localStoragePartitions() ([]disk.PartitionStat, error) {
	return disk.Partitions(true)
}

func isRemoteMount(string) bool {
	return false
}

func remoteStorageDevice(_ string, device string) string {
	return device
}

func localStorageIdentity(_ string, device string) string {
	return device
}
