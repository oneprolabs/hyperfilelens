//go:build windows

package explorer

import (
	"path/filepath"
	"unicode/utf16"

	"golang.org/x/sys/windows"
)

func mountPointAllowed(mountpoint string, localFixedOnly bool) bool {
	if !localFixedOnly {
		return true
	}
	volume := filepath.VolumeName(mountpoint)
	if len(volume) != 2 || volume[1] != ':' {
		return false
	}
	root, err := windows.UTF16PtrFromString(volume + `\`)
	return err == nil && windows.GetDriveType(root) == windows.DRIVE_FIXED
}

func extraMountPoints(seen map[string]struct{}) []string {
	buf := make([]uint16, 256)
	n, err := windows.GetLogicalDriveStrings(uint32(len(buf)), &buf[0])
	if err != nil || n == 0 {
		return nil
	}
	out := make([]string, 0, 8)
	for i := 0; i < int(n); {
		if buf[i] == 0 {
			break
		}
		j := i
		for j < int(n) && buf[j] != 0 {
			j++
		}
		raw := normalizeMountPath(string(utf16.Decode(buf[i:j])))
		i = j + 1
		if raw == "" {
			continue
		}
		key := mountKey(raw)
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		out = append(out, raw)
	}
	return out
}
