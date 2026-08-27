//go:build windows

package disk

import (
	"errors"
	"strings"
	"unsafe"

	"github.com/shirou/gopsutil/v4/disk"
	"golang.org/x/sys/windows"
)

var wNetGetConnection = windows.NewLazySystemDLL("mpr.dll").NewProc("WNetGetConnectionW")

func localStoragePartitions() ([]disk.PartitionStat, error) {
	parts, err := fixedDrivePartitions()
	if err != nil {
		return nil, err
	}
	volumeParts, volumeErr := fixedVolumeMountPartitions()
	if volumeErr != nil {
		// Drive roots still provide a useful local inventory when volume
		// enumeration is unavailable on an older Windows host.
		return parts, nil
	}

	seen := make(map[string]struct{}, len(parts)+len(volumeParts))
	result := make([]disk.PartitionStat, 0, len(parts)+len(volumeParts))
	for _, part := range append(parts, volumeParts...) {
		key := strings.ToLower(normalizeMountpoint(part.Mountpoint))
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		result = append(result, part)
	}
	return result, nil
}

func fixedDrivePartitions() ([]disk.PartitionStat, error) {
	buffer := make([]uint16, 256)
	length, err := windows.GetLogicalDriveStrings(uint32(len(buffer)), &buffer[0])
	if err != nil {
		return nil, err
	}
	if length >= uint32(len(buffer)) {
		buffer = make([]uint16, length+1)
		length, err = windows.GetLogicalDriveStrings(uint32(len(buffer)), &buffer[0])
		if err != nil {
			return nil, err
		}
	}
	parts := make([]disk.PartitionStat, 0)
	for start := 0; start < int(length); {
		end := start
		for end < int(length) && buffer[end] != 0 {
			end++
		}
		root := windows.UTF16ToString(buffer[start:end])
		start = end + 1
		if root == "" {
			continue
		}
		path, pathErr := windows.UTF16PtrFromString(root)
		if pathErr != nil || windows.GetDriveType(path) != windows.DRIVE_FIXED {
			continue
		}
		parts = append(parts, disk.PartitionStat{
			Device:     root,
			Mountpoint: root,
			Fstype:     "windows-fixed",
		})
	}
	return parts, nil
}

func fixedVolumeMountPartitions() ([]disk.PartitionStat, error) {
	volumeBuffer := make([]uint16, 1024)
	handle, err := windows.FindFirstVolume(&volumeBuffer[0], uint32(len(volumeBuffer)))
	if err != nil {
		return nil, err
	}
	defer windows.FindVolumeClose(handle)

	parts := make([]disk.PartitionStat, 0)
	for {
		volumeName := windows.UTF16ToString(volumeBuffer)
		volumePath, pathErr := windows.UTF16PtrFromString(volumeName)
		if pathErr == nil && windows.GetDriveType(volumePath) == windows.DRIVE_FIXED {
			for _, mountPoint := range volumeMountPoints(volumePath) {
				parts = append(parts, disk.PartitionStat{
					Device:     volumeName,
					Mountpoint: mountPoint,
					Fstype:     "windows-fixed",
				})
			}
		}

		clear(volumeBuffer)
		err = windows.FindNextVolume(handle, &volumeBuffer[0], uint32(len(volumeBuffer)))
		if errors.Is(err, windows.ERROR_NO_MORE_FILES) {
			break
		}
		if err != nil {
			return parts, err
		}
	}
	return parts, nil
}

func volumeMountPoints(volumeName *uint16) []string {
	buffer := make([]uint16, 512)
	var required uint32
	err := windows.GetVolumePathNamesForVolumeName(
		volumeName,
		&buffer[0],
		uint32(len(buffer)),
		&required,
	)
	if errors.Is(err, windows.ERROR_MORE_DATA) && required > uint32(len(buffer)) {
		buffer = make([]uint16, required)
		err = windows.GetVolumePathNamesForVolumeName(
			volumeName,
			&buffer[0],
			uint32(len(buffer)),
			&required,
		)
	}
	if err != nil {
		return nil
	}

	paths := make([]string, 0)
	for start := 0; start < len(buffer) && buffer[start] != 0; {
		end := start
		for end < len(buffer) && buffer[end] != 0 {
			end++
		}
		if path := windows.UTF16ToString(buffer[start:end]); path != "" {
			paths = append(paths, path)
		}
		start = end + 1
	}
	return paths
}

func isRemoteMount(mountPoint string) bool {
	path, err := windows.UTF16PtrFromString(mountPoint)
	if err != nil {
		return false
	}
	return windows.GetDriveType(path) == windows.DRIVE_REMOTE
}

func remoteStorageDevice(mountPoint string, device string) string {
	localName := strings.TrimSpace(mountPoint)
	if len(localName) < 2 || localName[1] != ':' {
		return device
	}
	localName = localName[:2]
	local, err := windows.UTF16PtrFromString(localName)
	if err != nil {
		return device
	}
	for _, size := range []uint32{512, 32768} {
		buffer := make([]uint16, size)
		length := size
		result, _, _ := wNetGetConnection.Call(
			uintptr(unsafe.Pointer(local)),
			uintptr(unsafe.Pointer(&buffer[0])),
			uintptr(unsafe.Pointer(&length)),
		)
		if result == uintptr(windows.ERROR_SUCCESS) {
			if remote := strings.TrimSpace(windows.UTF16ToString(buffer)); remote != "" {
				return remote
			}
			return device
		}
		if result != uintptr(windows.ERROR_MORE_DATA) {
			return device
		}
	}
	return device
}

func localStorageIdentity(_ string, mountPoint string, device string) string {
	clean := strings.TrimSpace(strings.ReplaceAll(mountPoint, "/", `\`))
	if clean == "" {
		return device
	}
	if !strings.HasSuffix(clean, `\`) {
		clean += `\`
	}
	mount, err := windows.UTF16PtrFromString(clean)
	if err != nil {
		return device
	}
	buffer := make([]uint16, 50)
	if err := windows.GetVolumeNameForVolumeMountPoint(mount, &buffer[0], uint32(len(buffer))); err != nil {
		return device
	}
	if volume := strings.TrimSpace(windows.UTF16ToString(buffer)); volume != "" {
		return volume
	}
	return device
}
