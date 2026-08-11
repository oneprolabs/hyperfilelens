package disk

import (
	"path/filepath"
	"runtime"
	"strings"

	gopsutildisk "github.com/shirou/gopsutil/v4/disk"
)

// PathUsage describes both the filesystem metrics and the stable storage pool
// that backs a path. PoolKey is intended for control-plane de-duplication; it
// is not a user-facing label.
type PathUsage struct {
	Total      uint64
	Used       uint64
	Free       uint64
	PoolKey    string
	MountPoint string
	Device     string
}

// Inspect returns filesystem usage together with the best matching mount.
func Inspect(path string) (PathUsage, error) {
	total, used, free, err := Usage(path)
	if err != nil {
		return PathUsage{}, err
	}

	info := PathUsage{Total: total, Used: used, Free: free}
	parts, partsErr := gopsutildisk.Partitions(true)
	if partsErr != nil {
		return info, nil
	}

	cleanPath := comparablePath(path)
	bestLength := -1
	for _, part := range parts {
		mountPoint := comparablePath(part.Mountpoint)
		if mountPoint == "" || !pathWithinMount(cleanPath, mountPoint) {
			continue
		}
		if len(mountPoint) <= bestLength {
			continue
		}
		bestLength = len(mountPoint)
		info.MountPoint = normalizeMountpoint(part.Mountpoint)
		info.Device = strings.TrimSpace(part.Device)
	}

	info.PoolKey = storagePoolKey(info.Device, info.MountPoint)
	return info, nil
}

func storagePoolKey(device string, mountPoint string) string {
	device = strings.TrimSpace(device)
	if runtime.GOOS == "windows" {
		device = strings.ToLower(device)
	}
	if device != "" {
		return "device:" + device
	}
	mountPoint = comparablePath(mountPoint)
	if mountPoint != "" {
		return "mount:" + mountPoint
	}
	return ""
}

func comparablePath(path string) string {
	clean := strings.TrimSpace(path)
	if clean == "" {
		return ""
	}
	if resolved, err := filepath.EvalSymlinks(clean); err == nil {
		clean = resolved
	}
	if absolute, err := filepath.Abs(clean); err == nil {
		clean = absolute
	}
	clean = filepath.Clean(clean)
	if runtime.GOOS == "windows" {
		clean = strings.ToLower(strings.ReplaceAll(clean, "/", `\`))
	}
	return clean
}

func pathWithinMount(path string, mountPoint string) bool {
	if path == "" || mountPoint == "" {
		return false
	}
	if path == mountPoint {
		return true
	}
	relative, err := filepath.Rel(mountPoint, path)
	if err != nil || relative == "." {
		return err == nil
	}
	return relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}
