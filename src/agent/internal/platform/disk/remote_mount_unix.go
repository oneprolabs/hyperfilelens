//go:build !windows

package disk

import (
	"regexp"
	"runtime"
	"strings"

	"github.com/shirou/gopsutil/v4/disk"
)

func localStoragePartitions() ([]disk.PartitionStat, error) {
	return disk.Partitions(true)
}

func isRemoteMount(string) bool {
	return false
}

func remoteStorageDevice(_ string, device string) string {
	return device
}

var darwinAPFSVolumeDevice = regexp.MustCompile(`^(/dev/disk[0-9]+)s[0-9]+(?:s[0-9]+)*$`)

func localStorageIdentity(fsType string, _ string, device string) string {
	// APFS exposes each system/data/preboot/VM volume as a separate device
	// path, although they share one container and one capacity snapshot. Use
	// the parent disk as the identity so the container is counted once.
	if runtime.GOOS == "darwin" && strings.EqualFold(strings.TrimSpace(fsType), "apfs") {
		if match := darwinAPFSVolumeDevice.FindStringSubmatch(strings.TrimSpace(device)); len(match) == 2 {
			return match[1]
		}
	}
	return device
}
